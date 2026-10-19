"""Scenario files: the deterministic half of a flow, pinned down in CI.

A scenario is a scripted conversation — user turns plus the interpretation the
LLM *would* have produced for each — replayed against ``TestHost``. No model
runs, so a scenario is fast, offline, and repeatable. That is exactly the
claim the product makes: the model only interprets, the runtime decides. A
scenario fixes the interpretation and asserts on what the runtime did with it.

Scenarios live in ``scenarios.json`` beside SKILL.md so they are diffable and
reviewable in a pull request like the flow itself, and so the revision store
snapshots them alongside the skill.

File shape::

    {"scenarios": [
      {"id": "happy-path",
       "name": "Books redwood on the first try",
       "turns": [{"user": "book redwood at 3pm",
                  "emits": [{"type": "SetField", "field": "room", "value": "redwood"}]}],
       "expect": {"fields": {"room": "redwood"}, "done": true}}
    ]}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import trace as _trace

FILENAME = "scenarios.json"


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #

def path_for(skill_folder: Path) -> Path:
    return Path(skill_folder) / FILENAME


def load(skill_folder: Path) -> list[dict[str, Any]]:
    """Read a skill's scenarios. Missing or malformed file -> no scenarios."""
    p = path_for(skill_folder)
    if not p.exists():
        return []
    try:
        body = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = body.get("scenarios") if isinstance(body, dict) else body
    return [s for s in (items or []) if isinstance(s, dict)]


def save(skill_folder: Path, scenarios: list[dict[str, Any]]) -> None:
    """Write scenarios back, or remove the file when the list is empty."""
    p = path_for(skill_folder)
    if not scenarios:
        p.unlink(missing_ok=True)
        return
    p.write_text(
        json.dumps({"scenarios": scenarios}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------------------- #
# running
# --------------------------------------------------------------------------- #

def _to_turn(raw: dict[str, Any]) -> Any:
    """Rehydrate one serialized interpretation command into its Turn model."""
    from .interpret import Cancel, Chitchat, Correct, OutOfScope, Reply, SetField, StartFlow

    kinds = {
        "SetField": SetField, "Correct": Correct, "StartFlow": StartFlow,
        "Cancel": Cancel, "OutOfScope": OutOfScope, "Chitchat": Chitchat, "Reply": Reply,
    }
    kind = kinds.get(str(raw.get("type")))
    if kind is None:
        raise ValueError(f"unknown interpretation command {raw.get('type')!r}")
    return kind.model_validate(raw)


def _check(expect: dict[str, Any], state: dict[str, Any], messages: list[str]) -> list[str]:
    """Compare a finished run against a scenario's expectations.

    Returns every mismatch rather than the first: one run should tell you
    everything that drifted, not send you round the loop per assertion.
    """
    failures: list[str] = []

    for key, want in (expect.get("fields") or {}).items():
        got = (state.get("fields") or {}).get(key)
        if got != want:
            failures.append(f"field {key!r}: expected {want!r}, got {got!r}")

    for key, want in (expect.get("results") or {}).items():
        got = (state.get("results") or {}).get(key)
        if got != want:
            failures.append(f"result {key!r}: expected {want!r}, got {got!r}")

    if "awaiting_field" in expect:
        want, got = expect["awaiting_field"], state.get("awaiting_field")
        if got != want:
            failures.append(f"awaiting_field: expected {want!r}, got {got!r}")

    if "done" in expect:
        want, got = bool(expect["done"]), bool(state.get("done"))
        if got != want:
            failures.append(f"done: expected {want}, got {got}")

    needle = expect.get("last_assistant_contains")
    if needle:
        last = messages[-1] if messages else ""
        if needle not in last:
            failures.append(f"last reply did not contain {needle!r} (got {last!r})")

    return failures


def _run_once(agent: Any, skill: str, scenario: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Replay one scenario on a fresh state. Returns (state, messages, trace)."""
    from .runtime import initial_state

    host = agent.host
    turns = scenario.get("turns") or []
    for t in turns:
        emits = [_to_turn(e) for e in (t.get("emits") or [])]
        host.queue_commands(t.get("user", ""), *emits)

    state: Any = initial_state()
    state["flow_id"] = skill

    messages: list[str] = []
    with _trace.recording() as entries:
        for t in turns:
            state["pending_user_message"] = t.get("user", "")
            state = agent.turn(state)
            messages.extend(host.drain_pending_messages())
    return state, messages, entries


def run_all(
    skills_dir: Path | str,
    skill: str,
    *,
    tools: Any = None,
    mode: str = "flex",
) -> dict[str, Any]:
    """Replay every scenario for one skill and report what the runtime did.

    Each scenario runs **twice** on independent state. The two traces must
    reduce to the same signature — that is the determinism property, checked
    rather than asserted. A mismatch is reported as a failure even when the
    expectations pass, because a flow that takes a different path on identical
    input is broken regardless of where it lands.

    Uses its own agent bound to ``TestHost``, never the live one: no model is
    called and the running agent's registry is untouched.
    """
    from .agent import FlowAgent
    from .hosts.test_host import TestHost

    folder = Path(skills_dir) / skill
    specs = load(folder)
    if not specs:
        return {"skill": skill, "results": [], "passed": 0, "failed": 0, "total": 0}

    results: list[dict[str, Any]] = []
    for spec in specs:
        entry: dict[str, Any] = {
            "id": spec.get("id") or spec.get("name") or "scenario",
            "name": spec.get("name") or spec.get("id") or "scenario",
        }
        try:
            # A fresh agent per scenario keeps one scenario's scripted host and
            # hot-loaded registry from leaking into the next.
            agent = FlowAgent.from_dir(skills_dir, host=TestHost(), mode=mode, tools=tools)
            state, messages, first = _run_once(agent, skill, spec)
            failures = _check(spec.get("expect") or {}, state, messages)

            agent2 = FlowAgent.from_dir(skills_dir, host=TestHost(), mode=mode, tools=tools)
            _, _, second = _run_once(agent2, skill, spec)

            sig_a, sig_b = _trace.signature(first), _trace.signature(second)
            deterministic = sig_a == sig_b
            if not deterministic:
                failures.append(
                    "non-deterministic: the same interpretation took two different paths"
                )

            entry.update(
                status="passed" if not failures else "failed",
                failures=failures,
                trace=first,
                deterministic=deterministic,
                fields=state.get("fields") or {},
                messages=messages,
            )
        except Exception as exc:  # noqa: BLE001 — a broken scenario is a failure, not a 500
            entry.update(
                status="error",
                failures=[f"{type(exc).__name__}: {exc}"],
                trace=[],
                deterministic=False,
            )
        results.append(entry)

    return {
        "skill": skill,
        "results": results,
        "total": len(results),
        "passed": sum(1 for r in results if r["status"] == "passed"),
        "failed": sum(1 for r in results if r["status"] != "passed"),
    }

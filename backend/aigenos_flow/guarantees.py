"""What the fail-closed loader proved about a skill.

The loader already checks all of this on every save — it just throws the
result away except when something fails. This module re-derives the same
picture for a skill that *did* load, so the Studio can show the passing side:
which guarantees are actively doing work for this flow, and which have nothing
to bite on yet.

Nothing here re-implements a check. A skill only reaches this code by having
survived ``load_skills_dir``, so every load-time property below is true by
construction; what we compute is how much of the flow each one covers. The
honest consequence is that a property with no applicable constructs reports
``n/a`` rather than a green tick — an unused guarantee is not a passing one.

Cross-references (the enforcement, not the reporting):
  * names resolve ............ skills.py `_check_bound` / `_check_step_bound`
  * free text vs tool args ... skills.py, action arg loop
  * gated actions ............ skills.py, `do:` step loop
  * managed writes ........... skills.py, managed descriptor loop
  * gate recovery ............ skills.py, `check:` step validation
  * chat leaf ................ skills.py, `chat:` step validation
  * acyclic compute .......... declarative.py `topo_sort_compute`
  * at-most-once effects ..... declarative.py `ctx.run` + `effects_log`
"""
from __future__ import annotations

import re
from typing import Any

Status = str  # "holds" | "n/a"

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def frontmatter_of(text: str) -> dict[str, Any]:
    """Parse a SKILL.md's YAML frontmatter. Unparseable or absent -> ``{}``."""
    import yaml

    m = _FM_RE.match(text)
    if not m:
        return {}
    try:
        fm = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return fm if isinstance(fm, dict) else {}


def analyze_markdown(text: str) -> dict[str, Any]:
    """Analyze a SKILL.md's YAML frontmatter."""
    return analyze(frontmatter_of(text))


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def analyze(frontmatter: dict[str, Any]) -> dict[str, Any]:
    """Describe the guarantees covering a loaded skill's frontmatter.

    Args:
        frontmatter: the parsed YAML frontmatter of a SKILL.md that has
            already loaded successfully.
    """
    fields: dict[str, Any] = frontmatter.get("fields") or {}
    compute: dict[str, Any] = frontmatter.get("compute") or {}
    actions: dict[str, Any] = frontmatter.get("actions") or {}
    flow: list[Any] = frontmatter.get("flow") or []
    steps = [s for s in flow if isinstance(s, dict)]

    checks = [s for s in steps if "check" in s]
    chats = [s for s in steps if "chat" in s]
    gated = {
        name: spec for name, spec in actions.items()
        if isinstance(spec, dict) and spec.get("requires")
    }
    audited = [
        name for name, spec in actions.items()
        if isinstance(spec, dict) and spec.get("audit")
    ]
    idempotent = [
        name for name, spec in actions.items()
        if isinstance(spec, dict) and spec.get("idempotency_key")
    ]
    # An action arg that binds a whole field, where that field is a string.
    constrained_strings = [
        name for name, spec in fields.items()
        if isinstance(spec, dict)
        and spec.get("type") == "string"
        and (spec.get("pattern") or spec.get("values"))
    ]

    props: list[dict[str, Any]] = []

    def add(pid: str, title: str, status: Status, detail: str, kind: str = "load") -> None:
        props.append({"id": pid, "title": title, "status": status, "detail": detail, "kind": kind})

    expr_count = len(compute) + sum(
        1 for s in steps if s.get("when") or s.get("when_missing") or s.get("check")
    )
    add(
        "names-resolve",
        "Every name resolves",
        "holds" if (expr_count or fields) else "n/a",
        (
            f"{_plural(expr_count, 'expression')} and every binding reference a declared "
            f"field, a compute, or a prior action result."
            if expr_count
            else "No expressions or bindings to resolve yet."
        ),
    )

    add(
        "no-free-text-to-tools",
        "Free text never reaches a tool argument",
        "holds" if actions else "n/a",
        (
            f"{_plural(len(actions), 'action')} bind only typed values; any string field "
            f"reaching a tool carries a pattern or enum "
            f"({_plural(len(constrained_strings), 'constrained string field')})."
            if actions
            else "No actions declared — nothing calls a tool."
        ),
    )

    add(
        "gated-effects",
        "Side effects run only behind their gate",
        "holds" if gated else "n/a",
        (
            f"{_plural(len(gated), 'action')} require confirmation "
            f"({', '.join(sorted(gated))}); each is reachable only from a step guarded by "
            f"a `when:` naming that field."
            if gated
            else "No action declares `requires:` — nothing is gated."
        ),
    )

    add(
        "audited-writes",
        "Writes are audited and idempotent",
        "holds" if (audited or idempotent) else "n/a",
        (
            f"{_plural(len(audited), 'action')} audited"
            + (f", {_plural(len(idempotent), 'action')} keyed for idempotency" if idempotent else "")
            + "."
            if (audited or idempotent)
            else "No audited or idempotency-keyed actions declared."
        ),
    )

    add(
        "gate-recovery",
        "Every gate has a recovery path",
        "holds" if checks else "n/a",
        (
            f"{_plural(len(checks), 'check')} each declare `else_clear:` — on failure the "
            f"named fields are re-collected rather than the run dead-ending."
            if checks
            else "No `check:` gates in this flow."
        ),
    )

    add(
        "chat-leaf",
        "Chat is a terminal, read-only leaf",
        "holds" if chats else "n/a",
        (
            "The `chat:` step is last, and its tools are allow-listed, registered, and "
            "non-side-effecting."
            if chats
            else "No `chat:` step in this flow."
        ),
    )

    add(
        "acyclic-compute",
        "Computed values are acyclic",
        "holds" if compute else "n/a",
        (
            f"{_plural(len(compute), 'computed value')} topologically ordered; no cycles or "
            f"forward references."
            if compute
            else "No `compute:` block."
        ),
    )

    add(
        "at-most-once",
        "Gated writes fire at most once per run",
        "holds" if gated else "n/a",
        (
            f"{_plural(len(gated), 'gated write')} dispatch through `ctx.run` and are "
            "recorded in a sticky effects log keyed on a stable flow_run_id, so a replay, "
            "a correction, a digression or a flow switch never re-fires one. Read-only "
            "actions are deliberately NOT deduped this way — a correction re-runs them "
            "against the new value."
            if gated
            else "No action declares `requires:` — there is no gated write to dedupe."
        ),
        kind="runtime",
    )

    return {
        "properties": props,
        "holds": sum(1 for p in props if p["status"] == "holds"),
        "checked": len(props),
    }


# --------------------------------------------------------------------------- a skill written as steps
def _flat(steps: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        if isinstance(step.get("steps"), list):
            out.append({"when": step.get("when")})           # the block's own condition, counted once
            out.extend(_flat(step["steps"]))
        else:
            out.append(step)
    return out


def _listed(names: list[str], limit: int = 4) -> str:
    shown = ", ".join(f"`{n}`" for n in names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def analyze_steps(frontmatter: dict[str, Any], skill: Any, page: dict[str, Any]) -> dict[str, Any]:
    """The same report for a skill written as ``steps:``.

    ``skill`` is the loaded skill and ``page`` its procedure
    (``procedure.for_skill``): the tools that change something and what each
    waits for come from there, so this reads what the loader accepted and does
    not re-derive it.
    """
    steps = _flat(frontmatter.get("steps"))
    conditions = sum(1 for st in steps if st.get("when")) + sum(
        len(st["check"]) for st in steps if isinstance(st.get("check"), dict)
    )
    rules = sum(len(st["check"]) for st in steps if isinstance(st.get("check"), dict))
    filled = sum(
        len(re.findall(r"\{\{[^}]+\}\}", str(st.get(key))))
        for st in steps for key in ("say", "title", "with") if st.get(key) is not None
    )
    others = sorted({str(st["by"]) for st in steps if "confirm" in st and st.get("by")})
    writes = [w for w in page.get("writes") or [] if isinstance(w, dict)]
    write_names = [str(w.get("tool")) for w in writes]
    lookups = [str(t.get("name")) for t in page.get("lookups") or [] if isinstance(t, dict)]
    fields = getattr(skill, "fields", None) or {}
    theirs = sorted(
        {n for n, spec in fields.items() if getattr(spec, "must_elicit", False) or getattr(spec, "sensitive", False)}
        | {n for n in (getattr(skill, "confirmation_fields", None) or []) if n in fields}
        | {n for n in (getattr(skill, "picked_fields", None) or []) if n in fields}
    )
    rows = (page.get("paths") or {}).get("rows") or []
    ways = f"on each of the {len(rows)} ways through the flow" if len(rows) > 1 else "on the way through the flow"

    props: list[dict[str, Any]] = []

    def add(pid: str, title: str, holds: bool, detail: str, idle: str, kind: str = "load") -> None:
        props.append({"id": pid, "title": title, "status": "holds" if holds else "n/a",
                      "detail": detail if holds else idle, "kind": kind})

    add(
        "names-resolve", "Every name resolves", bool(conditions or filled),
        f"{_plural(conditions, 'condition')} and {_plural(filled, 'filled-in name')} each read an answer "
        "asked above them or a tool's result. A name nothing provides is a load error.",
        "No conditions or filled-in names to resolve yet.",
    )
    add(
        "typed-inputs", "A tool gets its own inputs, typed, and nothing else", bool(write_names or lookups),
        f"{_plural(len(write_names) + len(lookups), 'tool')}: each receives the answers named after its "
        "inputs, with the type its signature gives them, and every required input is asked above the "
        "step that uses it.",
        "No step names a tool yet.",
    )
    add(
        "gated-effects", "A tool that changes something waits for a yes", bool(writes),
        f"{_listed(write_names)} cannot run before a `confirm:` above "
        f"{'it' if len(write_names) == 1 else 'them'} is answered yes, {ways}. "
        f"{_listed(lookups) if lookups else 'No tool'} only look"
        f"{'s' if len(lookups) == 1 else ''} things up, by "
        f"{'its' if len(lookups) == 1 else 'their'} own statement (`@readonly`)."
        if lookups else
        f"{_listed(write_names)} cannot run before a `confirm:` above "
        f"{'it' if len(write_names) == 1 else 'them'} is answered yes, {ways}.",
        "No step runs a tool that changes something.",
    )
    add(
        "approved-is-run", "What was approved is what runs", bool(writes),
        "The confirmation shows each tool a yes will run and its inputs. They are compared again when "
        "the tool is called: if one differs, nothing runs and the yes is asked for again.",
        "No tool waits for a confirmation.", kind="runtime",
    )
    add(
        "user-only", "An answer that decides something is the user's own", bool(theirs),
        f"{_plural(len(theirs), 'answer')} ({_listed(theirs)}) can only come from the user: a value the "
        "user did not type or pick is dropped, whatever the model proposes.",
        "No answer decides a condition, is a secret, a pick or a confirmation.",
    )
    add(
        "gate-recovery", "A refused answer is asked again", bool(rules),
        f"{_plural(rules, 'rule')}: an answer that fails one is cleared and its question asked again "
        "with the sentence the skill gives, rather than the run dead-ending.",
        "No `check:` rules in these steps.",
    )
    add(
        "others-yes", "Someone else's yes cannot be given in the chat", bool(others),
        f"A yes is asked of {', '.join(others)}. It reaches the flow only through the approvals store: "
        "sent through the conversation, by the user, by the model or by a posted form, it is refused.",
        "No confirmation is asked of someone else (`by:`).", kind="runtime",
    )
    chats = [st for st in steps if "chat" in st]
    chat_tools = sorted({
        str(t) for st in chats
        for t in (st.get("tools") if isinstance(st.get("tools"), list)
                  else str(st.get("tools") or "").replace(",", " ").split())
    })
    add(
        "chat-reads-only", "Open questions are answered with lookups only", bool(chats),
        f"The closing chat may call only {_listed(chat_tools)}, by "
        f"{'its' if len(chat_tools) == 1 else 'their'} own statement (`@readonly`), and a tool "
        "input that has an answer of the same name is fixed to it. Nothing can change there.",
        "No closing `chat:` step.",
    )
    add(
        "at-most-once", "A tool that changes something runs at most once", bool(writes),
        f"{_listed(write_names)} {'is' if len(write_names) == 1 else 'are'} recorded in an effects log "
        "keyed on the run, so a replay, a correction or a flow switch never runs one twice, and each "
        "run is on the audit trail with secrets redacted. Lookups are deliberately not deduped: a "
        "correction runs them again against the new value.",
        "No tool that changes something to dedupe.", kind="runtime",
    )
    return {
        "properties": props,
        "holds": sum(1 for p in props if p["status"] == "holds"),
        "checked": len(props),
    }

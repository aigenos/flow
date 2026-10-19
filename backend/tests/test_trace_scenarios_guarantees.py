"""Trace recording, scenario replay, and the guarantees report.

These three together are the product's determinism claim made inspectable:
the trace says which step ran and why, the scenario replays it without a
model, and the guarantees list what the loader proved.
"""
from __future__ import annotations

import json
import textwrap

import pytest

from aigenos_flow import FlowAgent, ToolRegistry, initial_state, trace
from aigenos_flow import guarantees, scenarios
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.skills import SkillRegistry, load_skill

GATED = """\
---
name: transfer-funds
description: Send money. Transfers of 1000 or more require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:
    type: number
    min: 0.01
    max: 25000
    must_elicit: true
  recipient:
    type: enum
    values: [self, mom, landlord]
  verification_code:
    type: string
    required: false
    pattern: '^[0-9]{6}$'
    sensitive: true
  confirm:
    type: boolean
compute:
  requires_2fa: "amount >= 1000"
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args:
      amount: "{{amount}}"
      payee: "{{recipient}}"
flow:
  - ask: amount
  - ask: recipient
  - when: "requires_2fa"
    ask: verification_code
  - say: "Send {{amount}} to {{recipient}}?"
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Body context for the interpreter.
"""


def _write(tmp_path, md, name="transfer-funds"):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(md, encoding="utf-8")
    return d


def _registry():
    calls: list[dict] = []
    reg = ToolRegistry()

    def bank_transfer(args):
        calls.append(dict(args))
        return {"id": f"TXN-{len(calls)}"}

    reg.register("bank_transfer", bank_transfer)
    return reg, calls


def _agent(skill_dir, reg):
    sr = SkillRegistry()
    sr.register(load_skill(skill_dir, tool_registry=reg))
    return FlowAgent(sr, TestHost(), mode="flex")


def _fresh():
    s = initial_state()
    s["flow_id"] = "transfer-funds"
    return s


def _turn(agent, st, **updates):
    st["pending_field_updates"] = dict(updates)
    return agent.turn(st)


# --------------------------------------------------------------------------- #
# trace
# --------------------------------------------------------------------------- #

def test_trace_is_off_by_default(tmp_path):
    """No recorder — emit is a no-op and nothing accumulates anywhere."""
    reg, _ = _registry()
    agent = _agent(_write(tmp_path, GATED), reg)
    assert not trace.active()
    _turn(agent, _fresh(), amount=10, recipient="mom")
    assert not trace.active()


def test_trace_records_guard_skips_with_the_reason(tmp_path):
    reg, _ = _registry()
    agent = _agent(_write(tmp_path, GATED), reg)

    with trace.recording() as entries:
        # Under 1000, so the 2FA ask must skip on its `when:` guard.
        _turn(agent, _fresh(), amount=10, recipient="mom")

    skipped = [e for e in entries if e["outcome"] == "skipped"]
    assert any(e["guard"] == "when" and "requires_2fa" in e["detail"] for e in skipped)
    # ...and the step that skipped is the verification_code ask (index 2).
    assert {e["step"] for e in skipped} >= {2}


def test_trace_distinguishes_paused_from_ran(tmp_path):
    """An `ask` for a field we don't have yet pauses; it did not complete."""
    reg, _ = _registry()
    agent = _agent(_write(tmp_path, GATED), reg)

    with trace.recording() as entries:
        _turn(agent, _fresh())  # no fields supplied at all

    first = entries[0]
    assert first == {"step": 0, "verb": "ask", "outcome": "paused", "detail": "amount"}


def test_trace_signature_ignores_detail_but_not_order():
    a = [{"step": 0, "verb": "ask", "outcome": "ran", "detail": "x"}]
    b = [{"step": 0, "verb": "ask", "outcome": "ran", "detail": "totally different"}]
    assert trace.signature(a) == trace.signature(b)

    reordered = [
        {"step": 1, "verb": "say", "outcome": "ran"},
        {"step": 0, "verb": "ask", "outcome": "ran"},
    ]
    forward = list(reversed(reordered))
    assert trace.signature(reordered) != trace.signature(forward)


def test_trace_is_isolated_between_recorders(tmp_path):
    reg, _ = _registry()
    agent = _agent(_write(tmp_path, GATED), reg)

    with trace.recording() as outer:
        _turn(agent, _fresh(), amount=10, recipient="mom")
        with trace.recording() as inner:
            _turn(agent, _fresh(), amount=20, recipient="self")
        assert inner  # the inner run went to the inner sink
    # ...and did not also land in the outer one.
    assert all(e.get("detail") != "self" for e in outer)


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #

def test_scenarios_roundtrip_and_delete(tmp_path):
    folder = _write(tmp_path, GATED)
    assert scenarios.load(folder) == []

    spec = [{"id": "s1", "name": "one", "turns": [], "expect": {}}]
    scenarios.save(folder, spec)
    assert scenarios.load(folder) == spec
    assert scenarios.path_for(folder).exists()

    scenarios.save(folder, [])  # empty list removes the file entirely
    assert not scenarios.path_for(folder).exists()
    assert scenarios.load(folder) == []


def test_malformed_scenarios_file_is_ignored_not_fatal(tmp_path):
    folder = _write(tmp_path, GATED)
    scenarios.path_for(folder).write_text("{not json", encoding="utf-8")
    assert scenarios.load(folder) == []


def test_scenario_passes_and_reports_determinism(tmp_path):
    """The gate holds under the threshold, and both replays take one path."""
    reg, _ = _registry()
    _write(tmp_path, GATED)
    scenarios.save(tmp_path / "transfer-funds", [{
        "id": "under-threshold",
        "name": "No 2FA below 1000",
        "turns": [{
            "user": "send 10 to mom",
            "emits": [
                {"type": "SetField", "field": "amount", "value": 10},
                {"type": "SetField", "field": "recipient", "value": "mom"},
            ],
        }],
        "expect": {"awaiting_field": "confirm"},
    }])

    out = scenarios.run_all(tmp_path, "transfer-funds", tools=reg)
    assert out["passed"] == 1, out["results"][0]["failures"]
    result = out["results"][0]
    assert result["deterministic"] is True
    # The 2FA ask skipped on its guard rather than running.
    assert any(e["outcome"] == "skipped" and e["guard"] == "when" for e in result["trace"])


def test_scenario_failure_lists_every_mismatch_not_just_the_first(tmp_path):
    reg, _ = _registry()
    _write(tmp_path, GATED)
    scenarios.save(tmp_path / "transfer-funds", [{
        "id": "wrong",
        "turns": [{
            "user": "send 10 to mom",
            "emits": [{"type": "SetField", "field": "amount", "value": 10}],
        }],
        "expect": {"fields": {"amount": 999, "recipient": "landlord"}, "done": True},
    }])

    out = scenarios.run_all(tmp_path, "transfer-funds", tools=reg)
    assert out["failed"] == 1
    failures = out["results"][0]["failures"]
    assert len(failures) == 3  # amount, recipient, done — all reported together


def test_unknown_command_type_is_a_scenario_failure_not_a_crash(tmp_path):
    reg, _ = _registry()
    _write(tmp_path, GATED)
    scenarios.save(tmp_path / "transfer-funds", [{
        "id": "bogus",
        "turns": [{"user": "hi", "emits": [{"type": "Nonsense"}]}],
        "expect": {},
    }])

    out = scenarios.run_all(tmp_path, "transfer-funds", tools=reg)
    assert out["results"][0]["status"] == "error"
    assert "Nonsense" in out["results"][0]["failures"][0]


def test_no_scenarios_reports_an_empty_run(tmp_path):
    reg, _ = _registry()
    _write(tmp_path, GATED)
    out = scenarios.run_all(tmp_path, "transfer-funds", tools=reg)
    assert out == {"skill": "transfer-funds", "results": [], "passed": 0, "failed": 0, "total": 0}


# --------------------------------------------------------------------------- #
# guarantees
# --------------------------------------------------------------------------- #

def test_guarantees_report_the_gated_action(tmp_path):
    report = guarantees.analyze_markdown(GATED)
    by_id = {p["id"]: p for p in report["properties"]}

    assert by_id["gated-effects"]["status"] == "holds"
    assert "submit_transfer" in by_id["gated-effects"]["detail"]
    assert by_id["acyclic-compute"]["status"] == "holds"   # requires_2fa
    assert by_id["at-most-once"]["kind"] == "runtime"      # honest about which layer
    assert report["holds"] <= report["checked"]


def test_unused_guarantees_report_na_rather_than_a_green_tick():
    """A property with nothing to bite on is not a passing property."""
    minimal = textwrap.dedent("""\
        ---
        name: tiny
        description: Say hello.
        fields:
          who:
            type: string
            pattern: '^[a-z]{1,20}$'
        flow:
          - ask: who
          - say: "hi {{who}}"
        ---
        body
        """)
    by_id = {p["id"]: p for p in guarantees.analyze_markdown(minimal)["properties"]}
    assert by_id["gated-effects"]["status"] == "n/a"
    assert by_id["chat-leaf"]["status"] == "n/a"
    assert by_id["gate-recovery"]["status"] == "n/a"
    assert by_id["no-free-text-to-tools"]["status"] == "n/a"


@pytest.mark.parametrize("text", ["", "no frontmatter here", "---\n: : bad yaml :\n---\n"])
def test_guarantees_never_raises_on_unparseable_input(text):
    report = guarantees.analyze_markdown(text)
    assert report["checked"] >= 0
    assert isinstance(report["properties"], list)

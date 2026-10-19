"""Declarative skills: dual-mode loading + the safety guards an adversarial
design panel demanded. Every gate-bypass / double-spend / unsafe-authoring case
is a LOAD ERROR or is structurally impossible at runtime.
"""
from __future__ import annotations

import textwrap

import pytest

from aigenos_flow import FlowAgent, ToolRegistry, initial_state
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.skills import SkillRegistry, load_skill

CANONICAL = """\
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
  from_account:
    type: enum
    values: [checking, savings]
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
      otp: "{{verification_code}}"
flow:
  - ask: amount
  - ask: from_account
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


def _write(tmp_path, md, name="transfer-funds", handlers=None):
    d = tmp_path / name
    d.mkdir()
    (d / "SKILL.md").write_text(md, encoding="utf-8")
    if handlers is not None:
        (d / "handlers.py").write_text(textwrap.dedent(handlers), encoding="utf-8")
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
# Behavior: a pure declarative skill (no handlers.py) enforces the gate
# --------------------------------------------------------------------------- #
def test_declarative_gate_holds_and_fires_with_code(tmp_path):
    reg, calls = _registry()
    agent = _agent(_write(tmp_path, CANONICAL), reg)

    st = _fresh()
    st = _turn(agent, st, amount=5000, from_account="checking", recipient="mom")
    assert st["awaiting_field"] == "verification_code"  # 2FA gate engaged
    st = _turn(agent, st, confirm=True)                 # confirm without a code
    assert calls == []                                  # nothing fired
    st = _turn(agent, st, verification_code="481920")
    assert st["awaiting_field"] == "confirm" and calls == []   # the early yes was not kept
    st = _turn(agent, st, confirm=True)
    assert len(calls) == 1
    assert calls[0] == {"amount": 5000.0, "payee": "mom", "otp": "481920"}  # typed args


def test_under_threshold_skips_gate_and_omits_the_unset_arg(tmp_path):
    reg, calls = _registry()
    agent = _agent(_write(tmp_path, CANONICAL), reg)

    st = _fresh()
    st = _turn(agent, st, amount=500, from_account="checking", recipient="mom")
    assert st["awaiting_field"] == "confirm"             # no 2FA step
    st = _turn(agent, st, confirm=True)
    assert len(calls) == 1
    # A field the flow never asked for is OMITTED, so the tool's own default
    # applies. Binding it to None used to crash any tool whose signature types
    # the arg (audit wrapper-2) — and the README's own example is such a tool.
    assert "otp" not in calls[0]


def test_correction_rearms_gate_and_confirm_and_never_double_spends(tmp_path):
    reg, calls = _registry()
    agent = _agent(_write(tmp_path, CANONICAL), reg)

    st = _fresh()
    st = _turn(agent, st, amount=500, from_account="checking", recipient="mom")
    st = _turn(agent, st, confirm=True)                  # the $500 fires
    assert len(calls) == 1
    st = _turn(agent, st, amount=5000)                   # bump over the threshold
    assert st["awaiting_field"] == "verification_code"   # gate re-armed
    assert st["fields"].get("confirm") is None           # stale confirm reset
    st = _turn(agent, st, confirm=True)                  # try again, no code, idempotent
    assert len(calls) == 1                               # NO double-spend


# --------------------------------------------------------------------------- #
# Load-time safety guards — each unsafe skill fails LOUDLY before any turn
# --------------------------------------------------------------------------- #
def test_unbound_gate_name_is_load_error(tmp_path):
    md = CANONICAL.replace('requires_2fa: "amount >= 1000"', 'requires_2fa: "amont >= 1000"')
    with pytest.raises(ValueError, match="undeclared name"):
        load_skill(_write(tmp_path, md), tool_registry=_registry()[0])


def test_tool_not_allowlisted_is_load_error(tmp_path):
    md = CANONICAL.replace("allowed-tools: bank_transfer", "allowed-tools: something_else")
    with pytest.raises(ValueError, match="not in.*allowed-tools|allowed-tools"):
        load_skill(_write(tmp_path, md), tool_registry=_registry()[0])


def test_tool_not_registered_is_load_error(tmp_path):
    with pytest.raises(ValueError, match="not registered"):
        load_skill(_write(tmp_path, CANONICAL), tool_registry=ToolRegistry())


def test_compute_cycle_is_load_error(tmp_path):
    md = CANONICAL.replace(
        'compute:\n  requires_2fa: "amount >= 1000"',
        'compute:\n  a: "b"\n  b: "a"\n  requires_2fa: "amount >= 1000"',
    )
    with pytest.raises(ValueError, match="cyclic"):
        load_skill(_write(tmp_path, md), tool_registry=_registry()[0])


def test_ungated_effect_is_load_error(tmp_path):
    # `do: submit_transfer` not guarded by a `when:` referencing `confirm`
    md = CANONICAL.replace('  - when: "confirm"\n    do: submit_transfer', "  - do: submit_transfer")
    with pytest.raises(ValueError, match="not guarded"):
        load_skill(_write(tmp_path, md), tool_registry=_registry()[0])


def test_unconstrained_string_arg_is_load_error(tmp_path):
    # Drop the pattern from verification_code -> binding it into a tool arg is unsafe
    md = CANONICAL.replace("    pattern: '^[0-9]{6}$'\n", "")
    with pytest.raises(ValueError, match="unconstrained string"):
        load_skill(_write(tmp_path, md), tool_registry=_registry()[0])


def test_a_say_may_carry_a_guard(tmp_path):
    """`when:` works on every step kind. `say:` used to be the one exception."""
    md = CANONICAL.replace(
        '  - say: "Send {{amount}} to {{recipient}}?"',
        '  - when: "requires_2fa"\n    say: "Send {{amount}} to {{recipient}}?"',
    )
    assert load_skill(_write(tmp_path, md), tool_registry=_registry()[0]).name


def test_a_gate_input_is_user_only_without_being_marked(tmp_path):
    """The loader used to refuse this file and tell the author to add the mark.
    It knows which fields a gate reads, so it adds the mark itself."""
    md = CANONICAL.replace("    must_elicit: true\n", "")
    assert "must_elicit" not in md
    skill = load_skill(_write(tmp_path, md), tool_registry=_registry()[0])
    assert skill.fields["amount"].must_elicit is True


def test_flow_and_pyflow_is_ambiguous_load_error(tmp_path):
    handlers = """
        from aigenos_flow import flow
        @flow
        def transfer_funds(ctx):
            ctx.ask("amount")
    """
    with pytest.raises(ValueError, match="ambiguous"):
        load_skill(_write(tmp_path, CANONICAL, handlers=handlers), tool_registry=_registry()[0])


# --------------------------------------------------------------------------- #
# Hybrid escape hatch: declarative flow + a Python @action leaf
# --------------------------------------------------------------------------- #
def test_hybrid_pyaction_escape_hatch(tmp_path):
    md = CANONICAL.replace(
        "allowed-tools: bank_transfer\n", ""
    ).replace(
        "actions:\n"
        "  submit_transfer:\n"
        "    tool: bank_transfer\n"
        "    requires: confirm\n"
        '    idempotency_key: "{{flow_run_id}}"\n'
        "    args:\n"
        '      amount: "{{amount}}"\n'
        '      payee: "{{recipient}}"\n'
        '      otp: "{{verification_code}}"\n',
        "",
    )
    handlers = """
        from aigenos_flow import action
        FIRED = []
        @action(requires="confirm")
        def submit_transfer(state):
            FIRED.append(state["fields"].get("amount"))
            return {"id": "PY-1"}
    """
    skill = load_skill(_write(tmp_path, md, handlers=handlers), tool_registry=ToolRegistry())
    assert "submit_transfer" in skill.actions  # the @action is the do: target


# --------------------------------------------------------------------------- #
# Widget steps: multi-turn, heterogeneous, data-bound widgets that chain
# --------------------------------------------------------------------------- #
WIDGET_SKILL = """\
---
name: lookup
description: Search for a thing and act on the chosen one.
allowed-tools: search_things, act_thing
fields:
  q:
    type: string
    must_elicit: true
    pattern: '^[A-Za-z ]{1,40}$'
  picked_id:
    type: string
    pattern: '^[A-Za-z0-9_-]{1,40}$'
  confirm:
    type: boolean
actions:
  search_things:
    tool: search_things
    readonly: true
    audit: false
    args:
      q: "{{q}}"
  act_thing:
    tool: act_thing
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args:
      id: "{{picked_id}}"
flow:
  - ask: q
  - do: search_things
  - widget: picker
    data:
      options: "{{search_things.results}}"
    save: [picked_id]
  - ask: confirm
  - when: "confirm"
    do: act_thing
---
Body.
"""


def _wregistry():
    acted: list[dict] = []
    reg = ToolRegistry()
    reg.register("search_things", lambda a: {"results": [{"id": "X1"}, {"id": "X2"}]})
    reg.register("act_thing", lambda a: acted.append(dict(a)) or {"ok": True})
    return reg, acted


def test_widget_pipeline_binds_results_and_chains(tmp_path):
    reg, acted = _wregistry()
    sr = SkillRegistry()
    sr.register(load_skill(_write(tmp_path, WIDGET_SKILL, name="lookup"), tool_registry=reg))
    agent = FlowAgent(sr, TestHost(), mode="flex")

    st = initial_state()
    st["flow_id"] = "lookup"
    st["pending_field_updates"] = {"q": "william"}
    st = agent.turn(st)
    pw = st["pending_widget"]                       # a widget is pending, bound to results
    assert pw and pw["widget"] == "picker"
    assert pw["data"]["options"] == [{"id": "X1"}, {"id": "X2"}]
    assert pw["fields"] == ["picked_id"]
    assert acted == []                              # the action has NOT fired yet

    st["pending_field_updates"] = {"picked_id": "X1"}   # submit the widget
    st = agent.turn(st)
    # The picker is done. What is pending now is the confirmation, on a card
    # that says which action a yes will run and with what.
    assert st["pending_widget"]["widget"] == "confirm:confirm"
    assert acted == []                              # the write still waits for confirm

    st["pending_field_updates"] = {"confirm": True}
    st = agent.turn(st)
    assert st["done"]
    assert acted == [{"id": "X1"}]                  # chained: the pick drove the next tool


def test_widget_save_unknown_field_is_load_error(tmp_path):
    md = WIDGET_SKILL.replace("save: [picked_id]", "save: [nope]")
    with pytest.raises(ValueError, match="widget save: unknown field"):
        load_skill(_write(tmp_path, md, name="lookup"), tool_registry=_wregistry()[0])


def test_widget_data_unbound_root_is_load_error(tmp_path):
    md = WIDGET_SKILL.replace('"{{search_things.results}}"', '"{{nope.results}}"')
    with pytest.raises(ValueError, match="widget data references undeclared"):
        load_skill(_write(tmp_path, md, name="lookup"), tool_registry=_wregistry()[0])

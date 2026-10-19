"""Runtime determinism and the gate — one test per hole found in the
pre-release audit of the runtime.

Design decisions these encode (owner-approved, see the audit):

  4. GATE INPUTS AND CONFIRMATIONS COME FROM THE USER. A value for a
     ``must_elicit`` field, for a boolean an action names in ``requires:``, or
     for a ``sensitive`` field is accepted only when the user supplied it —
     through a structured submission, or as text they actually wrote, matched by
     WHOLE TOKEN rather than substring. A model-proposed value is dropped.
  5. NUMBERS ARE FINITE. NaN and infinities never satisfy ``min:``/``max:``,
     never reach a tool argument, and a comparison against one fails closed.
  6. CORRECTIONS INVALIDATE DEPENDENTS. Correcting a gate input clears any
     verification value already collected and any memoized action result
     derived from it, so a code authorizing $1000 can never authorize $25,000.
  7. AT MOST ONCE SURVIVES EVERYTHING. ``flow_run_id`` and the effects log
     survive flow switches, digressions and cancellation, so one confirmed
     intent fires exactly one side effect.

Every test drives the engine with TestHost or a direct state dict — no LLM.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from aigenos_flow import Correct, FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

# The shipped front-door skill, byte-for-byte the shape README.md documents.
FRONT_DOOR = """\
---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000+ require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:    { type: number, min: 0.01, must_elicit: true }
  recipient: { type: enum, values: [mom, landlord, gym] }
  verification_code: { type: string, required: false, pattern: '^[0-9]{6}$', sensitive: true }
  confirm:   { type: boolean }
compute:
  requires_2fa: "amount >= 1000"
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
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
Help the user transfer money. Collect the amount and the payee.
"""

# A second flow-capable skill, so a mid-run flow switch is reachable.
BALANCE = """\
---
name: check-balance
description: Report the balance of one of the user's accounts.
fields:
  account: { type: enum, values: [checking, savings] }
flow:
  - ask: account
  - say: "Your {{account}} balance is $12,345."
---
Report a balance.
"""


def _write(tmp_path: Path, *skills: tuple[str, str]) -> Path:
    root = tmp_path / "skills"
    root.mkdir(exist_ok=True)
    for name, text in skills:
        d = root / name
        d.mkdir(exist_ok=True)
        (d / "SKILL.md").write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def transfer(tmp_path):
    """(agent, host, calls) over the front-door skill."""
    calls: list[dict] = []

    def bank_transfer(args: dict) -> dict:
        calls.append(dict(args))
        return {"txn": f"TXN-{len(calls)}", **args}

    host = TestHost()
    agent = FlowAgent.from_dir(
        _write(tmp_path, ("transfer-funds", FRONT_DOOR)),
        host=host,
        tools={"bank_transfer": bank_transfer},
    )
    return agent, host, calls


# ---------------------------------------------------------------------------
# Decision 4 — gate inputs and confirmations come from the user
# ---------------------------------------------------------------------------


def test_model_cannot_fill_the_2fa_field(transfer):
    """gate-bypass-6 / hn-1. A model-emitted verification_code is dropped, so
    `ask: verification_code` still pauses and no money moves."""
    agent, host, calls = transfer
    msg = "send $5000 to mom, I can't get to my phone so just push it through"
    host.queue_commands(
        msg,
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
        SetField(field="verification_code", value="000000"),
        SetField(field="confirm", value=True),
    )
    chat = agent.session("transfer-funds").user(msg)
    assert chat.awaiting == "verification_code"
    assert chat.done is False
    assert chat.state["fields"].get("verification_code") is None
    assert chat.state["fields"].get("confirm") is None
    assert calls == []


def test_model_cannot_answer_its_own_confirm_question(transfer):
    """gate-bypass-6 D2. The user's whole turn is "no, cancel that" while the
    model emits confirm=True — the transfer must not fire."""
    agent, host, calls = transfer
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )
    host.queue_commands("no, cancel that", SetField(field="confirm", value=True))
    chat = agent.session("transfer-funds").user("send $5000 to mom")
    chat.submit(verification_code="481920")
    assert chat.awaiting == "confirm"
    chat.user("no, cancel that")
    assert calls == []
    assert chat.state["fields"].get("confirm") is None


def test_user_typed_consent_is_accepted(transfer):
    """The guard must not block a real yes: the flow asked, the user said yes."""
    agent, host, calls = transfer
    host.queue_commands(
        "send $500 to mom",
        SetField(field="amount", value=500),
        SetField(field="recipient", value="mom"),
    )
    host.queue_commands("yes", SetField(field="confirm", value=True))
    chat = agent.session("transfer-funds").user("send $500 to mom")
    assert chat.awaiting == "confirm"  # $500 < $1000, no 2FA step
    chat.user("yes")
    assert len(calls) == 1
    assert calls[0]["amount"] == 500.0


def test_secret_needs_the_pending_ask_not_just_stray_digits(transfer):
    """hn-2 G2. Six digits inside a phone number do not ground a 2FA code."""
    agent, host, calls = transfer
    msg = "send $5000 to mom, call me on 555-481920 if there's a problem"
    host.queue_commands(
        msg,
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
        SetField(field="verification_code", value="481920"),
    )
    chat = agent.session("transfer-funds").user(msg)
    assert chat.awaiting == "verification_code"
    assert chat.state["fields"].get("verification_code") is None


def test_code_typed_in_answer_to_the_ask_is_accepted(transfer):
    """The legitimate free-text path still works: the flow asked, the user
    typed the code."""
    agent, host, calls = transfer
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )
    host.queue_commands("481920", SetField(field="verification_code", value="481920"))
    chat = agent.session("transfer-funds").user("send $5000 to mom")
    assert chat.awaiting == "verification_code"
    chat.user("481920")
    assert chat.state["fields"]["verification_code"] == "481920"
    assert chat.awaiting == "confirm"


@pytest.mark.parametrize(
    "message,value",
    [
        ("send mom 1500", 150),       # substring of a longer digit run
        ("send mom 1500", 15),
        ("transfer to mom from account 1000234", 1000),
        ("pay landlord 2500 for may rent", 250),
        ("send the usual to mom", 999),
    ],
)
def test_grounding_is_token_exact_for_numbers(message, value):
    """gate-bypass-13 / hn-2 G3. The old substring rule let a model record $150
    for a "$1500" request, silently keeping the amount under the 2FA
    threshold."""
    from aigenos_flow.interpret import _is_grounded_in_user

    assert _is_grounded_in_user("amount", value, message) is False


@pytest.mark.parametrize(
    "message,value",
    [
        ("send $5000 to mom", 5000),
        ("send 5k to mom", 5000),
        ("send $1,000 to mom", 1000),
        ("2400.50 please", 2400.50),
    ],
)
def test_grounding_accepts_numbers_the_user_typed(message, value):
    from aigenos_flow.interpret import _is_grounded_in_user

    assert _is_grounded_in_user("amount", value, message) is True


def test_user_only_fields_are_derived_not_authored(tmp_path):
    """Decision 4's set is computed from the spec the loader already validates:
    must_elicit inputs, requires:-named confirmations, and secrets."""
    from aigenos_flow.interpret import user_only_fields

    agent = FlowAgent.from_dir(
        _write(tmp_path, ("transfer-funds", FRONT_DOOR)),
        host=TestHost(),
        tools={"bank_transfer": lambda args: {}},
    )
    skill = agent.registry.get("transfer-funds")
    assert user_only_fields(skill) == {"amount", "verification_code", "confirm"}


# ---------------------------------------------------------------------------
# Decision 5 — numbers are finite
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_amount_is_refused_and_re_asked(transfer, bad):
    """gate-bypass-3. `nan >= 1000` is False, so NaN slipped past `min: 0.01`
    AND turned the 2FA gate off: a transfer fired with no code at all."""
    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(
        amount=bad, recipient="mom", confirm=True
    )
    assert chat.awaiting == "amount"
    assert chat.done is False
    assert "amount" not in chat.state["fields"]
    assert calls == []
    assert any("I need a number" in r for r in chat.replies)


@pytest.mark.parametrize("expr", ["amount >= 1000", "amount < 1000", "amount == 1000"])
def test_comparison_against_a_non_finite_number_fails_closed(expr):
    from aigenos_flow.engine.safe_eval import NonFiniteOperandError, eval_strict

    with pytest.raises(NonFiniteOperandError):
        eval_strict(expr, {"amount": float("nan")})


def test_non_finite_never_reaches_a_tool_argument():
    """Belt and braces: even if a non-finite value reached state some other
    way, the effect refuses to call the tool."""
    from aigenos_flow.declarative import _assert_finite_args

    _assert_finite_args("submit", {"amount": 5000.0, "meta": {"ok": [1, 2]}})
    with pytest.raises(ValueError, match="finite"):
        _assert_finite_args("submit", {"amount": float("inf")})
    with pytest.raises(ValueError, match="finite"):
        _assert_finite_args("submit", {"meta": {"nested": [1, float("nan")]}})


# ---------------------------------------------------------------------------
# Decision 6 — corrections invalidate dependents
# ---------------------------------------------------------------------------


def test_raising_the_amount_after_2fa_re_asks_for_a_fresh_code(transfer):
    """gate-bypass-4 C2. A code the user typed while the screen said $1000 must
    not authorize $25,000."""
    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(amount=1000, recipient="mom")
    assert chat.awaiting == "verification_code"
    chat.submit(verification_code="481920")
    assert chat.awaiting == "confirm"

    chat.submit(amount=25000)
    assert chat.awaiting == "verification_code"
    assert chat.state["fields"]["verification_code"] is None

    chat.submit(confirm=True)
    assert calls == []


def test_swapping_the_payee_after_2fa_re_asks_for_a_fresh_code(transfer):
    """gate-bypass-4 C3. The code authorized a transfer to 'mom'."""
    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(amount=5000, recipient="mom")
    chat.submit(verification_code="481920")
    chat.submit(recipient="landlord")
    assert chat.awaiting == "verification_code"
    assert chat.state["fields"]["verification_code"] is None
    assert calls == []


def test_setfield_and_correct_behave_identically(transfer, tmp_path):
    """gate-bypass-5. Whether a gate re-arms must not depend on the model's
    choice of item type — the runtime decides that a write over a filled field
    is a correction."""
    results = {}
    for label, item_cls in (("SetField", SetField), ("Correct", Correct)):
        calls: list[dict] = []
        host = TestHost()
        agent = FlowAgent.from_dir(
            _write(tmp_path, ("transfer-funds", FRONT_DOOR)),
            host=host,
            tools={"bank_transfer": lambda args: calls.append(dict(args)) or {}},
        )
        host.queue_commands(
            "send $5000 to mom",
            SetField(field="amount", value=5000),
            SetField(field="recipient", value="mom"),
        )
        host.queue_commands(
            "yes",
            item_cls(field="recipient", value="landlord"),
            item_cls(field="confirm", value=True),
        )
        chat = agent.session("transfer-funds").user("send $5000 to mom")
        chat.submit(verification_code="481920")
        chat.user("yes")
        results[label] = (chat.awaiting, dict(chat.state["fields"]), list(calls))

    assert results["SetField"] == results["Correct"]
    awaiting, fields, calls = results["SetField"]
    # The payee changed under an already-approved summary: re-arm, don't send.
    assert awaiting == "verification_code"
    assert calls == []


def test_correction_clears_every_memo_even_with_depends_on(tmp_path):
    """gate-bypass-9. `@action(depends_on=[...])` used to preserve the memo of
    any action whose list did not name the changed field, with nothing checking
    that the list covered what the action actually read."""
    from aigenos_flow.decorators import action
    from aigenos_flow.fields import FieldSpec
    from aigenos_flow.runtime import invalidate_dependents
    from aigenos_flow.skills import Skill

    @action(depends_on=["from_account"], readonly=True)
    def check_policy(state):  # reads `amount`, declares only `from_account`
        return {"requires_2fa": state["fields"]["amount"] > 1000}

    skill = Skill(
        name="t", description="t", triggers=[], body="", path=".",
        fields={"amount": FieldSpec(type="number"), "from_account": FieldSpec()},
        flow_fn=lambda ctx: None,
        actions={"check_policy": check_policy},
        action_deps={"check_policy": ["from_account"]},
    )
    state = {
        "fields": {"amount": 500.0, "from_account": "checking"},
        "results": {"check_policy": {"requires_2fa": False}},
    }
    invalidate_dependents(state, skill, ["amount"])
    assert state["results"] == {}


# ---------------------------------------------------------------------------
# Decision 7 — at most once survives everything
# ---------------------------------------------------------------------------


def test_one_confirmed_transfer_survives_a_flow_switch(tmp_path):
    """gate-bypass-1. The effects log and flow_run_id belong to the flow RUN,
    so a digression must not hand the parent back a blank record. Before the
    fix this moved $14,000 for one confirmed intent."""
    calls: list[dict] = []

    def bank_transfer(args: dict) -> dict:
        calls.append(dict(args))
        return {"txn": f"TXN-{len(calls)}"}

    host = TestHost()
    agent = FlowAgent.from_dir(
        _write(tmp_path, ("transfer-funds", FRONT_DOOR), ("check-balance", BALANCE)),
        host=host,
        tools={"bank_transfer": bank_transfer},
    )
    chat = agent.session("transfer-funds").submit(amount=5000, recipient="mom")
    chat.submit(verification_code="481920")
    chat.submit(confirm=True)
    assert len(calls) == 1
    fired_log = dict(chat.state["effects_log"])
    assert fired_log

    # A mid-run flow switch and back.
    host.queue_commands("what's my checking balance", SetField(field="account", value="x"))
    from aigenos_flow.interpret import StartFlow

    host.queue_commands("what's my checking balance", StartFlow(flow_id="check-balance"))
    chat.user("what's my checking balance")
    chat.submit(account="checking")
    assert chat.state["flow_id"] == "transfer-funds"
    assert dict(chat.state["effects_log"]) == fired_log, "log wiped by the switch"

    # Correct the amount and re-confirm: at most once still holds.
    chat.submit(amount=9000)
    chat.submit(verification_code="481920")
    chat.submit(confirm=True)
    assert len(calls) == 1, f"double spend: {calls}"


def test_effects_log_is_carried_over_not_wiped_by_a_new_flow():
    from aigenos_flow.runtime import initial_state, reset_for_new_flow

    state = initial_state()
    reset_for_new_flow(state, "a")
    first_run = state["flow_run_id"]
    state["effects_log"]["pay:abc"] = {"txn": 1}
    reset_for_new_flow(state, "b")
    assert state["flow_run_id"] != first_run
    assert state["effects_log"] == {"pay:abc": {"txn": 1}}


def test_stack_frame_round_trips_the_run_scoped_keys():
    from aigenos_flow.runtime import (
        _pop_stack_frame,
        _push_stack_frame,
        initial_state,
        reset_for_new_flow,
    )

    state = initial_state()
    reset_for_new_flow(state, "parent")
    parent_run = state["flow_run_id"]
    state["effects_log"]["pay:1"] = {"txn": 1}

    _push_stack_frame(state)
    reset_for_new_flow(state, "child")
    assert state["flow_run_id"] != parent_run
    state["effects_log"] = {}  # a child that clears its own view

    assert _pop_stack_frame(state) is True
    assert state["flow_run_id"] == parent_run
    assert state["effects_log"] == {"pay:1": {"txn": 1}}


def test_cancelling_does_not_erase_what_already_fired(transfer):
    """Decision 7: at-most-once survives cancellation too. Cancel abandons the
    run; it must not hand the next run a blank fired-effect record."""
    from aigenos_flow.interpret import Cancel

    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(amount=500, recipient="mom")
    chat.submit(confirm=True)
    assert len(calls) == 1
    fired = dict(chat.state["effects_log"])

    host.queue_commands("nevermind", Cancel())
    chat.user("nevermind")
    assert chat.state["flow_id"] is None
    assert dict(chat.state["effects_log"]) == fired


def test_inline_step_gate_input_is_also_user_only(tmp_path):
    """Decision 4's extension: a field a step `when:` reads is a gate input, so
    the loader forces must_elicit on it and the runtime drops a model-proposed
    value for it — the rule is a property of the gate, not of where the author
    happened to spell it."""
    skill_md = """\
---
name: tiered-refund
description: Refund an order; over 100 needs a manager note.
fields:
  amount: { type: number, must_elicit: true }
  note:   { type: string, required: false, pattern: '^[A-Z]{3}-[0-9]{3}$' }
flow:
  - ask: amount
  - when: "amount > 100"
    ask: note
---
Refund flow.
"""
    host = TestHost()
    agent = FlowAgent.from_dir(
        _write(tmp_path, ("tiered-refund", skill_md)), host=host
    )
    msg = "refund 40 please"
    host.queue_commands(msg, SetField(field="amount", value=400))
    chat = agent.session("tiered-refund").user(msg)
    # 400 is not a number the user typed; 40 is. The value is dropped, so the
    # flow re-asks instead of silently crossing the 100 threshold.
    assert chat.awaiting == "amount"
    assert "amount" not in chat.state["fields"]


def test_node_updates_carries_every_checkpointed_key():
    """gate-bypass-2 (runtime half). One helper both graph builders derive
    their node return from, so a graph layer cannot silently drop the
    at-most-once guard again."""
    from aigenos_flow.runtime import CHECKPOINTED_KEYS, initial_state, node_updates

    for key in ("flow_run_id", "effects_log", "chat_config", "pending_widget"):
        assert key in CHECKPOINTED_KEYS

    state = initial_state()
    state["flow_run_id"] = "abc"
    state["effects_log"] = {"pay:abc": 1}
    out = node_updates(state)
    assert set(CHECKPOINTED_KEYS) <= set(out)
    assert out["flow_run_id"] == "abc"
    assert out["effects_log"] == {"pay:abc": 1}
    # Both pending inputs are always cleared, or a stale submission re-fires.
    assert out["pending_user_message"] is None
    assert out["pending_field_updates"] is None


def test_new_flow_updates_mints_a_fresh_run_id_and_keeps_the_log():
    """An activation that resets `fields` but leaves the previous run_id in the
    checkpoint would key the new run's effect to the old run's log entry and
    silently dedupe a legitimate second transfer away."""
    from aigenos_flow.runtime import new_flow_updates

    prev = {"flow_run_id": "old", "effects_log": {"pay:old": {"txn": 1}}}
    upd = new_flow_updates("transfer-funds", prev)
    assert upd["flow_run_id"] not in (None, "", "old")
    assert upd["effects_log"] == {"pay:old": {"txn": 1}}
    assert upd["fields"] == {} and upd["results"] == {}
    assert upd["done"] is False and upd["say_cursor"] == 0
    a = new_flow_updates("x")["flow_run_id"]
    b = new_flow_updates("x")["flow_run_id"]
    assert a != b


def test_standalone_graph_node_returns_every_checkpointed_key(tmp_path):
    """The graph that DOES carry the guarantee must keep carrying it."""
    from langgraph.checkpoint.memory import MemorySaver

    from aigenos_flow.runtime import CHECKPOINTED_KEYS

    calls: list[dict] = []
    host = TestHost()
    host.checkpointer = MemorySaver()  # as_langgraph() reads it off the host
    agent = FlowAgent.from_dir(
        _write(tmp_path, ("transfer-funds", FRONT_DOOR)),
        host=host,
        tools={"bank_transfer": lambda args: calls.append(dict(args)) or {}},
    )
    graph = agent.as_langgraph()
    cfg = {"configurable": {"thread_id": "t1"}}
    graph.invoke(
        {"pending_field_updates": {"amount": 5000, "recipient": "mom"},
         "flow_id": "transfer-funds"},
        config=cfg,
    )
    values = graph.get_state(cfg).values
    # The run-scoped security keys, without which at-most-once does not exist.
    for key in ("flow_run_id", "effects_log", "chat_config", "pending_widget"):
        assert key in values, f"{key} dropped from the checkpoint"
    # No drift: turn_node now derives its return from runtime.node_updates,
    # so every checkpointed key (including `pending_fields`, the ask_group
    # multi-field form) is written back.
    missing = [k for k in CHECKPOINTED_KEYS if k not in values]
    assert missing == [], f"unexpected drift: {missing}"


# ---------------------------------------------------------------------------
# Structured-submission vocabulary, audit honesty, declined effects
# ---------------------------------------------------------------------------


def test_submission_cannot_write_undeclared_field_names(transfer):
    """gate-bypass-15. The structured path gets the closed vocabulary the
    loader validates: a state-shaped payload key is dropped, not stored."""
    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(
        amount=5000,
        recipient="mom",
        requires_2fa=False,
        done=True,
        awaiting_field=None,
        flow_run_id="x",
        effects_log={},
    )
    assert set(chat.state["fields"]) <= {
        "amount", "recipient", "verification_code", "confirm",
    }
    assert chat.state["flow_run_id"] != "x"
    assert chat.awaiting == "verification_code"
    assert calls == []


def test_audit_does_not_invent_a_secret_that_was_never_supplied(transfer):
    """gate-bypass-11. Redacting per-binding logged otp='***REDACTED***' for a
    sub-threshold transfer that carried no code, so the log could not tell a
    2FA-verified write from one that skipped the gate."""
    from aigenos_flow.declarative import AuditSink

    calls: list[dict] = []
    sink = AuditSink()
    agent = FlowAgent.from_dir(
        Path(transfer[0].registry.get("transfer-funds").path).parent,
        host=TestHost(),
        tools={"bank_transfer": lambda args: calls.append(dict(args)) or {"id": "T1"}},
        audit=sink,
    )
    chat = agent.session("transfer-funds").submit(
        amount=500, recipient="mom", confirm=True
    )
    assert chat.done is True and len(calls) == 1
    rec = sink.records[-1]
    # The arg is absent entirely (wrapper-2: an unasked optional field is no
    # longer bound to None), which still satisfies what this test protects —
    # the log never shows a redaction marker for a code nobody supplied.
    assert "otp" not in rec["args"], rec
    assert rec["gate"] == {"confirm": True}

    # A code that WAS supplied is still redacted.
    sink.records.clear()
    calls.clear()
    chat = agent.session("transfer-funds").submit(amount=5000, recipient="mom")
    chat.submit(verification_code="481920")
    chat.submit(confirm=True)
    rec = sink.records[-1]
    assert rec["args"]["otp"] == "***REDACTED***"
    assert "481920" not in str(rec)


def test_declining_a_gated_effect_says_so(transfer):
    """gate-bypass-14. The conversation used to go silent after the user
    declined a money transfer."""
    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(amount=5000, recipient="mom")
    chat.submit(verification_code="481920")
    chat.submit(confirm=False)
    assert calls == []
    assert chat.replies, "declining produced no assistant output at all"
    assert chat.done is True


def test_correcting_after_an_effect_fired_is_not_silent(transfer):
    """hn-10. At-most-once is right, but the user must be told — prompting for
    and accepting a confirmation that is then discarded is the worst of both."""
    agent, host, calls = transfer
    chat = agent.session("transfer-funds").submit(amount=500, recipient="mom")
    chat.submit(confirm=True)
    assert len(calls) == 1

    chat.submit(amount=900)
    assert any("already went through" in r for r in chat.replies), chat.replies
    chat.submit(confirm=True)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# hn-7 — a Python @action write is at-most-once too
# ---------------------------------------------------------------------------


CHARGE_SKILL = """\
---
name: charge-card
description: Charge a card, then ask for a rating.
handlers: ./handlers.py
fields:
  amount:    { type: number, min: 0.01, must_elicit: true }
  recipient: { type: enum, values: [acme, globex] }
  confirm:   { type: boolean }
  rate_us:   { type: integer, min: 1, max: 5, required: false }
---
Charge the card, then ask for a rating.
"""

CHARGE_HANDLERS = '''\
from aigenos_flow import action, flow

CHARGES = []
LOOKUPS = []


@flow
def charge_flow(ctx):
    ctx.ask("amount")
    ctx.ask("recipient")
    ctx.run(lookup_rate)
    ctx.ask("confirm")
    receipt = ctx.run(charge_card)
    ctx.say(f"Charged, receipt {receipt['receipt']}.")
    ctx.ask("rate_us")


@action(requires="confirm")
def charge_card(state):
    f = state["fields"]
    CHARGES.append({"amount": f["amount"], "payee": f["recipient"]})
    return {"receipt": f"R{len(CHARGES)}"}


@action(readonly=True)
def lookup_rate(state):
    LOOKUPS.append(state["fields"].get("amount"))
    return {"rate": 0.01}
'''


def test_python_action_write_does_not_re_run_after_a_correction(tmp_path):
    """hn-7. ctx.run memoized only in `results`, which every correction clears
    — so correcting the amount charged the same card a second time."""
    root = tmp_path / "skills" / "charge-card"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(CHARGE_SKILL, encoding="utf-8")
    (root / "handlers.py").write_text(CHARGE_HANDLERS, encoding="utf-8")

    agent = FlowAgent.from_dir(root.parent, host=TestHost())
    handlers = agent.registry.get("charge-card").actions["charge_card"].__module__
    import sys

    mod = sys.modules[handlers]

    chat = agent.session("charge-card").submit(
        amount=250, recipient="acme", confirm=True
    )
    assert chat.awaiting == "rate_us"
    assert mod.CHARGES == [{"amount": 250.0, "payee": "acme"}]

    chat.submit(amount=9999)
    assert mod.CHARGES == [{"amount": 250.0, "payee": "acme"}], "charged twice"
    # A READ still re-runs on the corrected value — the memo really was cleared,
    # so it is `effects_log`, not the memo, holding the write back.
    assert mod.LOOKUPS == [250.0, 9999.0]


def test_ctx_run_re_checks_a_python_action_gate(tmp_path):
    """Decision 10's runtime half: the Python escape hatch is held to the same
    gate as a declarative `do:` step, at dispatch."""
    root = tmp_path / "skills" / "charge-card"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(CHARGE_SKILL, encoding="utf-8")
    (root / "handlers.py").write_text(CHARGE_HANDLERS, encoding="utf-8")
    agent = FlowAgent.from_dir(root.parent, host=TestHost())
    charge = agent.registry.get("charge-card").actions["charge_card"]

    from aigenos_flow.context import Context

    state = {"fields": {"amount": 1.0, "recipient": "acme", "confirm": False}}
    ctx = Context(state=state, field_specs={}, emit=lambda t: None)
    with pytest.raises(PermissionError, match="requires 'confirm'"):
        ctx.run(charge)


def test_non_finite_is_rejected_for_every_numeric_field(tmp_path):
    """Decision 5 across types: number and integer, min and max."""
    from aigenos_flow.runtime import _write_field
    from aigenos_flow.fields import FieldSpec
    from aigenos_flow.skills import Skill

    emitted: list[str] = []

    class H:
        def emit(self, t):
            emitted.append(t)

    skill = Skill(
        name="t", description="t", triggers=[], body="", path=".",
        fields={
            "n": FieldSpec(type="number", min=0.01, max=25000),
            "i": FieldSpec(type="integer", min=1),
        },
        flow_fn=lambda ctx: None,
    )
    for name in ("n", "i"):
        for bad in (float("nan"), float("inf"), float("-inf")):
            state = {"fields": {}}
            wrote, _ = _write_field(state, skill, name, bad, H())
            assert wrote is False, (name, bad)
            assert state["fields"] == {}
    assert emitted and all("I need a number" in m for m in emitted)
    # Sanity: a finite value still lands.
    state = {"fields": {}}
    assert _write_field(state, skill, "n", 5000, H())[0] is True
    assert math.isfinite(state["fields"]["n"])

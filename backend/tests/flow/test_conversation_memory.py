"""A cancelled flow is over. It is not forgotten.

The state that drives a flow belongs to one run: a cancel ends the run, and
nothing of it can act later. That is what makes a flow deterministic, and it
used to be done by forgetting the run ever happened. A user who cancelled a
payment and then asked "who did I pick?" was asked the flow's first question,
and "continue" started from nothing.

Two things now outlive a run, and neither can cause an action:

* ``journal``: what each run came to. The model reads it to answer questions.
* ``parked``: the run the user last cancelled part-way. "continue" restores it
  at the question it stopped on, without its approvals or its secrets.

Plus two commands for the cases a cancel is too blunt for: ``Restart`` (this
task again from its first question) and ``Reopen`` (ask one earlier question
again).

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import aigenos_flow.runtime as runtime
from aigenos_flow import FlowAgent
from aigenos_flow.host import ToolCall
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import (
    Cancel,
    Correct,
    OutsideFlowDecision,
    Reopen,
    Reply,
    Restart,
    Resume,
    SetField,
    TurnInterpretation,
    _build_tools_for_skill,
    _decode_tool_calls,
    _filter_must_elicit,
    render_memory,
    render_paused,
)

PAY = """\
---
name: pay
description: Pay a saved payee. Use when the user wants to send money.
allowed-tools: send_payment
fields:
  payee:
    type: string
    pattern: '^[A-Za-z ]{2,30}$'
    description: Who are you paying?
  amount:
    type: number
    min: 1
    must_elicit: true
    description: How much?
  code:
    type: string
    sensitive: true
    pattern: '^[0-9]{6}$'
    description: Enter the 6-digit code.
  confirm:
    type: boolean
    description: Send this payment?
actions:
  send_payment:
    tool: send_payment
    requires: confirm
    args: { payee: "{{payee}}", amount: "{{amount}}" }
flow:
  - ask: payee
  - ask: amount
  - ask: code
  - say: "Sending {{amount}} to {{payee}}."
  - ask: confirm
  - when: "confirm"
    do: send_payment
    then_say: "Sent: {{send_payment.reference}}."
---
Pay someone.
"""

ERRAND = """\
---
name: errand
description: The money desk. Use when the user wants help with a payment.
handlers: ./handlers.py
fields:
  kind:
    type: enum
    values: [someone_new, a_saved_payee]
    must_elicit: true
    description: Is this to someone new, or to a saved payee?
---
Route the user to the payment flow.
"""

ERRAND_PY = """\
from aigenos_flow import Context, flow


@flow
def errand(ctx: Context):
    ctx.ask("kind")
    if ctx.link("pay"):
        ctx.say("All done.")
    else:
        ctx.say("No payment was made.")
"""

NOTE = """\
---
name: note
description: Save a note. Use when the user wants to write something down.
allowed-tools: save_note
fields:
  text:
    type: string
    pattern: '^[a-z0-9 ]{1,40}$'
    description: What should the note say?
  confirm:
    type: boolean
    description: Save it?
actions:
  save_note:
    tool: save_note
    requires: confirm
    args: { text: "{{text}}" }
flow:
  - ask: text
  - ask: confirm
  - when: "confirm"
    do: save_note
    then_say: "Saved."
---
Save a note.
"""

TRIP = """\
---
name: trip
description: Hold a seat, then order a meal. Use when the user books a trip.
allowed-tools: hold_seat, order_meal
fields:
  seat:
    type: string
    pattern: '^[0-9]{1,2}[A-F]$'
    description: Which seat?
  hold:
    type: boolean
    description: Hold this seat?
  meal:
    type: string
    pattern: '^[a-z]{2,20}$'
    description: Which meal?
  order:
    type: boolean
    description: Order this meal?
actions:
  hold_seat:
    tool: hold_seat
    requires: hold
    args: { seat: "{{seat}}" }
  order_meal:
    tool: order_meal
    requires: order
    args: { meal: "{{meal}}" }
flow:
  - ask: seat
  - ask: hold
  - when: "hold"
    do: hold_seat
    then_say: "Held: {{hold_seat.reference}}."
  - ask: meal
  - ask: order
  - when: "order"
    do: order_meal
    then_say: "Ordered: {{order_meal.reference}}."
---
Hold a seat and order a meal.
"""


PICK_PAY = """\
---
name: pick-pay
description: Pay a payee chosen from a list. Use when the user pays a saved payee.
allowed-tools: list_payees, send_payment
fields:
  payee_id:
    type: string
    pattern: '^P-[0-9]{4}$'
    description: Who are you paying? Pick them from the list.
  amount:
    type: number
    min: 1
    must_elicit: true
    description: How much?
  confirm:
    type: boolean
    description: Send this payment?
actions:
  list_payees:
    tool: list_payees
    readonly: true
  send_payment:
    tool: send_payment
    requires: confirm
    args: { payee: "{{payee_id}}", amount: "{{amount}}" }
flow:
  - do: list_payees
  - pick: "{{list_payees.payees}}"
    save: [payee_id]
    prompt: Who are you paying? Pick them from the list.
  - ask: amount
  - ask: confirm
  - when: "confirm"
    do: send_payment
    then_say: "Sent: {{send_payment.reference}}."
---
Pay someone from the list. You cannot choose the payee.
"""

PAYEES = [{"payee_id": "P-1001", "name": "Mom"}, {"payee_id": "P-1002", "name": "Landlord"}]


@pytest.fixture
def desk(tmp_path: Path):
    """(agent, host, fired): five skills on disk and a record of every write."""
    for name, skill_md in (
        ("pay", PAY), ("errand", ERRAND), ("note", NOTE), ("trip", TRIP), ("pick-pay", PICK_PAY),
    ):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (tmp_path / "errand" / "handlers.py").write_text(textwrap.dedent(ERRAND_PY), encoding="utf-8")

    fired: list[tuple[str, dict]] = []

    def tool(name: str, reference: str):
        return lambda args: fired.append((name, dict(args))) or {"reference": reference}

    tools = {
        "list_payees": lambda args: {"payees": [dict(row) for row in PAYEES]},
        "send_payment": tool("send_payment", "PM-1"),
        "save_note": tool("save_note", "NT-1"),
        "hold_seat": tool("hold_seat", "ST-1"),
        "order_meal": tool("order_meal", "ML-1"),
    }
    host = TestHost()
    host.checkpointer = MemorySaver()
    return FlowAgent.from_dir(tmp_path, host=host, tools=tools), host, fired


def _stack(chat) -> list[str]:
    return [frame["flow_id"] for frame in chat.state.get("flow_stack") or []]


def _cancel(chat, host, text: str = "never mind"):
    host.queue_commands(text, Cancel())
    return chat.user(text)


def _resume_outside(chat, host, text: str = "continue", *changes):
    """The user asks to go back, with no flow running."""
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
    host.queue_commands(text, *changes)
    return chat.user(text)


# --------------------------------------------------------------------------- the journal
def test_a_cancelled_run_is_written_down(desk):
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    assert chat.awaiting == "code"
    _cancel(chat, host)

    assert chat.state["flow_id"] is None and not fired
    (entry,) = chat.state["journal"]
    assert entry["flow"] == "pay" and entry["outcome"] == "cancelled"
    assert entry["fields"] == {"payee": "Landlord", "amount": 500}
    assert entry["waiting_for"] == "code"
    assert entry["wrote"] is False


def test_a_finished_run_and_a_declined_run_are_written_down(desk):
    agent, _, fired = desk
    done = agent.session("note").submit(text="milk").submit(confirm=True)
    assert done.done and fired == [("save_note", {"text": "milk"})]
    assert [(e["flow"], e["outcome"]) for e in done.state["journal"]] == [("note", "ended")]
    assert "NT-1" in done.state["journal"][0]["results"]

    said_no = agent.session("note").submit(text="eggs").submit(confirm=False)
    assert said_no.done and len(fired) == 1
    assert [(e["flow"], e["outcome"]) for e in said_no.state["journal"]] == [("note", "declined")]


def test_a_secret_never_reaches_the_journal_or_the_parked_run(desk):
    agent, host, _ = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500).submit(code="481920")
    assert chat.awaiting == "confirm"
    _cancel(chat, host)

    assert chat.state["journal"][-1]["fields"]["code"] == "(hidden)"
    assert "481920" not in repr(chat.state["journal"])
    assert "481920" not in repr(chat.state["parked"])


def test_one_run_has_one_entry_however_often_it_replays(desk):
    agent, _, _ = desk
    chat = agent.session("note").submit(text="milk").submit(confirm=True)
    chat.submit(confirm=True)  # the same yes, delivered twice
    assert len(chat.state["journal"]) == 1


def test_the_journal_is_bounded(desk):
    agent, host, _ = desk
    chat = agent.session("note")
    for n in range(12):
        chat.state["flow_id"] = "note"
        chat.state["done"] = False
        runtime.reset_for_new_flow(chat.state, "note")
        chat.submit(text=f"n{n}").submit(confirm=False)
    assert len(chat.state["journal"]) == runtime._JOURNAL_MAX
    assert chat.state["journal"][-1]["fields"]["text"] == "n11"


# --------------------------------------------------------------------------- the model can read it
def test_the_record_reaches_the_model_inside_the_next_flow(desk, monkeypatch):
    """The transcript that started this: cancel, start again, "who did I pick?"."""
    agent, host, _ = desk
    seen: list[dict] = []
    real = runtime.interpret_turn

    def spy(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(runtime, "interpret_turn", spy)
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    _cancel(chat, host)

    host.queue_classification("send money again", "pay")
    host.queue_commands("send money again")
    chat.user("send money again")
    host.queue_commands("who did I select last", Reply(text="You picked Landlord."))
    chat.user("who did I select last")

    memory = seen[-1]["memory"]
    assert '"pay": CANCELLED' in memory and "Landlord" in memory and '"code"' in memory
    assert "NOT a source of values" in memory
    assert chat.replies[0] == "You picked Landlord."
    assert chat.awaiting == "payee" and chat.state["fields"].get("payee") is None


def test_the_record_reaches_the_model_with_no_flow_running(desk, monkeypatch):
    agent, host, _ = desk
    seen: list[dict] = []
    import aigenos_flow.interpret as interpret

    real = interpret.decide_outside_flow

    def spy(**kwargs):
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(interpret, "decide_outside_flow", spy)
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    _cancel(chat, host)

    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="reply", reply="Landlord."))
    chat.user("who was I paying?")

    assert chat.replies == ["Landlord."]
    assert "CANCELLED" in seen[-1]["memory"] and "Landlord" in seen[-1]["memory"]
    assert "# Paused task" in seen[-1]["paused"] and 'action "resume"' in seen[-1]["paused"]
    assert chat.state["flow_id"] is None  # a question starts nothing


def test_a_first_message_sees_no_record(desk, monkeypatch):
    agent, host, _ = desk
    seen: list[dict] = []
    real = runtime.interpret_turn
    monkeypatch.setattr(runtime, "interpret_turn", lambda **kw: seen.append(kw) or real(**kw))
    host.queue_commands("hello")
    agent.session("pay").user("hello")
    assert seen[0]["memory"] == "" and seen[0]["paused"] == ""


def test_what_the_record_says():
    journal = [
        {"flow": "pay", "outcome": "cancelled", "fields": {"payee": "P-1002"}, "waiting_for": "amount",
         "results": '{"list_payees": [{"id": "P-1002", "name": "Landlord"}]}', "handoffs": [], "wrote": False},
        {"flow": "errand", "outcome": "ended", "fields": {"kind": "a_saved_payee"}, "results": "{}",
         "handoffs": [{"flow": "pay", "completed": False}], "wrote": False},
    ]
    text = render_memory(journal, [{"role": "user", "content": "never mind"}], in_flow=True)
    assert '1. "pay": CANCELLED by the user part-way' in text and 'waiting for "amount"' in text
    assert "Landlord" in text and "pay (NOT completed)" in text
    assert "ONLY if a result or a line" in text and "NOT a source of values" in text
    outside = render_memory(journal, None, in_flow=False)
    assert "CANCELLED" in outside and "NOT a source of values" not in outside
    assert render_memory([], [{"role": "user", "content": "hi"}]) == ""
    assert render_paused(None) == ""
    paused = render_paused({"root": "errand", "flow_id": "pay", "waiting_for": "amount", "shown": {"payee": "P-1002"}})
    assert '"errand" (it had reached "pay")' in paused and "emit Resume" in paused and "P-1002" in paused


# --------------------------------------------------------------------------- park and resume
def test_continue_picks_the_cancelled_run_up_where_it_stopped(desk):
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    run_id = chat.state["flow_run_id"]
    _cancel(chat, host)
    assert chat.state["parked"]["fields"] == {"payee": "Landlord", "amount": 500.0}

    _resume_outside(chat, host)

    assert chat.replies == ["Picking up where you left off.", "Enter the 6-digit code."]
    assert chat.state["flow_id"] == "pay" and chat.awaiting == "code"
    assert chat.state["fields"]["payee"] == "Landlord" and chat.state["fields"]["amount"] == 500
    assert chat.state["flow_run_id"] == run_id
    assert chat.state["parked"] is None and not fired

    chat.submit(code="481920").submit(confirm=True)
    assert fired == [("send_payment", {"payee": "Landlord", "amount": 500.0})]
    assert chat.done


def test_an_approval_is_never_kept_for_later(desk):
    """Cancelled at the last question with the code entered: picking it up asks
    for the code and the yes again, and shows what is being approved again."""
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500).submit(code="481920")
    assert chat.awaiting == "confirm"
    _cancel(chat, host)
    assert set(chat.state["parked"]["fields"]) == {"payee", "amount"}

    _resume_outside(chat, host)
    assert chat.awaiting == "code" and not fired
    chat.submit(code="481920")
    assert "Sending 500 to Landlord." in " ".join(chat.replies)
    assert chat.awaiting == "confirm" and not fired
    chat.submit(confirm=True)
    assert len(fired) == 1


def test_a_yes_in_the_message_that_resumes_approves_nothing(desk):
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500).submit(code="481920")
    _cancel(chat, host)

    _resume_outside(
        chat, host, "yes continue and send it",
        SetField(field="confirm", value=True), SetField(field="code", value="481920"),
    )
    assert not fired and chat.awaiting == "code"
    assert chat.state["fields"].get("confirm") is None and chat.state["fields"].get("code") is None


def test_the_message_that_resumes_can_change_a_value(desk):
    agent, host, _ = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    _cancel(chat, host)

    _resume_outside(chat, host, "continue but make it 600", Correct(field="amount", value=600))
    assert chat.state["fields"]["amount"] == 600 and chat.state["fields"]["payee"] == "Landlord"
    assert chat.replies[0] == "Picking up where you left off." and chat.awaiting == "code"


def test_a_run_with_nothing_entered_is_not_kept_and_replaces_nothing(desk):
    agent, host, _ = desk
    chat = agent.session("pay").submit(payee="Landlord")
    _cancel(chat, host)
    kept = dict(chat.state["parked"])

    host.queue_classification("a note please", "note")
    host.queue_commands("a note please")
    chat.user("a note please")
    assert chat.state["flow_id"] == "note" and chat.awaiting == "text"
    _cancel(chat, host, "forget it")

    assert chat.state["parked"] == kept
    assert [e["flow"] for e in chat.state["journal"]] == ["pay", "note"]


def test_continue_with_nothing_parked_starts_nothing(desk):
    agent, host, _ = desk
    chat = agent.session("note").submit(text="milk").submit(confirm=True)
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
    chat.user("continue")
    assert chat.replies == ["Sure — what would you like to do next?"]
    assert chat.done and chat.state["flow_id"] == "note"


def test_a_parked_run_whose_skill_is_gone_is_not_restored(desk):
    agent, host, _ = desk
    chat = agent.session("pay").submit(payee="Landlord")
    _cancel(chat, host)
    chat.state["parked"]["flow_id"] = "removed-skill"

    _resume_outside(chat, host)
    assert chat.replies == ["That is no longer there to pick up. Tell me what you would like to do."]
    assert chat.state["parked"] is None and chat.state["flow_id"] is None


# --------------------------------------------------------------------------- an errand of several flows
def test_a_cancel_inside_a_hand_off_parks_the_whole_errand(desk):
    agent, host, fired = desk
    chat = agent.session("errand").submit(kind="a_saved_payee")
    assert chat.state["flow_id"] == "pay" and _stack(chat) == ["errand"]
    chat.submit(payee="Landlord")
    _cancel(chat, host)

    # The errand ends, as before: the router is told its hand-off did nothing.
    assert "No payment was made." in chat.replies and chat.done and not fired
    assert [(e["flow"], e["outcome"]) for e in chat.state["journal"]] == [
        ("pay", "cancelled"), ("errand", "ended"),
    ]
    parked = chat.state["parked"]
    assert parked["root"] == "errand" and parked["flow_id"] == "pay"
    assert [f["flow_id"] for f in parked["frames"]] == ["errand"]

    _resume_outside(chat, host)
    assert chat.state["flow_id"] == "pay" and _stack(chat) == ["errand"]
    assert chat.awaiting == "amount" and chat.state["fields"] == {"payee": "Landlord"}

    chat.submit(amount=500).submit(code="481920").submit(confirm=True)
    assert fired == [("send_payment", {"payee": "Landlord", "amount": 500.0})]
    # Back in the router on its "the hand-off did its job" line.
    assert chat.replies[-1] == "All done." and chat.done and _stack(chat) == []


# --------------------------------------------------------------------------- going back while something else runs
def test_going_back_mid_flow_leaves_the_other_flow_waiting_underneath(desk):
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    _cancel(chat, host)

    host.queue_classification("a note please", "note")
    host.queue_commands("a note please")
    chat.user("a note please").submit(text="milk")
    assert chat.awaiting == "confirm"

    host.queue_commands("go back to the payment", Resume())
    chat.user("go back to the payment")
    assert chat.replies[0] == "Picking up where you left off."
    assert chat.state["flow_id"] == "pay" and _stack(chat) == ["note"] and chat.awaiting == "code"

    chat.submit(code="481920").submit(confirm=True)
    assert fired == [("send_payment", {"payee": "Landlord", "amount": 500.0})]
    # The note is where it was left: its text kept, its own yes still to give.
    assert "Now, back to where we were." in chat.replies
    assert chat.state["flow_id"] == "note" and chat.state["fields"]["text"] == "milk"
    assert chat.awaiting == "confirm"


def test_going_back_from_a_fresh_empty_run_replaces_it(desk):
    """Cancel, start the same task again, then "no, carry on with the earlier
    one": the empty new run is not left underneath to be returned to."""
    agent, host, _ = desk
    chat = agent.session("errand").submit(kind="a_saved_payee").submit(payee="Landlord")
    _cancel(chat, host)

    host.set_schema_override(
        OutsideFlowDecision, OutsideFlowDecision(action="start_skill", skill="errand")
    )
    host.queue_commands("send money")
    chat.user("send money")
    assert chat.state["flow_id"] == "errand" and chat.awaiting == "kind"
    assert chat.state["parked"] is not None  # starting again does not throw the old run away

    host.queue_commands("carry on with the earlier one", Resume())
    chat.user("carry on with the earlier one")
    assert chat.state["flow_id"] == "pay" and _stack(chat) == ["errand"]
    assert chat.awaiting == "amount" and chat.state["fields"] == {"payee": "Landlord"}


def test_resume_with_nothing_parked_changes_nothing(desk):
    agent, host, _ = desk
    chat = agent.session("pay").submit(payee="Landlord")
    host.queue_commands("go back", Resume())
    chat.user("go back")
    assert chat.state["flow_id"] == "pay" and chat.awaiting == "amount"
    assert chat.state["fields"] == {"payee": "Landlord"}


# --------------------------------------------------------------------------- a run that already wrote
def test_a_run_that_already_wrote_comes_back_as_it_was(desk):
    """The seat is held, the user cancels at the meal, then continues: the seat
    is not held twice and its approval is not asked for again."""
    agent, host, fired = desk
    chat = agent.session("trip").submit(seat="12A").submit(hold=True)
    assert fired == [("hold_seat", {"seat": "12A"})] and chat.awaiting == "meal"
    _cancel(chat, host)
    assert chat.state["journal"][-1]["wrote"] is True

    _resume_outside(chat, host)
    assert chat.awaiting == "meal" and len(fired) == 1
    chat.submit(meal="pasta").submit(order=True)
    assert fired == [("hold_seat", {"seat": "12A"}), ("order_meal", {"meal": "pasta"})]
    assert chat.done


# --------------------------------------------------------------------------- start over
def test_start_over_goes_back_to_the_first_question_in_one_message(desk):
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500)
    old_run = chat.state["flow_run_id"]

    host.queue_commands("start over", Restart(), Reply(text="dropped"))
    chat.user("start over")

    assert chat.replies == ["Okay, starting over.", "Who are you paying?"]
    assert chat.state["flow_id"] == "pay" and chat.awaiting == "payee"
    assert chat.state["fields"] == {} and chat.state["flow_run_id"] != old_run
    assert chat.state["journal"][-1]["outcome"] == "restarted" and not fired


def test_start_over_inside_a_hand_off_restarts_the_errand(desk):
    agent, host, _ = desk
    chat = agent.session("errand").submit(kind="a_saved_payee").submit(payee="Landlord")
    host.queue_commands("start over", Restart())
    chat.user("start over")
    assert chat.state["flow_id"] == "errand" and _stack(chat) == [] and chat.awaiting == "kind"
    assert chat.state["fields"] == {}


def test_start_over_after_a_write_says_the_write_stays(desk):
    agent, host, fired = desk
    chat = agent.session("trip").submit(seat="12A").submit(hold=True)
    host.queue_commands("start over", Restart())
    chat.user("start over")
    assert chat.replies[0] == "Okay, starting over. What already went through stays done."
    assert chat.awaiting == "seat" and len(fired) == 1
    # A fresh run is gated like any other: a new yes holds a new seat.
    chat.submit(seat="14C").submit(hold=True)
    assert fired == [("hold_seat", {"seat": "12A"}), ("hold_seat", {"seat": "14C"})]


def test_start_over_leaves_a_flow_waiting_under_a_side_trip_alone(desk):
    agent, host, _ = desk
    chat = agent.session("note").submit(text="milk")
    from aigenos_flow.interpret import StartFlow

    host.queue_commands("pay someone first", StartFlow(flow_id="pay"))
    chat.user("pay someone first").submit(payee="Landlord")
    host.queue_commands("start over", Restart())
    chat.user("start over")
    assert chat.state["flow_id"] == "pay" and chat.awaiting == "payee"
    assert _stack(chat) == ["note"] and chat.state["flow_stack"][0]["fields"] == {"text": "milk"}


# --------------------------------------------------------------------------- answer one question again
def test_reopen_asks_one_earlier_question_again(desk):
    agent, host, fired = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500).submit(code="481920")
    assert chat.awaiting == "confirm"

    host.queue_commands("let me pick someone else", Reply(text="Sure."), Reopen(field="payee"))
    chat.user("let me pick someone else")

    # What was authorised with the old payee is void, and the user is told.
    assert chat.replies == [
        "Sure.",
        "That changes what you approved, so your earlier approval no longer applies.",
        "Who are you paying?",
    ]
    assert chat.awaiting == "payee" and chat.state["fields"]["amount"] == 500
    assert chat.state["fields"].get("code") is None and not fired
    chat.submit(payee="Mom")
    assert chat.awaiting == "code"
    chat.submit(code="481920").submit(confirm=True)
    assert fired == [("send_payment", {"payee": "Mom", "amount": 500.0})]


def test_reopen_cannot_touch_a_confirmation_a_secret_or_an_unknown_field(desk):
    agent, host, _ = desk
    chat = agent.session("pay").submit(payee="Landlord").submit(amount=500).submit(code="481920")
    before = dict(chat.state["fields"])
    host.queue_commands(
        "hmm", Reopen(field="confirm"), Reopen(field="code"), Reopen(field="nope"),
    )
    chat.user("hmm")
    assert chat.state["fields"] == before and chat.awaiting == "confirm"


# --------------------------------------------------------------------------- a question keeps its answer
def _errand_skill(desk):
    return desk[0].registry.get("errand")


def test_a_question_is_not_answered_with_please_choose_one_of(desk):
    """"no I had selected payee earlier who was that": the model answered and
    also guessed the choice on screen. The guess goes; the answer stays."""
    interp = TurnInterpretation(items=[
        Reply(text="Last time you picked Landlord, then cancelled."),
        SetField(field="kind", value="a_saved_payee"),
    ])
    out = _filter_must_elicit(
        interp, _errand_skill(desk), "no I had selected payee earlier who was that",
        awaiting_field="kind", current_fields={},
    )
    assert [type(i).__name__ for i in out.items] == ["Reply"]
    assert out.items[0].text == "Last time you picked Landlord, then cancelled."


def test_an_answer_that_is_not_a_question_still_gets_the_hint(desk):
    interp = TurnInterpretation(items=[
        Reply(text="Got it."), SetField(field="kind", value="a_saved_payee"),
    ])
    out = _filter_must_elicit(
        interp, _errand_skill(desk), "I have paid them before",
        awaiting_field="kind", current_fields={},
    )
    assert out.items[0].text == "Please choose one of: Someone New, A Saved Payee."


def test_a_question_at_a_confirmation_never_keeps_the_models_words(desk):
    """There the model's words could be "sending it now"."""
    pay = desk[0].registry.get("pay")
    interp = TurnInterpretation(items=[
        Reply(text="Sending it now."), SetField(field="confirm", value=True),
    ])
    out = _filter_must_elicit(
        interp, pay, "can you just send it?",
        awaiting_field="confirm", current_fields={"payee": "Landlord", "amount": 500, "code": "481920"},
    )
    assert [type(i).__name__ for i in out.items] == ["Reply"]
    assert out.items[0].text == "Please answer yes or no."


# --------------------------------------------------------------------------- strict mode
def test_strict_mode_offers_the_same_three_commands(desk):
    pay = desk[0].registry.get("pay")
    tools = {t["name"]: t for t in _build_tools_for_skill(pay, [])}
    assert "restart" in tools and "resume_paused" not in tools
    assert tools["reopen"]["input_schema"]["properties"]["field"]["enum"] == ["payee", "amount"]
    assert "resume_paused" in {t["name"] for t in _build_tools_for_skill(pay, [], paused=True)}

    items = _decode_tool_calls(
        [
            ToolCall(name="restart", args={}),
            ToolCall(name="reopen", args={"field": "payee"}),
            ToolCall(name="reopen", args={"field": "made_up"}),
            ToolCall(name="resume_paused", args={}),
        ],
        pay,
    ).items
    assert [type(i).__name__ for i in items] == ["Restart", "Reopen", "Resume"]


def test_start_over_in_strict_mode(desk):
    agent, host, _ = desk
    strict = FlowAgent(agent.registry, host, mode="strict")
    chat = strict.session("pay").submit(payee="Landlord")
    host.queue_tool_calls("start over", ToolCall(name="restart", args={}))
    chat.user("start over")
    assert chat.replies == ["Okay, starting over.", "Who are you paying?"]
    assert chat.state["fields"] == {}


# --------------------------------------------------------------------------- through the compiled graph
def test_the_memory_survives_the_checkpoint_and_resumes_through_the_graph(desk):
    agent, host, fired = desk
    graph = agent.as_langgraph()
    config = {"configurable": {"thread_id": "memory"}}
    host.queue_classification("pay my landlord", "pay")
    host.queue_commands("pay my landlord")
    graph.invoke({"pending_user_message": "pay my landlord"}, config=config)
    graph.invoke(Command(resume={"payee": "Landlord"}), config=config)
    host.queue_commands("never mind", Cancel())
    graph.invoke(Command(resume="never mind"), config=config)

    values = graph.get_state(config).values
    assert values["flow_id"] is None
    assert values["journal"][-1]["outcome"] == "cancelled"
    assert values["parked"]["fields"] == {"payee": "Landlord"}

    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
    host.queue_commands("continue")
    graph.invoke({"pending_user_message": "continue"}, config=config)
    values = graph.get_state(config).values
    assert values["flow_id"] == "pay" and values["awaiting_field"] == "amount"
    assert values["fields"] == {"payee": "Landlord"} and values["parked"] is None
    said = [m["content"] for m in values["history"] if m["role"] == "assistant"]
    assert said[-2:] == ["Picking up where you left off.", "How much?"]
    assert not fired


# --------------------------------------------------------------------------- a list is read, never chosen from
def _at_the_list(desk):
    agent, host, _ = desk
    host.queue_commands("pay someone")
    chat = agent.session("pick-pay").user("pay someone")
    assert chat.awaiting == "payee_id"
    return chat, host


def test_the_model_reads_the_list_the_flow_showed(desk, monkeypatch):
    """Mid-flow "who did I pick?" used to be answered with the id: the model saw
    the collected value and not the list it came from."""
    seen: list[dict] = []
    real = runtime.interpret_turn
    monkeypatch.setattr(runtime, "interpret_turn", lambda **kw: seen.append(kw) or real(**kw))
    chat, host = _at_the_list(desk)
    chat.submit(payee_id="P-1002")
    host.queue_commands("who did I pick?", Reply(text="You picked Landlord."))
    chat.user("who did I pick?")

    assert "CURRENT flow" in seen[-1]["memory"] and "Landlord" in seen[-1]["memory"]
    assert "never choose a row" in seen[-1]["memory"]
    assert chat.replies == ["You picked Landlord.", "How much?"]


def test_reading_the_list_does_not_let_the_model_choose_from_it(desk):
    """The user names a row in words. The model knows its id now, and the id it
    sends is not the user's click: it is dropped and the list stays up."""
    chat, host = _at_the_list(desk)
    host.queue_commands("Mom", Reply(text="Mom it is."), SetField(field="payee_id", value="P-1001"))
    chat.user("Mom")
    assert chat.state["fields"].get("payee_id") is None and chat.awaiting == "payee_id"
    assert chat.replies[0] == "Please pick one from the list."


def test_an_id_the_user_typed_is_theirs(desk):
    chat, host = _at_the_list(desk)
    host.queue_commands("P-1001 please", SetField(field="payee_id", value="P-1001"))
    chat.user("P-1001 please")
    assert chat.state["fields"]["payee_id"] == "P-1001" and chat.awaiting == "amount"


def test_asking_for_a_different_row_in_words_brings_the_list_back(desk):
    chat, host = _at_the_list(desk)
    chat.submit(payee_id="P-1002").submit(amount=500)
    assert chat.awaiting == "confirm"
    host.queue_commands(
        "send it to Mom instead", Reply(text="Sure, Mom."), Correct(field="payee_id", value="P-1001"),
    )
    chat.user("send it to Mom instead")
    # Not "Sure, Mom.": nothing was chosen. The list is back and the amount is kept.
    assert chat.replies == ["Okay.", "Who are you paying? Pick them from the list."]
    assert chat.awaiting == "payee_id" and chat.state["fields"].get("payee_id") is None
    assert chat.state["fields"]["amount"] == 500


def test_restating_the_pick_changes_nothing(desk):
    """Answering "who did I pick?" a model tends to send the pick back as well.
    That is not a change, so it is not refused and nothing is reopened."""
    chat, host = _at_the_list(desk)
    chat.submit(payee_id="P-1002")
    host.queue_commands(
        "who did I pick?", Reply(text="You picked Landlord."), SetField(field="payee_id", value="P-1002"),
    )
    chat.user("who did I pick?")
    assert chat.replies == ["You picked Landlord.", "How much?"]
    assert chat.state["fields"]["payee_id"] == "P-1002"


# --------------------------------------------------------------------------- the flow asks the questions
def test_an_answer_does_not_end_with_the_models_own_question():
    trim = runtime._without_trailing_question
    assert trim("You picked Landlord. How much would you like to send?") == "You picked Landlord."
    assert trim("Sure! Which room instead?") == "Sure!"
    assert trim("Did you mean the 5th of October?") == "Did you mean the 5th of October?"
    assert trim("Updated to $200.") == "Updated to $200."
    # The flow's own prompt, repeated word for word, is dropped even when it is not a question.
    ask = "Who are you paying? Pick them from the list."
    assert trim(f"You previously selected Landlord. {ask}", [ask]) == "You previously selected Landlord."
    assert trim(ask, [ask]) == ask


def test_a_reply_does_not_repeat_the_question_the_flow_is_about_to_ask(desk):
    chat, host = _at_the_list(desk)
    chat.submit(payee_id="P-1002")
    host.queue_commands("hm", Reply(text="You picked Landlord. How much would you like to send?"))
    chat.user("hm")
    assert chat.replies == ["You picked Landlord.", "How much?"]


def test_an_answer_taken_back_is_still_on_record(desk, monkeypatch):
    """Pick Landlord, ask for the list again, then "who did I select last?".
    The field is empty by then; the record still says what it held."""
    seen: list[dict] = []
    real = runtime.interpret_turn
    monkeypatch.setattr(runtime, "interpret_turn", lambda **kw: seen.append(kw) or real(**kw))
    chat, host = _at_the_list(desk)
    chat.submit(payee_id="P-1002")
    host.queue_commands("someone else", Reopen(field="payee_id"))
    chat.user("someone else")
    assert chat.awaiting == "payee_id" and chat.state["fields"].get("payee_id") is None

    host.queue_commands("who did I select last", Reply(text="Landlord."))
    chat.user("who did I select last")
    memory = seen[-1]["memory"]
    assert 'since changed or taken back (oldest first): {"payee_id": ["P-1002"]}' in memory
    assert "Landlord" in memory  # the list it came from, to turn the id into a name


def test_a_flow_that_started_on_this_turn_is_not_restarted(desk):
    """"do that again" after a finished note: the run starts fresh, and the same
    message read against it says Restart. There is nothing to start over."""
    agent, host, _ = desk
    chat = agent.session("note").submit(text="milk").submit(confirm=True)
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="start_skill", skill="note"))
    host.queue_commands("do that again", Restart())
    chat.user("do that again")
    assert chat.replies == ["What should the note say?"]
    assert [e["outcome"] for e in chat.state["journal"]] == ["ended"]


def test_start_over_at_the_first_question_of_a_pinned_flow(desk):
    """A session pinned to a Python flow has no run id until something mints
    one. It is still a running flow."""
    agent, host, _ = desk
    chat = agent.session("errand")
    host.queue_commands("hi")
    chat.user("hi")
    assert chat.awaiting == "kind"
    host.queue_commands("start over", Restart())
    chat.user("start over")
    assert chat.replies[0] == "Okay, starting over." and chat.awaiting == "kind"


def test_what_a_parked_flow_may_hold(desk):
    """No secret, and no approval that has not been used. One that HAS been
    used stays: asking again would be asking to approve something that is done."""
    agent = desk[0]
    pay, trip = agent.registry.get("pay"), agent.registry.get("trip")

    untouched = runtime._for_parking(
        pay,
        {"flow_id": "pay", "flow_run_id": "r1", "say_cursor": 1, "results": {"quote": {"ok": True}},
         "fields": {"payee": "Landlord", "amount": 500.0, "code": "481920", "confirm": True}},
        {},  # nothing has gone through under r1
    )
    assert untouched["fields"] == {"payee": "Landlord", "amount": 500.0}
    assert untouched["results"] == {} and untouched["say_cursor"] == 0

    wrote = runtime._for_parking(
        trip,
        {"flow_id": "trip", "flow_run_id": "r2", "say_cursor": 1,
         "results": {"hold_seat": {"reference": "ST-1"}},
         "fields": {"seat": "12A", "hold": True, "meal": "pasta", "order": True}},
        {"hold_seat:r2": {"reference": "ST-1"}},  # the seat is held; the meal is not ordered
    )
    # hold is spent and stays. order was a yes to something that has not happened:
    # "never mind" took it back.
    assert wrote["fields"] == {"seat": "12A", "hold": True, "meal": "pasta"}
    assert wrote["results"] == {"hold_seat": {"reference": "ST-1"}}

    paid = runtime._for_parking(
        pay,
        {"flow_id": "pay", "flow_run_id": "r3", "say_cursor": 2,
         "results": {"send_payment": {"reference": "PM-1"}},
         "fields": {"payee": "Landlord", "amount": 500.0, "code": "481920", "confirm": True}},
        {"send_payment:r3": {"reference": "PM-1"}},
    )
    assert "code" not in paid["fields"] and paid["fields"]["confirm"] is True

"""Regression tests for the StartFlow / first-turn behavior.

Bug we fixed: when the LLM emitted only a `StartFlow` command (or nothing
useful), the turn would terminate before the flow function ran. The flow
should always re-execute after applying commands, so it can either complete
or pause on the first unfilled slot.
"""
from __future__ import annotations

from aigenos_flow import (
    Chitchat,
    OutOfScope,
    Reply,
    SetField,
    StartFlow,
    initial_state,
)


def test_startflow_alone_still_pauses_on_first_collect(agent, host):
    """If the LLM only emits StartFlow (no SetSlots), the flow should still
    run and pause on the first ctx.collect — the user should see a prompt."""
    state = initial_state()
    # Don't pre-set flow_id — let the executor classify.

    host.queue_commands(
        "file an expense",
        StartFlow(flow_id="file-expense"),
    )
    state["pending_user_message"] = "file an expense"
    state = agent.turn(state)

    assert state.get("awaiting_field") == "amount"
    assert state.get("awaiting_user") is True
    assert state.get("done") is not True
    assert host.drain_pending_messages(), "Expected a collect prompt to have been emitted"


def test_empty_command_list_still_pauses_on_first_collect(agent, host):
    """If the LLM returns no commands at all, the flow should still run and
    pause on the first unfilled slot — not silently complete."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands("file an expense")  # no commands
    state["pending_user_message"] = "file an expense"
    state = agent.turn(state)

    assert state.get("awaiting_field") == "amount"
    assert state.get("awaiting_user") is True


def test_chitchat_then_pause(agent, host):
    """Chitchat is a no-op; flow continues and pauses on the first unfilled slot."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "hello",
        Chitchat(text="hello"),
    )
    state["pending_user_message"] = "hello"
    state = agent.turn(state)

    assert state.get("awaiting_field") == "amount"


def test_outofscope_alone_falls_back_to_skill_description(agent, host):
    """If the LLM classified the turn as OOS but didn't emit a Reply, the
    runtime synthesizes a fallback using the skill description so the user
    still gets a real answer."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "what can you do?",
        OutOfScope(reason="capability_question"),  # internal label, NOT echoed
    )
    state["pending_user_message"] = "what can you do?"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    # Skill description should appear.
    assert any("expense" in m.lower() for m in msgs), msgs
    # Internal reason label must NEVER leak to the user.
    assert not any("capability_question" in m for m in msgs), msgs
    assert state.get("awaiting_field") == "amount"


def test_reply_is_emitted_verbatim(agent, host):
    """When the LLM emits a Reply, the text is shown to the user verbatim."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "hi",
        Reply(text="Hi! Let's file your expense."),
    )
    state["pending_user_message"] = "hi"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert "Hi! Let's file your expense." in msgs, msgs
    # And the flow still proceeds to the first collect.
    assert state.get("awaiting_field") == "amount"


def test_reply_with_oos_replaces_fallback(agent, host):
    """If the LLM emits BOTH OutOfScope AND a Reply, the Reply wins — no
    canned fallback. Runtime trusts the LLM's user-facing text."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "what's the weather?",
        OutOfScope(reason="weather"),
        Reply(text="I can't help with weather, but how much was your expense?"),
    )
    state["pending_user_message"] = "what's the weather?"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert any("weather" in m.lower() and "how much" in m.lower() for m in msgs), msgs
    # The canned "I can help with this: ..." description must NOT also appear.
    assert not any(m.startswith("I can help with this:") for m in msgs), msgs


def test_reply_with_setslot_combination(agent, host):
    """LLM acknowledges the user AND applies a slot in the same turn."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "100",
        Reply(text="Got it."),
        SetField(field="amount", value=100),
    )
    state["pending_user_message"] = "100"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert "Got it." in msgs, msgs
    assert state["fields"]["amount"] == 100.0
    assert state.get("awaiting_field") == "merchant"  # advance past amount


def test_startflow_plus_setslot_in_same_turn(agent, host):
    """LLM combines StartFlow + SetSlot in one turn (common with strong LLMs).
    Both should apply; the flow should continue past amount to the next slot."""
    state = initial_state()

    host.queue_commands(
        "file an expense for $100",
        StartFlow(flow_id="file-expense"),
        SetField(field="amount", value=100),
    )
    state["pending_user_message"] = "file an expense for $100"
    state = agent.turn(state)

    assert state["fields"]["amount"] == 100.0
    assert state.get("awaiting_field") == "merchant"  # amount filled; pause on merchant


def test_invalid_setslot_value_still_pauses(agent, host):
    """If the LLM emits SetSlot with an invalid value (e.g. value=None,
    or amount=-50), the rejection message AND the collect prompt should
    both be emitted. The slot must NOT silently be filled with the bad value."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "file an expense",
        SetField(field="amount", value=None),  # value missing / null
    )
    state["pending_user_message"] = "file an expense"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert any("amount" in m.lower() for m in msgs), msgs  # rejection mentions slot
    assert state.get("awaiting_field") == "amount"  # still asking for it
    assert state.get("fields", {}).get("amount") is None  # NOT set to bad value

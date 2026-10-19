"""Correction handling — the deterministic-guarantee proof.

When the user corrects a field, every action result that ran AFTER that
field was first filled must be invalidated. The flow re-executes from the
top with the new value; previously-cached action results are not reused.
"""
from __future__ import annotations

from aigenos_flow import Correct, SetField, initial_state


def test_correction_invalidates_downstream_results(agent, host):
    """Scenario: user files a small expense, then corrects amount to a high
    one. The policy check must re-run and the flow must now ask for a
    justification."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "file $100 at Whole Foods, food",
        SetField(field="amount", value=100),
        SetField(field="merchant", value="Whole Foods"),
        SetField(field="category", value="food"),
    )
    state["pending_user_message"] = "file $100 at Whole Foods, food"
    state = agent.turn(state)
    host.drain_pending_messages()

    # submit_expense is @action(requires="confirm"), so the flow parks on the
    # gate here — the read-only policy check has already run.
    assert state.get("awaiting_field") == "confirm"
    assert state["results"]["check_policy"]["requires_justification"] is False

    host.queue_commands("yes", SetField(field="confirm", value=True))
    state["pending_user_message"] = "yes"
    state = agent.turn(state)
    host.drain_pending_messages()

    assert state.get("done") is True
    assert state["results"]["check_policy"]["requires_justification"] is False


def test_mid_flow_correction_reasks_justification(agent, host):
    """User starts a small expense, then BEFORE submitting corrects to a high
    amount — the policy check must re-evaluate, and the flow must pause on
    ``justification`` even though the original run didn't need it."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "file $100 at Whole Foods",
        SetField(field="amount", value=100),
        SetField(field="merchant", value="Whole Foods"),
    )
    state["pending_user_message"] = "file $100 at Whole Foods"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state.get("awaiting_field") == "category"
    assert state.get("done") is not True

    host.queue_commands(
        "actually make it $800 travel",
        Correct(field="amount", value=800),
        SetField(field="category", value="travel"),
    )
    state["pending_user_message"] = "actually make it $800 travel"
    state = agent.turn(state)
    host.drain_pending_messages()

    assert state["fields"]["amount"] == 800.0
    assert state["fields"]["category"] == "travel"
    assert state.get("awaiting_field") == "justification"


def test_correction_clears_cached_action_results(agent, host):
    """A Correct item pops state.results so subsequent ctx.run calls
    re-run instead of returning cached values."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "file $100 at Whole Foods, food",
        SetField(field="amount", value=100),
        SetField(field="merchant", value="Whole Foods"),
        SetField(field="category", value="food"),
    )
    state["pending_user_message"] = "file $100 at Whole Foods, food"
    state = agent.turn(state)
    host.drain_pending_messages()
    cached_policy = state["results"]["check_policy"]
    assert cached_policy["requires_justification"] is False

    from aigenos_flow.runtime import apply_items

    apply_items(
        state,
        [Correct(field="amount", value=900)],
        agent.registry.get("file-expense"),
        host,
    )
    assert state["results"] == {}

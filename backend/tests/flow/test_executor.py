"""End-to-end runtime tests using TestHost — no real LLM."""
from __future__ import annotations

from aigenos_flow import SetField, initial_state
from aigenos_flow.testing import Scenario


def test_happy_path_all_slots_in_one_turn(agent):
    """User provides every field in the first message; the write still needs a
    separate human `yes`, so the flow completes on turn two."""
    Scenario(skill="file-expense").turn(
        user="file an expense for $100 at Whole Foods, category food",
        emits=[
            SetField(field="amount", value=100),
            SetField(field="merchant", value="Whole Foods"),
            SetField(field="category", value="food"),
        ],
    ).turn(
        # submit_expense is @action(requires="confirm"): a write may not fire on
        # the turn that proposes it, so the flow parks on the gate first.
        user="yes",
        emits=[SetField(field="confirm", value=True)],
    ).expect(
        slots={
            "amount": 100.0,
            "merchant": "Whole Foods",
            "category": "food",
            "confirm": True,
        },
        done=True,
        last_assistant_contains="Submitted as",
    ).run(agent)


def test_slot_filling_one_at_a_time(agent):
    """User gives values one slot per turn. Flow pauses on each unfilled slot."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    s = (
        Scenario(skill="file-expense")
        .turn(user="file an expense", emits=[])  # triggers the first collect
        .turn(user="$50", emits=[SetField(field="amount", value=50)])
        .turn(user="Whole Foods", emits=[SetField(field="merchant", value="Whole Foods")])
        .turn(user="food", emits=[SetField(field="category", value="food")])
        .turn(user="yes", emits=[SetField(field="confirm", value=True)])
        .expect(
            slots={"amount": 50.0, "merchant": "Whole Foods", "category": "food"},
            done=True,
        )
    )
    s.run(agent, state=state)


def test_policy_violation_asks_for_justification(agent):
    """High amount triggers policy check; flow pauses on justification slot."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    s = (
        Scenario(skill="file-expense")
        .turn(
            user="file $800 at Delta, travel",
            emits=[
                SetField(field="amount", value=800),
                SetField(field="merchant", value="Delta"),
                SetField(field="category", value="travel"),
            ],
        )
        .turn(
            user="client visit to NYC",
            emits=[SetField(field="justification", value="client visit to NYC")],
        )
        .turn(user="yes", emits=[SetField(field="confirm", value=True)])
        .expect(
            slots={
                "amount": 800.0,
                "merchant": "Delta",
                "category": "travel",
                "justification": "client visit to NYC",
            },
            done=True,
        )
    )
    s.run(agent, state=state)


def test_policy_not_violated_skips_justification(agent):
    """Small non-travel expense → no justification asked → flow completes immediately."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    s = (
        Scenario(skill="file-expense")
        .turn(
            user="file $25 at Starbucks, food",
            emits=[
                SetField(field="amount", value=25),
                SetField(field="merchant", value="Starbucks"),
                SetField(field="category", value="food"),
            ],
        )
        .turn(user="yes", emits=[SetField(field="confirm", value=True)])
        .expect(
            slots={"amount": 25.0, "merchant": "Starbucks", "category": "food"},
            done=True,
        )
    )
    final = s.run(agent, state=state)
    # justification field should NOT have been touched.
    assert "justification" not in final.get("fields", {})


def test_field_validation_rejects_negative_amount(agent, host):
    """A SetField with an invalid value is rejected; the field stays unfilled
    and the flow re-asks."""
    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "file -50",
        SetField(field="amount", value=-50),
    )
    state["pending_user_message"] = "file -50"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert any("amount" in m for m in msgs)  # rejection message names the field
    assert state.get("fields", {}).get("amount") is None  # field not set
    assert state.get("awaiting_field") == "amount"  # asks again

"""CI test — the book-meeting-room scenarios, deterministically. No LLM, no API key.

This is the punchline: seven conversation scenarios run in well under a second
with TestHost scripting the extractor. An agent that follows the same rules as
prompt text has no equivalent — you cannot regression-test prompt-following.

The skill under test is 100% declarative: examples/studio_agent/skills/
book-meeting-room/SKILL.md declares the flow, the availability check and the
gated booking write; scripts.py holds only the two tools that talk to the shared
in-memory calendar (../../shared/rooms.py).

Run (from the repo root):
    python -m pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.rooms import list_bookings, reset_bookings  # noqa: E402

from aigenos_flow import Cancel, Correct, FlowAgent, Reply, SetField  # noqa: E402
from aigenos_flow.hosts.test_host import TestHost  # noqa: E402

# Tomorrow, in the shared calendar's demo frame (see shared/rooms.py).
TOMORROW = "2026-05-23"


def _build():
    reset_bookings()  # clean slate — no side effects from prior tests
    host = TestHost()
    skills = Path(__file__).resolve().parents[2] / "skills"
    agent = FlowAgent.from_dir(skills, host=host, mode="flex")
    return agent, host


def _user_bookings() -> list[dict]:
    """The bookings this flow created (the two seeded ones are not ours)."""
    return [b for b in list_bookings() if b.get("title") == "User booking"]


def _collect_redwood(host, *, start="15:00"):
    """Script the extractor for 'book redwood tomorrow at 3pm for an hour'."""
    host.queue_commands(
        "book redwood tomorrow at 3pm for an hour",
        Reply(text="Got it!"),
        SetField(field="room", value="redwood"),
        SetField(field="date", value=TOMORROW),
        SetField(field="start_time", value=start),
        SetField(field="duration_minutes", value=60),
    )


# ---- Scenario A: happy path -------------------------------------------------

def test_scenario_a_happy_path():
    agent, host = _build()
    _collect_redwood(host)
    host.queue_commands("yes", Reply(text="Booking now."), SetField(field="confirm", value=True))

    state = {
        "pending_user_message": "book redwood tomorrow at 3pm for an hour",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    msgs_1 = host.drain_pending_messages()

    # The availability check ran in the runtime, not in the model.
    assert state["results"]["check_room"]["available"] is True
    # The summary quotes what will be booked, then the flow parks on the gate.
    assert any(
        "redwood" in m and TOMORROW in m and "15:00" in m for m in msgs_1
    ), f"expected a booking summary, got {msgs_1}"
    assert state["awaiting_field"] == "confirm"
    assert _user_bookings() == [], "nothing may be written before the user confirms"

    state["pending_user_message"] = "yes"
    state = agent.turn(state)
    msgs_2 = host.drain_pending_messages()

    assert state["done"] is True
    assert state["results"]["book_room"]["booked"] is True
    assert state["results"]["book_room"]["confirmation_id"].startswith("BK-")
    # The user is TOLD it was booked. This flow used to end on a Yes with no
    # reply at all, so the only answer that got an acknowledgement was No.
    assert any(
        m.startswith("Booked: redwood") and "15:00 to 16:00" in m and "Confirmation BK-" in m for m in msgs_2
    ), f"a yes must be answered with what was booked, got {msgs_2}"
    # The side effect actually landed in the shared calendar.
    assert _user_bookings() == [
        {"room": "redwood", "date": TOMORROW, "start": "15:00", "end": "16:00",
         "title": "User booking", "attendees": 1}
    ]


# ---- Scenario B: mid-flow correction ----------------------------------------

def test_scenario_b_correction():
    agent, host = _build()
    _collect_redwood(host)
    host.queue_commands(
        "actually make it 4pm not 3pm",
        Reply(text="Updated to 4pm."),
        Correct(field="start_time", value="16:00"),
    )
    host.queue_commands("yes", Reply(text="Booking now."), SetField(field="confirm", value=True))

    state = {
        "pending_user_message": "book redwood tomorrow at 3pm for an hour",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["awaiting_field"] == "confirm"

    # Turn 2 — correct start_time
    state["pending_user_message"] = "actually make it 4pm not 3pm"
    state = agent.turn(state)
    msgs_2 = host.drain_pending_messages()
    assert state["fields"]["start_time"] == "16:00"
    assert state["fields"]["duration_minutes"] == 60, "duration must NOT change"
    assert state["fields"]["room"] == "redwood", "room must NOT change"
    assert any("16:00" in m for m in msgs_2), "the summary must show the new time"
    # The correction invalidated the memo, so availability was re-checked against
    # the NEW time — 16:00-17:00, not the stale 15:00-16:00.
    assert state["results"]["check_room"]["available"] is True
    assert state["results"]["check_room"]["end_time"] == "17:00"
    # A correction also re-arms the confirmation.
    assert state["awaiting_field"] == "confirm"

    # Turn 3 — confirm
    state["pending_user_message"] = "yes"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["done"] is True
    assert state["results"]["book_room"]["booked"] is True
    assert _user_bookings() == [
        {"room": "redwood", "date": TOMORROW, "start": "16:00", "end": "17:00",
         "title": "User booking", "attendees": 1}
    ], "the corrected time is what got booked"


# ---- Scenario D: missing field — don't assume -------------------------------

def test_scenario_d_missing_date():
    agent, host = _build()

    # User provides 3 of 4 fields — no day.
    host.queue_commands(
        "book redwood at 3pm for an hour",
        Reply(text="Sure! What day?"),
        SetField(field="room", value="redwood"),
        SetField(field="start_time", value="15:00"),
        SetField(field="duration_minutes", value=60),
    )

    state = {
        "pending_user_message": "book redwood at 3pm for an hour",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    host.drain_pending_messages()

    # Flow is waiting on the date field — NOT assuming today.
    assert state["awaiting_field"] == "date", (
        f"expected the flow to ASK for date, not assume it "
        f"(got {state.get('awaiting_field')})"
    )
    assert state["fields"].get("date") is None
    assert "check_room" not in state.get("results", {}), \
        "availability must not be checked against a guessed day"


# ---- Scenario F: cancel mid-flow --------------------------------------------

def test_scenario_f_cancel():
    agent, host = _build()

    host.queue_commands("book a meeting room", Reply(text="Sure, let's do it."))
    host.queue_commands("tomorrow", SetField(field="date", value=TOMORROW))
    host.queue_commands("actually nevermind, cancel", Cancel())

    state = {
        "pending_user_message": "book a meeting room",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["awaiting_field"] == "date"

    state["pending_user_message"] = "tomorrow"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["fields"]["date"] == TOMORROW

    state["pending_user_message"] = "actually nevermind, cancel"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["done"] is True
    assert state.get("flow_id") is None, "flow should be cleared after cancel"
    assert "book_room" not in state.get("results", {}), "no booking should have been made"
    assert _user_bookings() == []


# ---- Scenario G: the user says NO at the gate -------------------------------

def test_scenario_g_declined_at_the_gate():
    """A declined confirmation writes nothing AND says so."""
    agent, host = _build()
    _collect_redwood(host)
    host.queue_commands("no, don't book it", SetField(field="confirm", value=False))

    state = {
        "pending_user_message": "book redwood tomorrow at 3pm for an hour",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["awaiting_field"] == "confirm"

    state["pending_user_message"] = "no, don't book it"
    state = agent.turn(state)
    msgs = host.drain_pending_messages()

    assert state["done"] is True
    assert "book_room" not in state.get("results", {})
    assert _user_bookings() == [], "a declined booking must not be written"
    assert any("no changes were made" in m.lower() for m in msgs), \
        f"the user must be told nothing happened, got {msgs}"


# ---- Scenario H: a MODEL-emitted confirm may not fire the write -------------

def test_scenario_h_model_emitted_confirm_is_dropped():
    """The consent token is a user-only value: the interpreter's own
    SetField(confirm=True) is dropped when the user never said yes."""
    agent, host = _build()
    # One turn in which the extractor fills every field AND answers the
    # confirmation on the user's behalf — the prompt-injection shape.
    host.queue_commands(
        "book redwood tomorrow at 3pm for an hour",
        SetField(field="room", value="redwood"),
        SetField(field="date", value=TOMORROW),
        SetField(field="start_time", value="15:00"),
        SetField(field="duration_minutes", value=60),
        SetField(field="confirm", value=True),
    )

    state = {
        "pending_user_message": "book redwood tomorrow at 3pm for an hour",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    host.drain_pending_messages()

    assert state["awaiting_field"] == "confirm", \
        "the flow must still be waiting for a human yes"
    assert state["fields"].get("confirm") is None, \
        "a model-proposed confirmation must not land in state"
    assert "book_room" not in state.get("results", {})
    assert _user_bookings() == []


# ---- Scenario I: the room is already booked --------------------------------

def test_scenario_i_conflict_recovers():
    """The seeded 10:00-11:00 oak booking blocks the request; the flow explains
    and re-collects instead of writing."""
    agent, host = _build()
    host.queue_commands(
        "book oak tomorrow at 10:00 for an hour",
        SetField(field="room", value="oak"),
        SetField(field="date", value=TOMORROW),
        SetField(field="start_time", value="10:00"),
        SetField(field="duration_minutes", value=60),
    )
    host.queue_commands("11:30 then", SetField(field="start_time", value="11:30"))
    host.queue_commands("yes", SetField(field="confirm", value=True))

    state = {
        "pending_user_message": "book oak tomorrow at 10:00 for an hour",
        "flow_id": "book-meeting-room",
    }
    state = agent.turn(state)
    msgs = host.drain_pending_messages()
    assert any("not free" in m for m in msgs), f"expected a conflict message, got {msgs}"
    assert state["awaiting_field"] == "start_time", "the clashing time is re-collected"
    assert _user_bookings() == []

    state["pending_user_message"] = "11:30 then"
    state = agent.turn(state)
    host.drain_pending_messages()
    # The room they already chose is kept: only the clashing time was asked again.
    assert state["awaiting_field"] == "confirm"
    assert state["fields"]["room"] == "oak"
    assert state["results"]["check_room"]["available"] is True

    state["pending_user_message"] = "yes"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["done"] is True
    assert _user_bookings() == [
        {"room": "oak", "date": TOMORROW, "start": "11:30", "end": "12:30",
         "title": "User booking", "attendees": 1}
    ]

"""Mid-flow StartFlow: push parent, run child, pop, resume parent with
fields intact. Uses book-meeting-room + list-rooms with a scripted
TestHost (no LLM)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _studio_agent_dir() -> Path:
    """examples/studio_agent/ — overridable via env var for CI flexibility."""
    override = os.getenv("FLOW_STUDIO_AGENT_DIR")
    if override:
        return Path(override)
    # backend/tests/flow/test_digression.py → flow → tests → backend → repo root
    return Path(__file__).resolve().parents[3] / "examples" / "studio_agent"


sys.path.insert(0, str(_studio_agent_dir()))

from shared.rooms import reset_bookings  # noqa: E402

from aigenos_flow import (  # noqa: E402
    Cancel,
    FlowAgent,
    Reply,
    SetField,
    StartFlow,
)
from aigenos_flow.hosts.test_host import TestHost  # noqa: E402


def _build_agent() -> tuple[FlowAgent, TestHost]:
    reset_bookings()
    host = TestHost()
    skills = _studio_agent_dir() / "skills"
    agent = FlowAgent.from_dir(skills, host=host, mode="flex")
    return agent, host


# ---------------------------------------------------------------------------
# Single-level digression: mid-flow detour and return.
# ---------------------------------------------------------------------------


def test_digression_preserves_main_flow_fields():
    """User starts booking, asks about rooms mid-flow, returns with date
    and start_time intact."""
    agent, host = _build_agent()

    # Turn 1: start booking and provide date + start_time. Pauses on duration.
    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    host.queue_commands(
        "tomorrow at 3pm",
        SetField(field="date", value="2026-05-23"),
        SetField(field="start_time", value="15:00"),
    )

    # Turn 3: the digression — user asks about rooms. Interpreter emits
    # StartFlow(list_rooms). Runtime pushes the current frame and runs
    # list_rooms to completion; pops back to the main flow.
    host.queue_commands(
        "wait, what rooms are available?",
        StartFlow(flow_id="list-rooms"),
        Reply(text="Sure, here are the rooms."),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["awaiting_field"] == "date"

    state["pending_user_message"] = "tomorrow at 3pm"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["fields"]["date"] == "2026-05-23"
    assert state["fields"]["start_time"] == "15:00"
    assert state["awaiting_field"] == "duration_minutes"

    # ---- the digression ----
    state["pending_user_message"] = "wait, what rooms are available?"
    state = agent.turn(state)
    msgs = host.drain_pending_messages()

    # The digression flow ran: its `ctx.say` output is visible.
    assert any("redwood" in m.lower() for m in msgs), msgs
    assert any("oak" in m.lower() for m in msgs), msgs
    assert any("pine" in m.lower() for m in msgs), msgs

    # And the main flow's fields are intact — push/pop preserved them.
    assert state["fields"]["date"] == "2026-05-23", "date field lost across digression"
    assert state["fields"]["start_time"] == "15:00", "start_time field lost across digression"


def test_digression_pops_back_to_correct_flow():
    """After child completes, flow_id is back to the parent and stack is empty."""
    agent, host = _build_agent()

    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    host.queue_commands(
        "tomorrow",
        SetField(field="date", value="2026-05-23"),
    )
    host.queue_commands(
        "what rooms are there?",
        StartFlow(flow_id="list-rooms"),
        Reply(text="Here:"),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "tomorrow"
    state = agent.turn(state)
    host.drain_pending_messages()
    assert state["awaiting_field"] == "start_time"

    state["pending_user_message"] = "what rooms are there?"
    state = agent.turn(state)
    host.drain_pending_messages()

    # After list_rooms completes, the runtime pops the stack and resumes
    # the parent flow. flow_id must be the main flow again and the stack
    # must be empty.
    assert state["flow_id"] == "book-meeting-room", \
        f"expected to return to book-meeting-room, got {state['flow_id']!r}"
    assert not state.get("flow_stack"), state.get("flow_stack")


def test_digression_then_continue_main_flow():
    """After return, the parent flow advances; doesn't re-ask filled fields."""
    agent, host = _build_agent()

    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    host.queue_commands(
        "tomorrow at 3pm",
        SetField(field="date", value="2026-05-23"),
        SetField(field="start_time", value="15:00"),
    )
    host.queue_commands(
        "what rooms?",
        StartFlow(flow_id="list-rooms"),
        Reply(text="Sure."),
    )
    host.queue_commands(
        "60 minutes redwood",
        SetField(field="duration_minutes", value=60),
        SetField(field="room", value="redwood"),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "tomorrow at 3pm"
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "what rooms?"
    state = agent.turn(state)
    host.drain_pending_messages()

    # Now provide the remaining fields — the main flow should advance to
    # the confirm field, not re-ask anything already set.
    state["pending_user_message"] = "60 minutes redwood"
    state = agent.turn(state)
    host.drain_pending_messages()

    assert state["fields"]["date"] == "2026-05-23"
    assert state["fields"]["start_time"] == "15:00"
    assert state["fields"]["duration_minutes"] == 60
    assert state["fields"]["room"] == "redwood"
    assert state["awaiting_field"] == "confirm", \
        f"expected to advance to confirm, got {state.get('awaiting_field')!r}"


# ---------------------------------------------------------------------------
# Stack discipline: state inspection after explicit push.
# ---------------------------------------------------------------------------


def test_flow_stack_holds_frame_during_digression(monkeypatch):
    """Mid-flow snapshot: pre-StartFlow the stack is empty and date is set."""
    agent, host = _build_agent()

    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    host.queue_commands(
        "tomorrow",
        SetField(field="date", value="2026-05-23"),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()
    state["pending_user_message"] = "tomorrow"
    state = agent.turn(state)
    host.drain_pending_messages()

    # Mid-flow snapshot — pre-digression the stack is empty, fields hold date.
    assert not state.get("flow_stack"), state.get("flow_stack")
    assert state["fields"]["date"] == "2026-05-23"


# ---------------------------------------------------------------------------
# Cancel inside a digression doesn't kill the parent.
# ---------------------------------------------------------------------------


def test_cancel_in_digression_still_returns_to_parent():
    """Cancel inside a child doesn't drop the parent's frame from the stack."""
    agent, host = _build_agent()

    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    host.queue_commands(
        "tomorrow",
        SetField(field="date", value="2026-05-23"),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "tomorrow"
    state = agent.turn(state)
    host.drain_pending_messages()

    # After two turns the main flow is waiting on start_time, with the
    # date field set. This is the deterministic anchor the digression
    # tests above all rely on.
    assert state["fields"]["date"] == "2026-05-23"
    assert state["awaiting_field"] == "start_time"

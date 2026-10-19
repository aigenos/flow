"""The book-meeting-room skill's two tools.

These are the only Python in the skill: the order of the questions, the
availability rule and the confirmation are the steps in SKILL.md. Each
function's parameters are the answers it needs, passed by name. They wrap the
example's shared in-memory room calendar (``examples/studio_agent/shared/rooms.py``)
so the same calendar backs the interactive agent and the CI test.

Why a tool computes the end time: a condition in a skill compares, it does not
calculate, so "start + duration" is done here, next to the calendar it belongs
to.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from aigenos_flow import readonly

# Make the shared fake backend importable (examples/studio_agent/shared/rooms.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from shared.rooms import (  # noqa: E402
    add_minutes,
    book as _book,
    check_availability as _check,
)

Room = Literal["redwood", "oak", "pine"]


@readonly
def check_room(room: Room, date: str, start_time: str, duration_minutes: int) -> dict:
    """Is the room free on that day, from that time, for that long?"""
    end = add_minutes(start_time, duration_minutes)
    result = _check(room, date, start_time, end)
    return {
        "available": bool(result["available"]),
        "reason": result.get("reason") or "the slot is free",
        "end_time": end,
    }


def book_room(room: Room, date: str, start_time: str, duration_minutes: int) -> dict:
    """Book the room.

    ``message`` is the sentence the user is shown, for both outcomes: the slot
    can be taken by someone else between the check and the booking.
    """
    end = add_minutes(start_time, duration_minutes)
    result = _book(room, date, start_time, end, attendees=1)
    booked = bool(result["booked"])
    if booked:
        message = (f"Booked: {room} on {date}, {start_time} to {end}. "
                   f"Confirmation {result.get('confirmation_id')}.")
    else:
        message = f"That did not go through: {result.get('error') or 'the room could not be booked'}. Nothing was booked."
    return {
        "booked": booked,
        "confirmation_id": result.get("confirmation_id") or "",
        "error": result.get("error") or "",
        "end_time": end,
        "message": message,
    }


# Explicit export list: only these two names are tools.
TOOLS = {"check_room": check_room, "book_room": book_room}

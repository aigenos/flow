"""In-memory meeting-room calendar used by both example agents.

Three rooms, two seeded conflicts. Just enough surface that the two agents
have something to disagree on when reasoning about availability and
corrections.

Today (for the demo) is treated as 2026-05-22 (a Friday). "Tomorrow" is
2026-05-23. The seeded bookings sit on that Saturday so a user saying
"book redwood tomorrow at 3pm" hits a real conflict.
"""
from __future__ import annotations

from datetime import datetime, timedelta

ROOMS: dict[str, dict] = {
    "redwood": {"capacity": 8},
    "oak":     {"capacity": 4},
    "pine":    {"capacity": 12},
}

# Seeded existing bookings on Saturday (2026-05-23). Picked so the headline
# demo prompt ("book redwood tomorrow at 3pm") has no conflict, but other
# combinations (oak at 10:30, pine at 13:30, etc.) do — useful for poking
# the agents off the happy path.
_INITIAL_BOOKINGS: list[dict] = [
    {"room": "oak",  "date": "2026-05-23", "start": "10:00", "end": "11:00",
     "title": "1:1"},
    {"room": "pine", "date": "2026-05-23", "start": "13:00", "end": "14:00",
     "title": "All-hands"},
]

_BOOKINGS: list[dict] = list(_INITIAL_BOOKINGS)

# Call counters, for comparing agents on the same backend: how many times an
# agent re-checked availability or attempted a booking. Outcome counts (a
# booking made or not) cannot tell "re-checked after a correction" from
# "silently reused the stale check"; counting here, at the backend, measures
# every agent the same way.
_CALL_COUNTS: dict[str, int] = {"check_availability": 0, "book": 0}


def reset_bookings() -> None:
    """Reset to seeded state. Call between tests so book() side effects
    from one scenario don't leak into the next."""
    _BOOKINGS.clear()
    _BOOKINGS.extend(_INITIAL_BOOKINGS)


def reset_call_counts() -> None:
    _CALL_COUNTS["check_availability"] = 0
    _CALL_COUNTS["book"] = 0


def get_call_counts() -> dict[str, int]:
    return dict(_CALL_COUNTS)


def list_rooms() -> list[dict]:
    return [{"name": n, **info} for n, info in ROOMS.items()]


def check_availability(room: str, date: str, start: str, end: str) -> dict:
    """Return {available: bool, reason?: str}."""
    _CALL_COUNTS["check_availability"] += 1
    if room not in ROOMS:
        return {"available": False, "reason": f"unknown room {room!r}"}
    for b in _BOOKINGS:
        if b["room"] != room or b["date"] != date:
            continue
        if _overlap(start, end, b["start"], b["end"]):
            return {
                "available": False,
                "reason": (
                    f"{room} is booked {b['start']}–{b['end']} "
                    f"for {b['title']!r}"
                ),
            }
    return {"available": True}


def book(room: str, date: str, start: str, end: str, attendees: int = 1) -> dict:
    """Persist a booking if the slot is free. Returns confirmation or error.

    Note: this also adds one to the check_availability count, because it
    calls check_availability itself; the agent's own checks = checks - books.
    """
    _CALL_COUNTS["book"] += 1
    avail = check_availability(room, date, start, end)
    if not avail["available"]:
        return {"booked": False, "error": avail["reason"]}
    _BOOKINGS.append(
        {"room": room, "date": date, "start": start, "end": end,
         "title": "User booking", "attendees": attendees}
    )
    return {"booked": True, "confirmation_id": f"BK-{len(_BOOKINGS):04d}"}


def add_minutes(hhmm: str, minutes: int) -> str:
    """'15:00' + 60 → '16:00'. Helper for both agents."""
    h, m = map(int, hhmm.split(":"))
    dt = datetime(2000, 1, 1, h, m) + timedelta(minutes=int(minutes))
    return dt.strftime("%H:%M")


def list_bookings() -> list[dict]:
    """Return a copy of all current bookings."""
    return list(_BOOKINGS)


def _overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return not (a_end <= b_start or a_start >= b_end)

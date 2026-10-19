"""list_rooms — a tiny read-only digression flow.

It exists so the booking flow can be interrupted mid-collection ("wait,
which rooms are available again?") without losing already-filled fields.
Demonstrates the runtime's push/pop semantics: when this flow completes,
control returns to the suspended booking flow at the field it was waiting
on, with date/time/room/etc. preserved.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.rooms import ROOMS  # noqa: E402

from aigenos_flow import flow  # noqa: E402


@flow
def list_rooms(ctx):
    # Every room there is. It does not look at the calendar, so it must not
    # say "available": it used to, one line after a room had been refused.
    lines = ["Meeting rooms (this list does not check the calendar):"]
    for name, info in ROOMS.items():
        lines.append(f"  • {name} — capacity {info['capacity']}")
    ctx.say("\n".join(lines))

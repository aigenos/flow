"""Tools for table-booking. They sit beside the skill, so only it can name them.

Replace the bodies with calls to your reservation system. Two look things up
(`@readonly`); `book_table` changes something, so the skill cannot run it
without the `confirm:` above it.
"""
from __future__ import annotations

from aigenos_flow import readonly

RESTAURANTS = [
    {"restaurant_id": "trattoria", "name": "Trattoria Roma", "cuisine": "Italian", "area": "Downtown"},
    {"restaurant_id": "sakura", "name": "Sakura", "cuisine": "Japanese", "area": "Harbor"},
    {"restaurant_id": "bistro", "name": "Le Petit Bistro", "cuisine": "French", "area": "Old Town"},
]
#: Times that are already full (a stand-in for the reservation system).
FULL = {("sakura", "19:00"), ("trattoria", "20:00")}
BOOKED: list = []


@readonly
def list_restaurants() -> dict:
    """The restaurants that take bookings."""
    return {"restaurants": [dict(r) for r in RESTAURANTS]}


@readonly
def check_table(restaurant_id: str, day: str, time: str, party_size: int) -> dict:
    """Is there a table at that time, and what would be booked."""
    name = next((r["name"] for r in RESTAURANTS if r["restaurant_id"] == restaurant_id), restaurant_id)
    return {
        "available": (restaurant_id, time) not in FULL,
        "summary": {"Restaurant": name, "Day": day, "Time": time, "Party": f"{party_size} people"},
    }


def book_table(restaurant_id: str, day: str, time: str, party_size: int, card_last4: str = "") -> dict:
    """Book the table."""
    BOOKED.append((restaurant_id, day, time, party_size, card_last4))
    return {"reference": f"TB-{len(BOOKED):04d}"}

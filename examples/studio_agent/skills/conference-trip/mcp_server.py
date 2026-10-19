"""The conference-trip travel services as an MCP server (stdio).

    pip install mcp
    python mcp_server.py

It exposes exactly the functions in ``trip_services.py``, with the same names
the skill's steps use. Hand these tools to the flow with ``tools=`` and the
same SKILL.md runs against MCP instead of the in-process functions — the flow
still decides which tool is called, when, and with which arguments; the MCP
server only ever sees validated values.

Each lookup is annotated ``readOnlyHint``. That is how a flow knows it may run
it freely; a tool without the hint changes something, so the skill cannot run
it without a confirmation above it.

The client side is ``aigenos_flow.mcp_tools.MCPTools``
(``examples/studio_agent/proofs/conference_trip/mcp_tools.py`` wraps it for the proof).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from mcp.types import ToolAnnotations

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ModuleNotFoundError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

#: "This tool only looks something up."
READS = ToolAnnotations(readOnlyHint=True)

_spec = importlib.util.spec_from_file_location(
    "conference_trip_services", Path(__file__).with_name("trip_services.py")
)
svc = importlib.util.module_from_spec(_spec)
sys.modules["conference_trip_services"] = svc
_spec.loader.exec_module(svc)

server = _Server("conference-trip-travel")


# ---- lookups -----------------------------------------------------------------
@server.tool(annotations=READS)
def search_flights(origin: str, conference_date: str) -> dict:
    """Flights from a New York airport to SFO the day before the conference (sample timetable)."""
    return svc.search_flights(origin, conference_date)


@server.tool(annotations=READS)
def check_arrival(flight_id: str, origin: str, conference_date: str) -> dict:
    """Does this flight land by 8 pm the evening before the conference?"""
    return svc.check_arrival(flight_id, origin, conference_date)


@server.tool(annotations=READS)
def find_hotels() -> dict:
    """Hotels nearest Moscone Center, from OpenStreetMap."""
    return svc.find_hotels()


@server.tool(annotations=READS)
def check_hotel(hotel_id: str) -> dict:
    """Is this hotel inside the travel policy (walking distance and nightly rate)?"""
    return svc.check_hotel(hotel_id)


@server.tool(annotations=READS)
def quote_cab(hotel_id: str) -> dict:
    """Real driving route from SFO to the hotel, priced on San Francisco's taxi meter tariff."""
    return svc.quote_cab(hotel_id)


@server.tool(annotations=READS)
def quote_trip(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict:
    """Cost lines and total for the trip, and whether it needs finance approval."""
    return svc.quote_trip(flight_id, origin, conference_date, hotel_id, nights)


@server.tool(annotations=READS)
def verify_approval(approval_code: str) -> dict:
    """Check a finance approval code."""
    return svc.verify_approval(approval_code)


@server.tool(annotations=READS)
def build_itinerary(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict:
    """The itinerary as a list of dated events."""
    return svc.build_itinerary(flight_id, origin, conference_date, hotel_id, nights)


# ---- writes (mock: nothing is reserved, no card is charged) -------------------
@server.tool()
def hold_flight(flight_id: str, origin: str, conference_date: str) -> dict:
    """Hold a seat on the flight (mock)."""
    return svc.hold_flight(flight_id, origin, conference_date)


@server.tool()
def hold_hotel(hotel_id: str, conference_date: str, nights: int) -> dict:
    """Hold the hotel room (mock)."""
    return svc.hold_hotel(hotel_id, conference_date, nights)


@server.tool()
def reserve_cab(hotel_id: str, flight_id: str, conference_date: str) -> dict:
    """Reserve the airport cab (mock)."""
    return svc.reserve_cab(hotel_id, flight_id, conference_date)


@server.tool()
def take_payment(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict:
    """Take payment for the trip (mock: no card is charged)."""
    return svc.take_payment(flight_id, origin, conference_date, hotel_id, nights)


# ---- for the proofs: what did this server actually do? ------------------------
@server.tool(annotations=READS)
def list_bookings() -> dict:
    """Everything this server has booked since it started (mock ledger)."""
    return {"bookings": list(svc.LEDGER)}


if __name__ == "__main__":
    server.run("stdio")

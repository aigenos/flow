"""The conference-trip skill's tools: one function per tool its steps name.

A function's parameters are the answers it needs, and the flow passes them by
name; the model chooses none of them. `@readonly` marks the eight that only
look something up, so the flow may run them whenever a step needs the result.
The other four change something (here, a mock ledger): the skill cannot run
them without the confirmation above them, and each runs at most once.

The work is done by ``trip_services.py`` next to this file, which is also what
``mcp_server.py`` exposes over MCP. So the same SKILL.md runs against these
in-process functions (the default here) or against the MCP server: see
``examples/studio_agent/proofs/conference_trip/prove.py``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from aigenos_flow import Secret, readonly


def _services():
    """One shared instance of trip_services, however many times this file is imported."""
    name = "conference_trip_services"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("trip_services.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


svc = _services()


# ---- lookups ------------------------------------------------------------------
@readonly
def search_flights(origin: str, conference_date: str) -> dict:
    """Flights from a New York airport to SFO the day before the conference."""
    return svc.search_flights(origin, conference_date)


@readonly
def check_arrival(flight_id: str, origin: str, conference_date: str) -> dict:
    """Does this flight land by 8 pm the evening before the conference?"""
    return svc.check_arrival(flight_id, origin, conference_date)


@readonly
def find_hotels() -> dict:
    """Hotels nearest Moscone Center, from OpenStreetMap."""
    return svc.find_hotels()


@readonly
def check_hotel(hotel_id: str) -> dict:
    """Is this hotel inside the travel policy (walking distance and nightly rate)?"""
    return svc.check_hotel(hotel_id)


@readonly
def quote_cab(hotel_id: str) -> dict:
    """A driving route from SFO to the hotel, priced on San Francisco's taxi meter tariff."""
    return svc.quote_cab(hotel_id)


@readonly
def quote_trip(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict:
    """Cost lines and total for the trip, and whether it needs finance approval."""
    return svc.quote_trip(flight_id, origin, conference_date, hotel_id, nights)


@readonly
def verify_approval(approval_code: Secret) -> dict:
    """Check a finance approval code."""
    return svc.verify_approval(approval_code)


@readonly
def build_itinerary(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict:
    """The itinerary as a list of dated events."""
    return svc.build_itinerary(flight_id, origin, conference_date, hotel_id, nights)


# ---- tools that change something (mock: nothing is reserved, no card is charged)
def hold_flight(flight_id: str, origin: str, conference_date: str) -> dict:
    """Hold a seat on the flight."""
    return svc.hold_flight(flight_id, origin, conference_date)


def hold_hotel(hotel_id: str, conference_date: str, nights: int) -> dict:
    """Hold the hotel room."""
    return svc.hold_hotel(hotel_id, conference_date, nights)


def reserve_cab(hotel_id: str, flight_id: str, conference_date: str) -> dict:
    """Reserve the airport cab."""
    return svc.reserve_cab(hotel_id, flight_id, conference_date)


def take_payment(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict:
    """Take payment for the trip."""
    return svc.take_payment(flight_id, origin, conference_date, hotel_id, nights)

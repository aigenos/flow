"""Travel services for the conference-trip skill. Standard library only.

What is real and what is not, because a demo that blurs this is worthless:

  REAL, fetched live (with a bundled snapshot as fallback)
    * hotels      OpenStreetMap, via the Overpass API: every hotel within
                  900 m of Moscone Center, with its name, address and position.
    * cab route   OSRM (OpenStreetMap routing): the driving distance and time
                  from SFO's terminals to the hotel you picked.
    * weather     Open-Meteo: the forecast for San Francisco on arrival day,
                  when that day is inside the forecast window.

  REAL, a published tariff
    * cab fare    San Francisco's taxi meter rates as published by SFMTA
                  (effective 17 Nov 2022). The fare is that formula applied to
                  the real route distance.
    * hotel tax   San Francisco's 14% transient occupancy tax.

  SAMPLE values (no keyless public API offers these)
    * flights     a fixed sample timetable with invented carriers and fares.
    * room rates  a sample nightly rate derived from the hotel's id. It is not
                  the hotel's price.

  MOCK
    * every booking and the payment. Nothing is reserved and no card is charged.

Every function appends to CALL_LOG, so a test — or you — can read back exactly
which service calls a conversation made, and in what order.

Set TRIP_OFFLINE=1 to skip the network and use the snapshot only.
Hotel and route data © OpenStreetMap contributors, ODbL 1.0.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).parent
USER_AGENT = "aigenos-flow-conference-trip-demo/0.3 (+https://github.com/aigenos/flow)"
TIMEOUT_S = 8

#: Moscone Center (South), San Francisco, and the SFO terminal loop.
MOSCONE = (37.7842, -122.4016)
SFO = (37.6164, -122.3861)
HOTEL_RADIUS_M = 900
HOTELS_SHOWN = 8

# ---- Travel policy: the numbers the flow's gates enforce --------------------
LATEST_LANDING = "20:00"        # local time, the evening before the conference
MAX_WALK_MIN = 8                # hotel to Moscone Center
MAX_NIGHTLY_RATE = 400.00
TRIP_BUDGET = 1200.00           # above this a finance approval code is required
#: The demo's approval code. A real system verifies against an identity
#: provider; the point is that the FLOW decides when one is needed.
APPROVAL_CODE = "731942"

# ---- San Francisco taxi meter rates (SFMTA, effective 2022-11-17) -----------
# https://www.sfmta.com/getting-around/taxi/taxi-fares
TAXI_FLAG_DROP = 4.15           # first 1/5 mile
TAXI_PER_FIFTH_MILE = 0.65      # each additional 1/5 mile or fraction
TAXI_SFO_PICKUP_FEE = 6.00
SF_HOTEL_TAX = 0.14             # transient occupancy tax

#: Every service call, in order: (name, args). Read by the proofs and tests.
CALL_LOG: list[tuple[str, dict[str, Any]]] = []
#: Mock bookings. Nothing here leaves the process.
LEDGER: list[dict[str, Any]] = []


def reset() -> None:
    CALL_LOG.clear()
    LEDGER.clear()
    _cache.clear()


def offline() -> bool:
    return os.getenv("TRIP_OFFLINE", "").strip().lower() in {"1", "true", "yes"}


def _log(name: str, **args: Any) -> None:
    CALL_LOG.append((name, args))


# --------------------------------------------------------------------------- #
# Live fetch with a snapshot behind it
# --------------------------------------------------------------------------- #
_cache: dict[str, tuple[Any, str]] = {}


def _snapshot() -> dict[str, Any]:
    path = HERE / "snapshot.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _http_json(url: str, form: dict[str, str] | None = None) -> Any:
    data = urllib.parse.urlencode(form).encode() if form else None
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 — fixed https hosts
        return json.loads(response.read().decode("utf-8"))


def _live_or_snapshot(key: str, fetch: Callable[[], Any], fallback: Callable[[], Any]) -> tuple[Any, str]:
    """``(value, source)`` where source is "live" or "snapshot". Cached per process,
    so one conversation never sees two different answers to the same question."""
    if key in _cache:
        return _cache[key]
    if not offline():
        try:
            value = fetch()
            if value:
                _cache[key] = (value, "live")
                return _cache[key]
        except Exception:  # noqa: BLE001 — any network failure falls back
            pass
    _cache[key] = (fallback(), "snapshot")
    return _cache[key]


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


# --------------------------------------------------------------------------- #
# Flights — SAMPLE timetable (invented carriers, sample fares)
# --------------------------------------------------------------------------- #
#: (id, carrier, departs NY time, flight minutes, stops, base fare)
_TIMETABLE = [
    ("NW101", "Northwind", "06:45", 385, 0, 289.00),
    ("SK204", "Skylark", "09:30", 390, 0, 342.00),
    ("CA318", "Cascadia", "12:15", 505, 1, 238.00),
    ("NW455", "Northwind", "15:20", 392, 0, 367.00),
    ("SK612", "Skylark", "18:40", 388, 0, 254.00),
    ("CA790", "Cascadia", "21:55", 386, 0, 198.00),   # red-eye: lands on conference day
]
_ORIGIN = {  # extra minutes and fare by airport; LaGuardia has no nonstop this far west
    "JFK": (0, 0.00, 0), "EWR": (10, 12.00, 0), "LGA": (95, -18.00, 1),
}


def travel_date(conference_date: str) -> date:
    """You fly the day before the conference starts."""
    return date.fromisoformat(conference_date) - timedelta(days=1)


def _flight(flight_id: str, origin: str, conference_date: str) -> dict[str, Any]:
    fid, carrier, departs, minutes, stops, fare = next(f for f in _TIMETABLE if f[0] == flight_id)
    extra_min, extra_fare, extra_stops = _ORIGIN[origin]
    day = travel_date(conference_date)
    takeoff = datetime.combine(day, datetime.strptime(departs, "%H:%M").time())
    landing = takeoff + timedelta(minutes=minutes + extra_min) - timedelta(hours=3)  # NY -> SF clock
    return {
        "flight_id": fid, "carrier": carrier,
        "departs": f"{origin} {takeoff:%H:%M}",
        "arrives": f"SFO {landing:%H:%M}" + (" (+1 day)" if landing.date() > day else ""),
        "stops": "nonstop" if stops + extra_stops == 0 else f"{stops + extra_stops} stop",
        "fare": round(fare + extra_fare, 2),
        "_landing": landing,
    }


def _real_date(conference_date: str) -> bool:
    """2026-02-30 has the right shape and is not a day."""
    try:
        date.fromisoformat(conference_date)
    except ValueError:
        return False
    return True


def search_flights(origin: str, conference_date: str) -> dict[str, Any]:
    _log("search_flights", origin=origin, conference_date=conference_date)
    if not _real_date(conference_date):
        return {"valid_date": False, "flights": [], "travel_date": "", "data": "sample timetable and fares"}
    rows = [_flight(f[0], origin, conference_date) for f in _TIMETABLE]
    for r in rows:
        r.pop("_landing")
        r["fare"] = f"${r['fare']:.2f}"
    return {"valid_date": True, "flights": rows, "travel_date": travel_date(conference_date).isoformat(),
            "data": "sample timetable and fares"}


def check_arrival(flight_id: str, origin: str, conference_date: str) -> dict[str, Any]:
    """Policy: land by LATEST_LANDING the evening before the conference."""
    _log("check_arrival", flight_id=flight_id, origin=origin, conference_date=conference_date)
    if not any(row[0] == flight_id for row in _TIMETABLE):
        # A typed number that is not on the list ("SK240" for "SK204").
        return {"known": False, "ok": False, "lands": "", "cutoff": ""}
    f = _flight(flight_id, origin, conference_date)
    day = travel_date(conference_date)
    cutoff = datetime.combine(day, datetime.strptime(LATEST_LANDING, "%H:%M").time())
    return {"known": True, "ok": f["_landing"] <= cutoff, "lands": f"{f['_landing']:%a %d %b, %H:%M}",
            "cutoff": f"{cutoff:%a %d %b, %H:%M}"}


# --------------------------------------------------------------------------- #
# Hotels — REAL: OpenStreetMap via Overpass
# --------------------------------------------------------------------------- #
def sample_rate(hotel_id: str) -> float:
    """A SAMPLE nightly rate, stable per hotel id. Not the hotel's price."""
    return 170.0 + (zlib.crc32(hotel_id.encode()) % 12) * 30.0


def _fetch_hotels() -> list[dict[str, Any]]:
    query = (f'[out:json][timeout:20];nwr["tourism"="hotel"](around:{HOTEL_RADIUS_M},'
             f"{MOSCONE[0]},{MOSCONE[1]});out center tags 120;")
    elements = _http_json("https://overpass-api.de/api/interpreter", {"data": query})["elements"]
    hotels = []
    for e in elements:
        tags = e.get("tags") or {}
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if not tags.get("name") or lat is None or lon is None:
            continue
        street = " ".join(x for x in (tags.get("addr:housenumber", "").split(";")[0], tags.get("addr:street", "")) if x)
        hotels.append({"hotel_id": f"{e['type'][0]}{e['id']}", "name": tags["name"], "address": street,
                       "lat": round(lat, 6), "lon": round(lon, 6)})
    return hotels


def _hotels() -> tuple[list[dict[str, Any]], str]:
    hotels, source = _live_or_snapshot("hotels", _fetch_hotels, lambda: _snapshot().get("hotels", []))
    ranked = sorted(hotels, key=lambda h: (_metres(MOSCONE, (h["lat"], h["lon"])), h["hotel_id"]))
    return ranked, source


def _hotel(hotel_id: str) -> dict[str, Any]:
    hotels, _ = _hotels()
    return next(h for h in hotels if h["hotel_id"] == hotel_id)


def _walk(hotel: dict[str, Any]) -> tuple[int, int]:
    metres = round(_metres(MOSCONE, (hotel["lat"], hotel["lon"])))
    return metres, max(1, math.ceil(metres / 80))  # 80 m a minute


def find_hotels() -> dict[str, Any]:
    _log("find_hotels")
    hotels, source = _hotels()
    rows = []
    for h in hotels[:HOTELS_SHOWN]:
        _, minutes = _walk(h)
        rows.append({"hotel": h["name"], "address": h["address"] or "—", "walk": f"{minutes} min",
                     "sample_rate": f"${sample_rate(h['hotel_id']):.0f}", "osm_id": h["hotel_id"]})
    return {"hotels": rows, "found": len(hotels), "source": f"OpenStreetMap ({source})"}


def check_hotel(hotel_id: str) -> dict[str, Any]:
    """Policy: within MAX_WALK_MIN of Moscone Center and at most MAX_NIGHTLY_RATE."""
    _log("check_hotel", hotel_id=hotel_id)
    if not any(h["hotel_id"] == hotel_id for h in _hotels()[0]):
        return {"known": False, "ok": False, "hotel": "", "walk_min": 0, "rate": 0.0}
    h = _hotel(hotel_id)
    _, minutes = _walk(h)
    rate = sample_rate(hotel_id)
    return {"known": True, "ok": minutes <= MAX_WALK_MIN and rate <= MAX_NIGHTLY_RATE, "hotel": h["name"],
            "walk_min": minutes, "rate": rate}


# --------------------------------------------------------------------------- #
# Cab — REAL route (OSRM) priced with the REAL tariff (SFMTA)
# --------------------------------------------------------------------------- #
def taxi_fare(miles: float) -> float:
    """SFMTA meter rates on distance, plus the SFO pickup fee. Waiting time and tip excluded."""
    fifths = max(0, math.ceil(round(miles / 0.2, 6)) - 1)
    return round(TAXI_FLAG_DROP + fifths * TAXI_PER_FIFTH_MILE + TAXI_SFO_PICKUP_FEE, 2)


def _fetch_route(hotel: dict[str, Any]) -> dict[str, float]:
    url = (f"https://router.project-osrm.org/route/v1/driving/"
           f"{SFO[1]},{SFO[0]};{hotel['lon']},{hotel['lat']}?overview=false")
    route = _http_json(url)["routes"][0]
    return {"metres": float(route["distance"]), "seconds": float(route["duration"])}


def _route(hotel_id: str) -> tuple[dict[str, float], str]:
    hotel = _hotel(hotel_id)

    def fallback() -> dict[str, float]:
        saved = (_snapshot().get("routes") or {}).get(hotel_id)
        if saved:
            return saved
        metres = _metres(SFO, (hotel["lat"], hotel["lon"])) * 1.18  # road factor for this corridor
        return {"metres": metres, "seconds": metres / 19.0}

    return _live_or_snapshot(f"route:{hotel_id}", lambda: _fetch_route(hotel), fallback)


def quote_cab(hotel_id: str) -> dict[str, Any]:
    _log("quote_cab", hotel_id=hotel_id)
    route, source = _route(hotel_id)
    miles = route["metres"] / 1609.344
    fare = taxi_fare(miles)
    return {
        "fare": fare,
        "summary": {
            "from": "SFO, terminal pickup",
            "to": _hotel(hotel_id)["name"],
            "distance": f"{miles:.1f} mi ({route['metres'] / 1000:.1f} km)",
            "drive time": f"{round(route['seconds'] / 60)} min without traffic",
            "meter estimate": f"${fare:.2f}",
            "tariff": "SFMTA meter rates + $6.00 SFO pickup fee",
            "route": f"OpenStreetMap routing ({source})",
        },
    }


# --------------------------------------------------------------------------- #
# Weather — REAL: Open-Meteo (only when the day is inside the forecast window)
# --------------------------------------------------------------------------- #
def forecast(day: str) -> str | None:
    def fetch() -> dict[str, Any]:
        url = ("https://api.open-meteo.com/v1/forecast?latitude=37.7842&longitude=-122.4016"
               "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               f"&timezone=America%2FLos_Angeles&start_date={day}&end_date={day}")
        daily = _http_json(url)["daily"]
        return {"max": daily["temperature_2m_max"][0], "min": daily["temperature_2m_min"][0],
                "rain": daily["precipitation_probability_max"][0]}

    value, _ = _live_or_snapshot(f"weather:{day}", fetch, lambda: None)
    if not value or value.get("max") is None:
        return None
    return f"{round(value['min'])}–{round(value['max'])} °C, {value['rain']}% chance of rain (Open-Meteo)"


# --------------------------------------------------------------------------- #
# Pricing, approval, itinerary
# --------------------------------------------------------------------------- #
def _money(x: float) -> str:
    return f"${x:,.2f}"


def _totals(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict[str, float]:
    fare = _flight(flight_id, origin, conference_date)["fare"]
    room = sample_rate(hotel_id) * nights
    tax = round(room * SF_HOTEL_TAX, 2)
    route, _ = _route(hotel_id)
    cab = taxi_fare(route["metres"] / 1609.344)
    return {"flight": fare, "room": room, "tax": tax, "cab": cab, "total": round(fare + room + tax + cab, 2)}


def quote_trip(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict[str, Any]:
    """Every figure is computed here, from validated ids. The model never does arithmetic."""
    _log("quote_trip", flight_id=flight_id, origin=origin, conference_date=conference_date,
         hotel_id=hotel_id, nights=nights)
    t = _totals(flight_id, origin, conference_date, hotel_id, nights)
    hotel = _hotel(hotel_id)["name"]
    return {
        "lines": [
            {"item": f"Flight {flight_id}, {origin} to SFO", "amount": _money(t["flight"])},
            {"item": f"{hotel}, {nights} night{'s' if nights != 1 else ''} at {_money(sample_rate(hotel_id))}",
             "amount": _money(t["room"])},
            {"item": "San Francisco hotel tax (14%)", "amount": _money(t["tax"])},
            {"item": "Cab, SFO to hotel (meter estimate)", "amount": _money(t["cab"])},
            {"item": "TOTAL", "amount": _money(t["total"])},
        ],
        "total": t["total"],
        "total_text": _money(t["total"]),
        "budget_text": _money(TRIP_BUDGET),
        "needs_approval": t["total"] > TRIP_BUDGET,
    }


def verify_approval(code: str) -> dict[str, Any]:
    _log("verify_approval")  # the code itself is never logged
    return {"valid": code == APPROVAL_CODE}


def build_itinerary(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict[str, Any]:
    _log("build_itinerary", flight_id=flight_id, origin=origin, conference_date=conference_date,
         hotel_id=hotel_id, nights=nights)
    f = _flight(flight_id, origin, conference_date)
    hotel = _hotel(hotel_id)
    _, walk_min = _walk(hotel)
    route, _ = _route(hotel_id)
    day = travel_date(conference_date)
    start = date.fromisoformat(conference_date)
    events = [
        {"time": f"{day:%a %d %b}", "title": f"Flight {flight_id} ({f['carrier']})",
         "description": f"{f['departs']} → {f['arrives']}, {f['stops']}"},
        {"time": f"{day:%a %d %b}", "title": "Cab from SFO",
         "description": f"{route['metres'] / 1609.344:.1f} mi, about {round(route['seconds'] / 60)} min to {hotel['name']}"},
        {"time": f"{day:%a %d %b}", "title": f"Check in: {hotel['name']}",
         "description": f"{hotel['address'] or 'San Francisco'} · {nights} night{'s' if nights != 1 else ''}"},
        {"time": f"{start:%a %d %b}", "title": "Innovation AI Conference, Moscone Center",
         "description": f"{walk_min} min walk from the hotel"},
        {"time": f"{start + timedelta(days=nights - 1):%a %d %b}", "title": "Check out",
         "description": "Return travel is not part of this booking"},
    ]
    weather = forecast(day.isoformat())
    if weather:
        events.insert(1, {"time": f"{day:%a %d %b}", "title": "San Francisco on arrival", "description": weather})
    return {"events": events, "flight": f"{flight_id} · {f['departs']} → {f['arrives']}",
            "hotel": f"{hotel['name']} · {nights} night{'s' if nights != 1 else ''}",
            "cab": f"SFO → hotel · {_money(taxi_fare(route['metres'] / 1609.344))} est."}


# --------------------------------------------------------------------------- #
# Bookings and payment — MOCK. Nothing is reserved; no card is charged.
# --------------------------------------------------------------------------- #
def _reference(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:6].upper()
    return f"{prefix}-{digest}"


def _book(kind: str, reference: str, **details: Any) -> dict[str, Any]:
    LEDGER.append({"kind": kind, "reference": reference, **details})
    return {"reference": reference}


def hold_flight(flight_id: str, origin: str, conference_date: str) -> dict[str, Any]:
    _log("hold_flight", flight_id=flight_id, origin=origin, conference_date=conference_date)
    return _book("flight", _reference("FL", flight_id, origin, conference_date), flight_id=flight_id)


def hold_hotel(hotel_id: str, conference_date: str, nights: int) -> dict[str, Any]:
    _log("hold_hotel", hotel_id=hotel_id, conference_date=conference_date, nights=nights)
    return _book("hotel", _reference("HT", hotel_id, conference_date, nights), hotel_id=hotel_id, nights=nights)


def reserve_cab(hotel_id: str, flight_id: str, conference_date: str) -> dict[str, Any]:
    _log("reserve_cab", hotel_id=hotel_id, flight_id=flight_id, conference_date=conference_date)
    return _book("cab", _reference("CB", hotel_id, flight_id, conference_date), hotel_id=hotel_id)


def take_payment(flight_id: str, origin: str, conference_date: str, hotel_id: str, nights: int) -> dict[str, Any]:
    """The amount is recomputed here from the validated ids. Nothing the model said can change it."""
    _log("take_payment", flight_id=flight_id, origin=origin, conference_date=conference_date,
         hotel_id=hotel_id, nights=nights)
    total = _totals(flight_id, origin, conference_date, hotel_id, nights)["total"]
    out = _book("payment", _reference("PAY", flight_id, hotel_id, conference_date, nights, total),
                amount=total, charged=False)
    return {**out, "amount": f"{total:,.2f}"}


# --------------------------------------------------------------------------- #
# Snapshot: `python trip_services.py --snapshot` re-captures the real data
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    if "--snapshot" not in sys.argv:
        sys.exit("usage: python trip_services.py --snapshot")
    hotels = sorted(_fetch_hotels(), key=lambda h: (_metres(MOSCONE, (h["lat"], h["lon"])), h["hotel_id"]))[:40]
    routes = {}
    for h in hotels[:HOTELS_SHOWN]:
        routes[h["hotel_id"]] = _fetch_route(h)
    (HERE / "snapshot.json").write_text(json.dumps({
        "captured": datetime.now().astimezone().isoformat(timespec="seconds"),
        "attribution": "Hotel and route data © OpenStreetMap contributors, ODbL 1.0 "
                       "(https://www.openstreetmap.org/copyright). Hotels via the Overpass API; "
                       "routes via the OSRM demo server.",
        "hotels": hotels,
        "routes": routes,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"snapshot: {len(hotels)} hotels, {len(routes)} routes")

---
name: conference-trip
description: >-
  Plan a trip from New York to the Innovation AI Conference at Moscone Center
  in San Francisco: choose a flight, a hotel within walking distance, a cab
  from the airport, then review the itinerary and pay. Use when the user wants
  to travel to the conference, fly to San Francisco, or book a flight, hotel
  or airport cab for it.
license: Apache-2.0

steps:
  - ask: { origin: "Which New York airport are you leaving from?" }
    choices: [JFK, EWR, LGA]
  - ask: { conference_date: "What day does the conference start? (YYYY-MM-DD)" }
    pattern: '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
    # Not a policy rule: 2026-02-30 has the right shape and is not a day.
    check: { search_flights.valid_date: "That date does not exist. Enter the day the conference starts." }
  - ask: { nights: "How many nights do you need the hotel?" }
    check: { nights >= 1 and nights <= 7: "Choose between 1 and 7 nights." }

  # 1. Flights. The list comes from a tool; the pick is judged by a rule, not by the model.
  - ask: { flight_id: "Which flight? You fly the day before the conference." }
    from: search_flights.flights
    check:
      check_arrival.known: "That is not one of the flights listed. Pick one from the list."
      check_arrival.ok: "That flight lands after 8 pm the night before the conference, which the travel policy does not allow. Pick an earlier one."

  # 2. Hotels: live OpenStreetMap data around Moscone Center.
  - ask: { hotel_id: "Which hotel? These are the closest to Moscone Center." }
    from: find_hotels.hotels
    check:
      check_hotel.known: "That is not one of the hotels listed. Pick one from the list."
      check_hotel.ok: "That hotel is outside the travel policy: over $400 a night, or more than an 8-minute walk. Pick another."

  # 3. Cab: a real route to the hotel just picked, priced on the city's meter tariff.
  - say: "{{quote_cab.summary}}"
    title: Cab from the airport

  # 4. Over budget needs a code, and it has to be the right one.
  - ask: { approval_code: "This trip is over the $1,200 budget. Enter the 6-digit finance approval code." }
    when: quote_trip.needs_approval
    type: secret
    pattern: '^[0-9]{6}$'
    check: { verify_approval.valid: "That approval code is not valid. Ask finance for the current 6-digit code." }

  # 5. The checkout page: itinerary, cost lines, one Pay button.
  - confirm: { pay: "Pay for this trip?" }
    widget: trip_checkout
    with:
      flight: "{{build_itinerary.flight}}"
      hotel: "{{build_itinerary.hotel}}"
      cab: "{{build_itinerary.cab}}"
      events: "{{build_itinerary.events}}"
      lines: "{{quote_trip.lines}}"
      total: "{{quote_trip.total_text}}"

  # 6. Four tools that change something, in this order, each at most once, and only after Pay.
  - do: hold_flight
  - do: hold_hotel
  - do: reserve_cab
  - do: take_payment
  - say: "Booked. Flight {{hold_flight.reference}}, hotel {{hold_hotel.reference}}, cab {{reserve_cab.reference}}. Payment {{take_payment.reference}} for ${{take_payment.amount}}. This is a demo: nothing was reserved and no card was charged."
---

You help the user plan a trip to a conference at Moscone Center in San
Francisco. The steps above are authoritative: they decide what is asked,
which service is called, what the policy allows, and when anything is booked.
This body is only context for understanding what the user says.

# Extraction rules

On every turn, emit a `SetField` for anything the user has clearly stated.

- `origin`: only when the user names a specific New York airport: JFK,
  Newark (EWR) or LaGuardia (LGA). "New York" alone is not an airport — leave
  it unset and the flow will ask.
- `conference_date`: the day the conference STARTS, as YYYY-MM-DD.
- `nights`: only a number the user actually said.

You cannot choose a flight or a hotel for the user, waive the travel policy or
the approval code, change a price, or pay. The flow does each of those itself.

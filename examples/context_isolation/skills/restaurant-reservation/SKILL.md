---
name: restaurant-reservation
description: "Makes a restaurant reservation by collecting the date, time, party size, and seating preference, then submits to the booking system after confirmation. Use when the user says 'make a reservation', 'book a table', or asks for a dinner/lunch spot."
license: Apache-2.0
metadata:
  author: skill-creator
  version: 1.0.0
  source: skill-creator
handlers: ./handlers.py
triggers:
  - "make a reservation"
  - "book a table"
  - "dinner reservations"
  - "lunch spot"
fields:
  date:
    type: string
    description: "Date of the reservation (e.g. 'tomorrow', 'next Saturday', or '2024-03-16')."
  time:
    type: enum
    values: ["morning", "afternoon", "evening"]
    description: "Time of day for the reservation. Map common phrasings: 'brunch'→morning, 'lunch'→afternoon, 'dinner'→evening."
  party_size:
    type: integer
    description: "Number of people in the party (e.g. '2', '4', or '10')."
  seating:
    type: enum
    values: ["inside", "outside", "patio"]
    description: "Preferred seating area for the reservation."
  confirm:
    type: boolean
    description: "Customer confirms the reservation details. Accept yes/y/sure/ok as true."
---

# Restaurant Reservation

Extract date, time, party size, and seating from the initial message if possible. Show a summary before asking for confirmation. After submission, tell the user the reservation ID and ETA.

# Extraction rules

On EVERY user turn, scan the message for values for any unfilled field
and emit a `SetField(field=..., value=...)` for each one you can extract.
Be generous — partial information is better than no information.
Always emit at least one structured item; never reply with text alone
when the user has given any extractable information.

Field map:

- `date` (string): Date of the reservation (e.g. 'tomorrow', 'next Saturday', or '2024-03-16').
  Capture the user's words verbatim (or normalized form) as the value. Even a short phrase is acceptable.
- `time` (enum, one of `morning`, `afternoon`, `evening`): Time of day for the reservation. Map common phrasings: 'brunch'→morning, 'lunch'→afternoon, 'dinner'→evening.
  Map common phrasings to the nearest enum value. If the user's intent is ambiguous, ask once; do not loop.
- `party_size` (integer): Number of people in the party (e.g. '2', '4', or '10').
  Extract the numeric value from phrases like "$45", "forty-five", "five hundred".
- `seating` (enum, one of `inside`, `outside`, `patio`): Preferred seating area for the reservation.
  Map common phrasings to the nearest enum value. If the user's intent is ambiguous, ask once; do not loop.

# Corrections

If the user revises an already-filled field (*"actually make it X instead"*), emit a `Correct(field=..., value=NEW)` — not SetField. Change only the named field; keep all others unchanged.

# Confirmation
After all fields are collected, the flow shows a summary and asks for `confirm`. Map yes/y/sure/ok to true and no/cancel to false.

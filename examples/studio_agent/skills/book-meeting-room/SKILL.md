---
name: book-meeting-room
description: >-
  Reserves a meeting room. Collects the day, start time, duration and room,
  checks the calendar, then asks for an explicit confirmation before writing the
  booking. Use when the user wants to "book a room", "reserve a meeting room",
  "schedule a meeting", "book redwood / oak / pine", or any phrasing that asks
  to CREATE a new meeting-room reservation. Do NOT use for listing existing
  bookings, changing an already-confirmed booking, or questions about which
  rooms exist.
license: MIT
compatibility: Requires the aigenos-flow runtime (the two tools come from scripts.py).
metadata:
  author: aigenos
  version: 3.0.0
  category: scheduling
triggers:
  - "book a meeting room"
  - "book a room"
  - "reserve a room"
  - "reserve the"
  - "book the"
  - "schedule a meeting"
  - "book redwood"
  - "book oak"
  - "book pine"
  - "reserve redwood"
  - "reserve oak"
  - "reserve pine"
  - "book meeting"
  - "reserve meeting"

steps:
  - ask: { date: "Which day is the meeting? (name a day: tomorrow, Monday, May 23rd)" }
    pattern: '^\d{4}-\d{2}-\d{2}$'
  - ask: { start_time: "What time does it start? (24-hour HH:MM, so 3pm is 15:00)" }
    pattern: '^([01][0-9]|2[0-3]):[0-5][0-9]$'
    # Availability is checked by the flow, not by the model. The rule sits on
    # the time, so when the room is taken only the time is asked for again.
    check: { check_room.available: "{{room}} is not free on {{date}} at {{start_time}}. Pick a different start time and I'll check again." }
  - ask: { duration_minutes: "How long, in minutes? (an hour is 60)" }
    check: { duration_minutes >= 15 and duration_minutes <= 480: "A booking is between 15 minutes and 8 hours." }
  - ask: { room: "Which room? Redwood seats 8, Oak 4, Pine 12." }
    choices: [redwood, oak, pine]
  - say: "Ready to book {{room}} on {{date}} at {{start_time}} for {{duration_minutes}} minutes. Nothing is booked yet."
  - confirm: Shall I book it?
  - do: book_room
  # The tool's own sentence says what happened: the slot can be taken by
  # someone else between the check and the booking.
  - say: "{{book_room.message}}"
---

# Book a meeting room

The user wants to reserve a meeting room. Treat the current calendar date (from
the system clock at runtime) as "today" when the user uses relative language
like "tomorrow" or "next Monday". Do NOT hard-code a specific date — read the
current date dynamically and resolve relative references against it.

Available rooms:

- **redwood** — 8 people
- **oak** — 4 people
- **pine** — 12 people

The availability check, the confirmation and the booking are the steps above.
This body is only LLM context for understanding what the user says.

# Extraction rules

If the user says e.g. *"book redwood tomorrow at 3pm for an hour"*, extract all
four fields in one turn. Resolve `tomorrow` against today's actual date and
format as `YYYY-MM-DD`:

- `date` → tomorrow's date in `YYYY-MM-DD` (e.g. if today is 2026-11-04,
  `tomorrow` → `"2026-11-05"`)
- `start_time` → `"15:00"`
- `duration_minutes` → `60`
- `room` → `"redwood"`

# Corrections

If the user corrects a field mid-flow (*"actually make it 4pm not 3pm"*), emit a
`Correct` item — NOT `SetField`. Use the EXACT field name and the NEW value.
Change ONLY the named field; keep every other field unchanged.

- *"actually make it 4pm"* → `Correct(field="start_time", value="16:00")`
- *"make it pine instead"* → `Correct(field="room", value="pine")`
- *"actually 90 minutes"* → `Correct(field="duration_minutes", value=90)`

A correction clears the memoized availability check, so the new time is always
re-checked against the calendar before the confirmation is asked again.

# Confirmation

Once all four fields are filled and the room is free, the flow shows a summary
and asks the user to confirm. Map confirmation responses generously:

- `true`: yes / y / yeah / yep / sure / ok / go ahead / confirmed / do it
- `false`: no / nope / nah / cancel / stop / nevermind

Emit `SetField(field="confirm", value=true|false)` based on the user's reply. Do
NOT emit `Cancel` for "no" on a confirm question — that is a deliberate "no,
don't book" answer, not a flow cancellation.

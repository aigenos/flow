---
name: list-rooms
description: Show the meeting rooms and how many people each one seats. Use when the user asks "which rooms?", "what rooms are available?", "remind me of the options", or any read-only question about which meeting rooms exist. Does NOT book a room — pair with the booking flow for that.
handlers: ./handlers.py
triggers:
  - "which rooms"
  - "what rooms"
  - "available rooms"
  - "list rooms"
  - "show rooms"
  - "my options"
fields: {}
---

# List meeting rooms

The user wants to know which rooms exist. This is a read-only flow — it
does not modify any state. It is routable both from cold start ("what
rooms do you have?") and mid-flow (the user asks the same question during
a booking). In the mid-flow case, the runtime preserves every field the
parent flow had collected and resumes there after this flow completes.

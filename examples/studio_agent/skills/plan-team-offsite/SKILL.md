---
name: plan-team-offsite
description: >-
  File a plan for a team offsite: collect the team, city, dates, headcount and
  total budget, read the plan back, and file it. A budget of $5,000 or more
  waits for finance to approve it. Use when the user wants to plan an offsite,
  team retreat, or team event.

# The whole skill, and there is no Python anywhere in it: questions, two rules,
# a yes from the user, a yes from someone else, and the built-in tool that
# keeps a record. A good one to take apart on the canvas.
steps:
  - ask: { team_name: "Which team is this offsite for?" }
  - ask: { city: "Where should it be?" }
    choices: [austin, lisbon, tokyo, berlin, remote]
  - ask: { start_date: "When does it start? (for example next Monday, or 2026-10-12)" }
  - ask: { attendees: "How many people are coming?" }
    type: whole number
    check:
      attendees >= 2: "An offsite needs at least 2 people."
      attendees <= 60: "This planner handles up to 60 people."
  # A condition compares, it does not calculate: the total is asked for, not
  # multiplied out of a per-person figure.
  - ask: { total_budget: "What is the total budget in USD, for the whole trip?" }
    check: { total_budget >= 0: "Enter a budget of zero or more." }
  - say: "Here's the plan: {{team_name}} in {{city}}, starting {{start_date}}, {{attendees}} people, total budget ${{total_budget}}."
  - confirm: File this plan?
  # Not the user's yes, and not the model's: the flow waits until someone in
  # finance answers on the Approvals page.
  - confirm: Approve this offsite budget?
    by: finance
    when: total_budget >= 5000
  - do: save_request
  - say: "Filed. Reference {{save_request.reference}}."
---

You help file a plan for a team offsite. Collect the team name, city, start
date, number of attendees, and the total budget, then read the plan back. The
steps above are authoritative; this body is only LLM context for
extraction.

# Extraction rules

On EVERY user turn, scan the message for values for any unfilled field and emit
a `SetField(field=..., value=...)` for each one you can extract. Map city
phrasings to the enum ('online'/'virtual' → remote). Extract numbers from
phrases like "12 people" or "$1800 total", and a total budget from "budget of
1800". Do NOT do arithmetic: if the user gives a per-person figure, ask for the
total instead of multiplying. If the user revises a filled field, emit
`Correct(field=..., value=NEW)` instead.

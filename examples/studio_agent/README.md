# studio_agent: the agent the Studio opens

One agent is one folder of skills. This is that folder for the Studio: start
the server from a clone and it loads [`skills/`](skills), so these eight skills
are in the Workspace and one chat can reach any of them.

```
studio_agent/
├── skills/                 the agent: one folder per skill
│   ├── conference-trip/        twelve tools, three gates, a checkout page
│   ├── send-money/             a router: asks one question, hands off to the next two
│   ├── add-payee/              flow 1 of the errand
│   ├── pay-someone/            flow 2 of the errand
│   ├── book-meeting-room/      a gated booking
│   ├── list-rooms/             a lookup, so a digression has somewhere to go
│   ├── refund-desk/            refund window, manager code, confirmation
│   ├── plan-team-offsite/      no Python: a built-in tool, and a yes from finance
│   └── tools/bank.py           the functions the three banking skills name
├── shared/rooms.py         the fake room calendar the two room skills use
└── proofs/                 the same skills, run with no API key
    ├── conference_trip/        prove.py, plus the measured prompt-only baseline
    ├── send_money/             prove.py
    └── book_room/              seven scripted conversations, and a ReAct agent to talk to
```

A proof loads its skill from `skills/`, so there is one `SKILL.md` per skill in
this repo and it is the one the Studio runs.

## Run the proofs

```bash
python examples/studio_agent/proofs/conference_trip/prove.py    # 22 checks (18 without the optional mcp package)
python examples/studio_agent/proofs/send_money/prove.py         # 47 checks
python -m pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py -q    # 7 passed
```

## Open it in the Studio

[Start the Studio](../../README.md#the-studio) with no `FG_SKILLS_DIR` set and
this folder is what it serves. Things to type:

- *"I'm going to the Innovation AI Conference in San Francisco. It starts
  October 15, 2026. I'm flying from JFK and I need 2 nights."*
- *"I need to send some money"*
- *"Book a room for tomorrow at 3pm"*
- *"I need a refund, my headphones arrived broken"*
- *"Plan an offsite for the Platform team in Lisbon, 12 people, total budget
  $6000"*, then open **Approvals** (the inbox icon) in a second tab and answer
  as finance

A skill you create in the Studio is written into `skills/` beside these. Git
ignores every folder there except the eight above and `tools/`, so your own
skills stay out of a commit.

## Use your own folder instead

```bash
FG_SKILLS_DIR=/path/to/your/skills \
  uvicorn aigenos_flow.main:app --port 8000 --app-dir backend
```

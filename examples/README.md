# Examples

One agent is one folder of skills. That is the only rule behind this layout.

## `studio_agent/`: the agent the Studio opens

[`studio_agent/skills`](studio_agent/skills) is the folder the server loads when
you start it from a clone. Its eight skills are one agent: they are all in the
Studio's Workspace, one chat can reach any of them, and a skill can hand the
user to another one because they share a folder.

| Skill | What it shows |
|---|---|
| [`conference-trip`](studio_agent/skills/conference-trip) | twelve tools, three gates, a designed checkout page, in 39 lines of steps |
| [`send-money`](studio_agent/skills/send-money), [`add-payee`](studio_agent/skills/add-payee), [`pay-someone`](studio_agent/skills/pay-someone) | one errand as three flows: a router that branches and hands off to the other two |
| [`book-meeting-room`](studio_agent/skills/book-meeting-room), [`list-rooms`](studio_agent/skills/list-rooms) | a gated booking, and a lookup you can ask about halfway through it |
| [`refund-desk`](studio_agent/skills/refund-desk) | a 30-day refund window, a manager code from $200, an explicit confirmation |
| [`plan-team-offsite`](studio_agent/skills/plan-team-offsite) | no Python at all: it ends on the built-in `save_request`, and a budget of $5,000 or more waits for a yes from finance (`by: finance`) |

Seven of the eight are written as `steps:`; `list-rooms` is a Python `@flow`,
kept as the example of one. The tools the banking skills share are in
[`studio_agent/skills/tools/`](studio_agent/skills/tools), which is where any
skill's tools go.

[`studio_agent/proofs`](studio_agent/proofs) runs those skills with no API key.
Each proof loads its skill from `studio_agent/skills`, so there is one `SKILL.md`
per skill in the repo and it is the one the Studio runs.

| Proof | Command | Result |
|---|---|---|
| [Conference trip](studio_agent/proofs/conference_trip) | `python examples/studio_agent/proofs/conference_trip/prove.py` | 22 checks (18 without the optional `mcp` package) |
| [Send money](studio_agent/proofs/send_money) | `python examples/studio_agent/proofs/send_money/prove.py` | 47 checks |
| [Book a room](studio_agent/proofs/book_room) | `pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py` | 7 passed |

## `gallery/`: the Studio's templates

The Studio's Gallery page installs these into its agent with one click. Each is
a skill written as `steps:`, with its tools beside it in `scripts.py`, so it
installs as one folder.
[`backend/tests/test_gallery_templates.py`](../backend/tests/test_gallery_templates.py)
runs each one as a conversation, with no API key.

| Template | What it shows |
|---|---|
| [`expense-report`](gallery/expense-report) | a `repeat:` that collects the report's lines, a total a lookup works out, and a manager's yes (`by: manager`) from $500 |
| [`subscription-help`](gallery/subscription-help) | a pick from a designed grid, a designed details card (`say:` with `widget:`), then a chat whose lookup reads only the chosen subscription |
| [`table-booking`](gallery/table-booking) | a pick from a list, answers with a shape (`pattern:`), a rule a lookup decides, and a card asked only for parties over 8 |

## Every other folder: one small agent each

Each has its own `skills/` folder, a README, and a script that checks its own
claims with no API key. The Studio does not load these on start.

| Folder | What it shows | Run |
|---|---|---|
| [`before_after/`](before_after) | the same policy as prompt text and as a flow, under the same hostile input | `python examples/before_after/prove.py` (42 checks) |
| [`context_isolation/`](context_isolation) | switching flows never leaks one flow's state into another | `python examples/context_isolation/prove.py` (26 checks) |
| [`declarative_flow/`](declarative_flow) | the long form written out by hand (`fields:` / `actions:` / `flow:`): what `steps:` expands to | `python examples/declarative_flow/prove.py` |
| [`embed_existing_agent/`](embed_existing_agent) | wiring the engine into a LangGraph backend you already run | `python examples/embed_existing_agent/verify_roundtrip.py` |
| [`subscription_insights/`](subscription_insights) | grid, details card, then a chat scoped to one record | `python examples/subscription_insights/prove.py` |
| [`insurance_account_insights/`](insurance_account_insights) | two grids, a validation gate with recovery, scoped chat | `python examples/insurance_account_insights/prove.py` |
| [`widget_pipeline/`](widget_pipeline) | designed widgets, each submit driving the next step | `python examples/widget_pipeline/prove.py` |
| [`expense/`](expense) | a terminal REPL over one skill (needs a model) | `python examples/expense/main.py` |

Four of these folders hold a skill named `transfer-funds`, and `before_after/`
holds two. They are five different skills on the same scenario, each written
to show the one thing its folder is about.

`before_after/`, `gallery/` and the `studio_agent/` skills (`list-rooms` aside)
are written as `steps:`, the form the docs teach. The other folders predate it
and are written in the long form or as Python flows; both still load and run.
[`gallery/subscription-help`](gallery/subscription-help) is the
`subscription_insights/` scenario (a grid, a details card, a chat about one
record) written as steps.

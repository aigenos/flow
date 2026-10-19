# Subscription insights — deterministic widgets + agentic tail

The classic "3-step agent" scenario, as **one SKILL.md and two widget specs —
zero Python agent code**:

1. **"Show subscriptions for ‹first name›"** → a REST/MCP tool returns rows →
   they render as a **single-select a2ui grid** (`subscription_grid`, fixed by
   catalog id — always the same widget).
2. **User selects a row** → the submit fills typed fields (no LLM in that
   path) → a second MCP tool fetches full details → they render as a
   **composed widget** (`subscription_details`): highlights card + badges +
   pending-due alert + billing **grid** + event **timeline** — one catalog id,
   identical layout every run.
3. **Free chat about *that* subscription** — "what's my pending due?", "when
   did I start?" — a scoped agentic leaf where the model picks the tool and
   phrases the answer, but the runtime **pins** `service` from the validated
   selection. Even if the model asks for a different subscription, the pin
   wins.

**Skip-ahead:** if the user already names the service ("show my disney plus
subscription"), the interpreter fills `subscription_id` directly and the
`when_missing:` guards skip both the list call and the grid — the flow
fast-paths to step 2. Same skill, both paths.

## Layout (Anthropic skills spec)

```
skills/subscription-insights/
├── SKILL.md                  fields + actions + flow — the deterministic contract
├── scripts.py                this skill's tools; auth headers live here
└── references/
    └── subscription-api.md   tool shape contract, loaded on demand
widgets/
├── subscription_grid.json    single-select a2ui grid
└── subscription_details.json card + billing grid + timeline, one id
```

`scripts.py` is auto-registered **for this skill only** — no host wiring
needed, and `allowed-tools:` stays the complete audit answer.

## Run the proof

```bash
python prove.py        # no API key needed (scripted interpreter)
```

18 checks: widget ids and bindings per step, LLM-free row submits, the pin
override in chat, the skip-ahead path, a determinism check (identical inputs →
identical widget sequence), and `scripts.py` auto-registration with zero tools
injected.

## Map: scenario → mechanics

| You want | SKILL.md mechanism |
|---|---|
| Query REST/MCP by first name | `actions: list_subscriptions` with `args: {first_name: "{{first_name}}"}` |
| Render THE grid, always the same | `widget: subscription_grid` + `data: {rows: "{{list_subscriptions.subscriptions}}"}` |
| Single-select rows fill state | the widget's `fields:` mapping + `save: [subscription_id, subscription_name]` |
| Second tool gets the *selected* row | `args: {service: "{{subscription_id}}"}` — bound from validated state, never model-filled |
| Card + grid + timeline combo | one composed spec in `subscription_details.json` (a widget is a tree) |
| Free Q&A in context | `chat:` step with `tools:` allowlist + `pin:` |
| Skip straight to details | `subscription_id` as an extractable enum + `when_missing:` on the list/grid steps |

## Going real

- Replace the two mock functions in `prove.py` with your REST client / MCP
  call — the skill doesn't change (`FlowAgent.from_dir(..., tools={...})`).
- In the served Studio app, register the tools through **Connectors** (managed
  operations) and import the two `widgets/*.json` into the catalog via the
  **Widgets** tab (Code mode) or `POST /widgets`. The flow itself is editable
  on the **Canvas** — each `widget:` step's dropdown lists the catalog.

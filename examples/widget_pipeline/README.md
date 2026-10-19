# Multi-turn, heterogeneous, data-bound widgets

Your scenario: **client search (autosuggest form) → accounts (datatable) →
confirmation → MCP call**, where each turn renders a *different* widget,
populated from the previous tool's result, and each submit drives the next step.
All declared in one SKILL.md, no Python.

```
python prove.py    # no API key — proves every turn deterministically
```

The two lookups declare `readonly: true`, so they re-run freely after a
correction. The MCP call is a **write**, so it declares `requires: confirm` and
its `do:` step is guarded by `when: "confirm"` — the flow parks on the gate after
the account is picked and the MCP server is not called until the user says yes.
`prove.py` asserts both halves: nothing fires at turn 3, exactly one call fires at
turn 4, and re-submitting the confirmation does not fire a second one.

## How a widget is tied to a step

A declarative `widget:` step is the rich analog of `ask:` — instead of collecting one
scalar field, it renders a whole widget and collects the fields it `save:`s:

```yaml
- do: search_clients                 # a tool that RETURNS data
- widget: client_picker              # the a2ui widget id your frontend renders
  data:
    options: "{{search_clients.matches}}"   # bound from the tool's result
  save: [client_id, client_full_name]       # the fields its submit fills
```

When the flow reaches this step and those fields aren't filled, the runtime **pauses**
and sets `state["pending_widget"]`:

```jsonc
{
  "widget": "client_picker",                 // which widget to render
  "data":   { "options": [ {"id": "C-001", "full_name": "William Smith"}, … ] },  // bound data
  "fields": ["client_id", "client_full_name"],   // what the submit must return
  "submit_schema": { … }                     // optional JSON Schema for the submit
}
```

It's **replay-safe**: on the turn after the user submits, those fields are filled, so the
step returns immediately and the flow continues to the next `do:` — which binds the new
fields. The 2FA-style determinism guarantees carry over unchanged.

## The round-trip (wire your own a2ui to this)

```
backend pauses ──> state["pending_widget"] = { widget, data, fields, submit_schema }
   your frontend renders widget `pending_widget.widget` with `pending_widget.data`
   user interacts (search+pick / pick a row / …) and submits
   frontend posts { <field>: <value>, … } for each name in `pending_widget.fields`
                    └──> that becomes pending_field_updates (structured, LLM-free)
backend applies them, replays, and renders the NEXT widget (or finishes)
```

So your existing a2ui renderer only needs to: (1) switch on `pending_widget.widget`,
(2) render with `pending_widget.data`, (3) on submit send back the `fields`. Nothing
about the determinism engine changes — the submit is the same structured
`pending_field_updates` path the scalar widgets already use.

## Widget types ↔ primitives

The reference renderer (`frontend/src/renderer/primitives`) now ships `autosuggest`,
`datatable`, and `timeline` (plus the existing `form/input/select/card/…`). A widget is a
tree of these primitives; the rich ones emit hidden form fields for the option/row the
user picks, so one selection can fill several `save:` fields. Reference specs:
`widgets/client_picker.json` (a `form` wrapping an `autosuggest`) and
`widgets/account_table.json` (a `datatable`). If you use your own a2ui components, map
`widget` id → your component instead — the backend contract is identical.

## The 3 "skills" → one declarative flow

You described client_search, find_accounts, and the MCP call as three skills. The cleanest
deterministic shape is **one flow with three phases plus the gate** (this example):
the pipeline is explicit and the data hand-off is just field bindings. If you genuinely need three
independently-reusable skills, the same `widget:`/`do:` steps work and you chain them with
a `StartFlow` hand-off — but prefer one flow unless a phase is reused elsewhere.

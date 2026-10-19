# Docs — start here

**If you read one document, read the [tutorial](GUIDE.md).** Ten minutes, no
API key: a tool, a skill, a run, a test, what the loader refuses, and the
procedure read back. Every file and every output on it is run by the test
suite.

## The path

| Order | Doc | What it is | Time |
|---|---|---|---|
| 1 | **[GUIDE.md](GUIDE.md)** | Tutorial. Build a working workflow from zero and try to break it. | 10 min |
| 2 | [steps.md](steps.md) | The whole language on one page: five kinds of step, lists of items, 20 words, a complete example, tools, a yes from someone else. | 10 min |
| 3 | [cookbook.md](cookbook.md) | Recipes. Nine things to copy and run: a gated write, a list to pick from, a refusal, a code above a threshold, a branch, a hand-off, tests with no model. | 15 min |
| 4 | [developer-guide.md](developer-guide.md) | The hub. Install, the three entry points, tools (a shared folder, MCP), *which word when*, the load-time contract, approvals, persistence, REST API, ship checklist. | 15 min |
| 5 | [skill-authoring.md](skill-authoring.md) | The long form underneath steps: fields, guards, bindings, free-form chat inside a flow. | reference |

## By role

- **Developer embedding into an existing agent or chat app** →
  [integration-patterns.md](integration-patterns.md) (patterns A/B/C), then
  [integration-recipe.md](integration-recipe.md) (the step-by-step for a
  LangGraph + React stack).
- **Non-developer building in the browser** → [user-guide.md](user-guide.md)
  (the Studio, no code).
- **Data owner wiring databases** →
  [managed-connectors.md](managed-connectors.md).
- **Evaluator / reviewer** → [COMPARISON.md](COMPARISON.md) (architecture-class
  comparison + the evaluation rubric), then run the keyless proofs under
  [`examples/`](../examples) — each one asserts its own claims with no API key.
  The developer guide lists all ten and what each pins.
- **Whoever has to sign off a workflow** → run
  `python -m aigenos_flow.procedure skills/ <skill>`, or open **Procedure** on
  the Studio's Canvas: the skill in plain words, every way through it, and what
  must be true before anything changes.
- **"Why not just a prompt?"** →
  [Why structure instead of a better prompt](../README.md#why-structure-instead-of-a-better-prompt)
  in the README.

The live-model comparison (the same twelve tools with and without the flow,
every run included) is
[`examples/studio_agent/proofs/conference_trip/compare.py`](../examples/studio_agent/proofs/conference_trip/compare.py).

Everything else in this folder is media (`media/`).

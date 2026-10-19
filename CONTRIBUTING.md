# Contributing to aigenos / flow

Thanks for the interest. This is a small project — pull requests, issues,
and example skills are all welcome.

## Repo layout

```
aigenos/flow/
├── backend/     pip install aigenos-flow      — the Python package
│   └── aigenos_flow/   flow engine at the root (agent/runtime/interpret/…)
│                       + widget server (agui/ai/integrations, [server] extra)
├── frontend/    npm install @aigenos/flow     — React renderer + Studio app
├── create-app/  npx aigenos-agentic-app       — the scaffold
├── bench/       gate3 policy-gate battery + report.py
├── docs/        the guides rendered at aigenos.io/docs, plus docs/media/
└── examples/    studio_agent (the agent the Studio opens, and its proofs),
                 before_after, context_isolation, declarative_flow,
                 embed_existing_agent, expense, insurance_account_insights,
                 subscription_insights, widget_pipeline
```

Each of the three published artifacts carries its own `LICENSE` (and the npm
renderer a `NOTICE`) so the tarball and the wheel ship the Apache-2.0 text, not
just the metadata claim. If you add a fourth publishable package, copy the root
`LICENSE` into it and make sure its packaging manifest keeps it.

## Quick dev setup

```bash
# Python side — one editable install. [server] adds FastAPI plus the Anthropic
# and Ollama providers; [connectors] the database drivers; [dev] pytest.
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # macOS / Linux
pip install -e "backend[server,connectors,dev]"

# Frontend (optional, only if touching React)
cd frontend && npm install && cd ..
```

## Running tests

```bash
pytest backend/tests          # whole suite
pytest backend/tests/flow     # engine only
```

The test suite must stay green. CI runs it on Python 3.11, 3.12, and 3.13.

There are currently two `xfail`-marked tests in `backend/tests/flow/test_modes.py`.
They script strict mode with `TestHost.queue_commands`, which strict mode does
not read; `TestHost.queue_tool_calls` is the helper that it does read. Rewriting
them with it is a good first contribution — the xfail comment has the context,
though it predates that helper.

## Coding conventions

- **Engine** code: pure Python, no LLM in the control path. Anything that's
  not deterministic should be quarantined to `interpret.py` and clearly
  marked.
- **Backend** code: FastAPI + Pydantic. Keep router signatures small and
  thread skill access through the lazy `_get_agent()` closure.
- **Frontend** code: React + TypeScript + Tailwind. Existing renderer
  primitives live in `frontend/src/renderer/`.

## Adding a new SKILL.md

The SKILL.md format follows the
[Anthropic Agent Skills spec](https://docs.anthropic.com/en/docs/agents-and-tools/agent-skills/overview):

- Folder name: kebab-case (`book-meeting-room`, not `book_meeting_room`)
- `name:` in frontmatter must match folder name
- `description:` says what + when (under 1024 chars, no `<` or `>`)
- No `README.md` inside the skill folder

Flow-engine extensions: `handlers`, `triggers`, `fields`, `flow`, `compute`,
`actions`, `guidelines`, `assist` — see the
[developer guide](docs/developer-guide.md) and
[skill-authoring cookbook](docs/skill-authoring.md).

## Pull request checklist

- [ ] Tests pass locally (`pytest backend/tests`)
- [ ] New behaviour has a test
- [ ] Public API changes are documented in the relevant README
- [ ] No commits include user-generated skills (gitignored under
      `examples/studio_agent/skills/`)
- [ ] No build artifacts committed — `npm pack` tarballs (`*.tgz`), `dist/`,
      `__pycache__/`. They are regenerated at publish time and a committed copy
      is stale the moment the version bumps.
- [ ] No local paths, credentials or retired project names in anything tracked —
      including screenshots and recordings under `docs/media/`, which are the
      one place a stale name survives a rename.
- [ ] Commit messages are clear (imperative mood, why > what)

## License

Apache-2.0 — by contributing you agree your code is released under the same
license as the rest of the project.

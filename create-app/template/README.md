# __APP_NAME__

A deterministic agentic app, scaffolded with `aigenos-agentic-app`. The LLM
understands what your users *say*; your workflow is a list of steps in a skill
file, run by an engine the model cannot talk past. The form in the chat is
generated from the same file — so it stays provably on-script.

```
__APP_NAME__/
├── server/        FastAPI + aigenos/flow   (the deterministic backend)
│   └── skills/    your flows live here  (one SKILL.md each; tools in skills/tools/)
├── web/           React chat + typed widgets + a read-only flow view
└── tests/         zero-LLM flow tests you can run in CI
```

## Run it

Pick the path that matches what you have installed. Each line says exactly what
it needs — nothing is silently magic.

### Developers (you have Python 3.11+ and Node 18+)

```bash
cd __APP_NAME__
npm run dev
```

That's the whole thing. The **first** `npm run dev` does a one-time setup for you:
creates a Python virtual env (`.venv`), installs `server/requirements.txt`, installs
the web deps, and copies `.env.example` → `.env`. The Python install (FastAPI +
LLM providers) is a few hundred MB and takes **~30s–3min on the first run** — you'll
see live progress, it is not frozen. Every run after that boots in a second or two.

Re-provision after a broken install: `npm run dev -- --reinstall` (or delete `.venv`).

### No Python? Use Docker (you only need Docker Desktop)

```bash
cd __APP_NAME__
npm run dev:docker                    # = docker compose up   (needs a key in .env)
docker compose --profile ollama up    # runs a local model instead of using a key
```

Docker does not create `.env` — only `npm run dev` does. Copy `.env.example` to
`.env` and put your key in it before the first `up`.

Needs Docker Desktop installed and running; the first `up` builds images (a few
minutes). The `ollama` profile additionally **downloads the model itself** on
its first run — `qwen3:8b` is about 5 GB — and chat is unavailable until that
finishes. Watch the `ollama` logs for progress; `GET /api/health` tells you what
it is still waiting for, and the UI shows the same thing as a banner. To pick a
smaller model, set `FG_OLLAMA_MODEL` in `.env` (e.g. `FG_OLLAMA_MODEL=qwen3:4b`).

### The LLM key

The app **boots with no key**: the UI comes up, `GET /api/health` returns
`ok: false` with the exact reason, and chat shows that reason as a banner instead
of failing silently. Add **one** key to `.env`:

```
GEMINI_API_KEY=...      # or OPENAI_API_KEY=...  or ANTHROPIC_API_KEY=...
```

…then restart the server (Ctrl-C, then `npm run dev` or `npm run dev:docker`
again). `.env` is read at startup, so reloading the page is not enough.
`FG_MODEL` overrides the model for whichever provider your key selects;
`FG_OLLAMA_MODEL` sets the local model for the keyless path.

| Command | Needs | First-run cost |
|---|---|---|
| `npm run dev` | Python 3.11+ on PATH, Node 18+ | one-time pip install, ~30s–3min |
| `npm run dev:docker` | Docker Desktop running | one-time image build, a few minutes |
| `docker compose --profile ollama up` | Docker Desktop running | the above **plus** a ~5 GB model download |

Once it's up, open **http://localhost:5173** → chat with the sample expense flow.
Try *"file an expense for $600"* and watch the manager-approval gate appear —
then *"$80 for lunch"* and watch it skip. That branch is one line in
`server/skills/submit-expense/SKILL.md` (`when: amount > 500`), not a prompt.
At the confirmation, the chat shows which tool a yes will run and with which
inputs; that comes from the engine, not from the model.

The **Flow** tab reads your own skills back from disk (`GET /api/flow/outline`)
and lists the real steps the engine will run — questions, conditions, tools.
It is a read-only view of `SKILL.md`, not an editor, and it needs no LLM key.
To read a skill back as a document someone can sign off, run
`python -m aigenos_flow.procedure server/skills submit-expense` (with the
`.venv` that `npm run dev` created).

## Add your own flow

Create `server/skills/<your-skill>/SKILL.md` with a `steps:` list. There are
five kinds of step:

| Step | What it does |
|---|---|
| `ask:` | get an answer and keep it under a name |
| `say:` | tell the user something |
| `do:` | run a tool, or another skill |
| `confirm:` | get a yes; "no" stops the flow |
| `chat:` | the last step: answer questions with lookups only |

`when:` makes a step conditional, `check:` says what an answer must satisfy,
and `repeat:` asks the steps under it once per item. That is the language; `server/skills/submit-expense/SKILL.md` uses
most of it, and the
[steps reference](https://github.com/aigenos/flow/blob/main/docs/steps.md) is
one page.

A tool is a Python function in `server/skills/tools/`. Its parameters are the
answers it needs. The server loads skills once, so a new one shows up in chat
after a restart: `npm run dev` runs uvicorn with `--reload`, which restarts
when a `.py` file is saved; after a `SKILL.md`-only edit, or under Docker,
restart it yourself (Ctrl-C and run it again, or
`docker compose restart server`).

Two rules, so read them once:

- A tool **changes something** unless it is marked `@readonly`. A skill that
  names one does not load without a `confirm:` above it, the tool runs at most
  once per conversation, and it runs with the inputs the confirmation showed
  or not at all.
- A misspelled word anywhere in `SKILL.md` is a load error that names the
  nearest valid one, not a silently ignored line. `GET /api/health` reports
  it under `skill_errors`.

## The web UI

`web/` is deliberately small and self-contained: React, `fetch`, and under 300
lines you can read in one sitting. It does **not** depend on the `@aigenos/flow`
npm package — it speaks this server's own `/api/chat` JSON.

When you outgrow it, the published renderer (`npm i @aigenos/flow@0.3.1`) gives
you the full widget library, the style system and AG-UI streaming; pair it with
`make_chat_router` on the server. See the
[integration guide](https://github.com/aigenos/flow/blob/main/docs/integration-patterns.md).

## Test without spending a token

```bash
npm test          # runs pytest with the .venv that `npm run dev` created
```

The tests drive whole conversations through typed answers — no model in the
loop — so your gates are verified deterministically on every commit.

---

Built on [aigenos / flow](https://github.com/aigenos/flow) · Apache-2.0.

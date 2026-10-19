# aigenos-agentic-app

Scaffold a deterministic agentic app in one command.

```bash
npx aigenos-agentic-app my-app
```

You get a runnable project — a chat UI, typed widgets generated from your field
schema, and an [aigenos/flow](https://github.com/aigenos/flow) backend — wired
together so `npm run dev` boots the whole thing:

```
my-app/
├── server/        FastAPI + aigenos/flow   (deterministic backend)
│   └── skills/    your flows (one SKILL.md each; tools in skills/tools/)
├── web/           React chat + typed widgets + a read-only flow view
└── tests/         zero-LLM flow tests for CI
```

The LLM interprets what users say; your workflow is a list of steps in a skill
file (`ask`, `say`, `do`, `confirm`, `chat`), with **gates the model cannot skip**. The
form in the chat is generated from the same file — provably on-script. The
first skill is 12 lines of steps and one Python function.

The generated web app is deliberately self-contained: under 300 lines of React and
`fetch`, no UI framework to learn, so you can read all of it in one sitting.
It does **not** use the `@aigenos/flow` npm renderer. When you want the full
widget library (16 primitives, the style system, AG-UI streaming), add
`@aigenos/flow` to `web/` and mount `make_chat_router` on the server — see
[the integration guide](https://github.com/aigenos/flow/blob/main/docs/integration-patterns.md).

## Usage

```bash
npx aigenos-agentic-app            # scaffolds ./my-agentic-app
npx aigenos-agentic-app billing    # scaffolds ./billing
```

Then:

```bash
cd my-app
npm run dev
```

The first `npm run dev` provisions a Python virtual env, installs the server and
web dependencies, and copies `.env.example` → `.env` for you (one time, ~30 s
to 3 min for the Python packages). No Python installed? `npm run dev:docker`
instead. Either way, open http://localhost:5173.

The UI comes up with no LLM key — chat shows a banner telling you which key to
add, and `GET /api/health` reports `ok: false` with the exact reason until one
is set (a hosted key is not checked against its provider until your first
message). Add **one** key to `.env` (`GEMINI_API_KEY`, `OPENAI_API_KEY` or
`ANTHROPIC_API_KEY`) and restart the server — Ctrl-C, then the same command
again; `.env` is read at startup, so reloading the page is not enough — or run a
local model with [Ollama](https://ollama.com) for a zero-key setup. `npm run dev`
creates `.env` for you; on the Docker path, copy `.env.example` to `.env`
yourself first.

## Requirements

- Node 18+
- Python 3.11+ — or Docker Desktop, for `npm run dev:docker`
- One LLM key (Gemini / OpenAI / Anthropic) — or [Ollama](https://ollama.com)
  with a model pulled, for a fully local setup.

Apache-2.0 · part of [aigenos / flow](https://github.com/aigenos/flow).

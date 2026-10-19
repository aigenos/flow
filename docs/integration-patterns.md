# Integration patterns

Three ways to put aigenos/flow into a stack you already have:

- **A** — widgets only: keep your agent and your chat backend, add
  `render_widget` as a tool
- **B** — deterministic skills inside your agent: `create_agent` for a new one,
  `wrap_agent` for one you already run
- **C** — the bundled backend behind your own React frontend

Pattern B is the one to reach for. A adds UI; only B adds gates.

---

## Pattern A — Widgets only

You already have a chatbot and you don't want a new runtime. You want the
model to **draw a form** instead of asking for fields one at a time in prose.

### Backend

Install the server extra first — `pip install "aigenos-flow[server]"` — because
`aigenos_flow.integrations.langgraph` imports FastAPI and `sse-starlette`. Then
add the `render_widget` tool to your existing LangGraph agent and mount the chat
router:

```python
from fastapi import FastAPI
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from aigenos_flow.integrations.langgraph import (
    make_chat_router,
    render_widget,
    widget_catalog_prompt,
)

agent = create_react_agent(
    model=ChatAnthropic(model="claude-sonnet-4-5"),
    tools=[render_widget, *your_existing_tools],
    prompt=widget_catalog_prompt(),          # tells the model which ids exist
    checkpointer=MemorySaver(),              # required for interrupt/resume
)

app = FastAPI()
app.include_router(make_chat_router(agent), prefix="/chat")
```

That mounts `POST /chat` and `POST /chat/{thread_id}/resume`.

**`render_widget` does not let the model invent UI.** Its signature is
`render_widget(widget_id, props)`: the model picks *which* registered widget
to show and supplies values for the `{{bindings}}` that widget declares.
The spec itself lives in the server-side catalog. Passing a spec object
instead of an id is an error, and an unknown id comes back as
`{"_error": "unknown widget '...'. Known: [...]"}` rather than rendering
anything.

The catalog ships inside the package at `aigenos_flow/data/widgets/*.json`
(`duration_picker`, `order_id_input`, `order_summary`, `refund_confirm`,
`subscription_details`, `subscription_grid`, `trip_checkout`). Set `FG_DATA_DIR` to point reads
and writes at a writable directory of your own — the packaged one may be
read-only. Widgets are then read from `$FG_DATA_DIR/widgets/` only, so copy the
shipped JSON files there if you want to keep them. `widget_catalog_prompt()`
renders the live list, ids and submit schemas included, for your system prompt.

`create_react_agent` is deprecated in LangGraph 1.0 and removed in 2.0, which
is why `aigenos-flow` caps `langgraph` at `<2.0`.

### Frontend

```bash
npm i @aigenos/flow react react-dom
```

**Styling is built in.** Importing a component inside a bundler pulls in the
prebuilt stylesheet, so there is no Tailwind setup and no manual CSS import,
and the package ships no global CSS reset. In plain Node (SSR, unit tests) the
`node` export condition serves a CSS-free build — load
`@aigenos/flow/style.css` yourself there.

```tsx
import { Widget, useFlowChat } from "@aigenos/flow";

export function MyChat() {
  const { messages, send, submitWidget, status } = useFlowChat({
    endpoint: "/api/chat",     // your backend
    protocol: "ag-ui",         // "ag-ui" | "a2ui" | "mcp-ui"
  });

  return (
    <div>
      {messages.map((m) =>
        m.kind === "widget" ? (
          <Widget
            key={m.id}
            spec={m.payload.spec}
            disabled={m.resolved}
            onSubmit={(payload) => submitWidget(m.tool_call_id, payload)}
          />
        ) : (
          <p key={m.id} data-role={m.role}>{m.content}</p>
        ),
      )}
      <input
        disabled={status === "live" || status === "waiting_for_widget"}
        onKeyDown={(e) => {
          if (e.key === "Enter") void send(e.currentTarget.value);
        }}
      />
    </div>
  );
}
```

`useFlowChat` also returns `threadId`, `error` (a string, or `null` when
nothing failed), `runState`, `reset` and `pushUserMessage`. If you want the
whole chat surface instead of the hook, `<Chat endpoint="/api/chat" />` takes
`endpoint`, `protocol`, `skill`, `threadId`, `placeholder`, `emptyState`,
`className`, `validateEndpoint`, `onError` and `voice` (default `true`: a
dictation microphone and a hands-free "Talk" control, shown where the browser
supports speech).

### What this pattern does not give you

Typed `FieldSpec` validation, the deterministic flow runtime, the load-time
verifier, and skill hot-reload all live in the flow engine, not in
`render_widget`. Your own tool logic is responsible for making sense of what a
widget submits. If you want the gates, use Pattern B.

---

## Pattern B — Deterministic skills inside your agent

You want **specific workflows** (a transfer, a refund, an expense) run by the
deterministic runtime — typed fields, structural gates, correction replay —
while your agent keeps handling everything else.

### A new agent

```python
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool

from aigenos_flow import create_agent

@tool
def bank_transfer(amount: float, to_account: str) -> str:
    """Move money. Governed by the transfer-funds skill."""
    ...

agent = create_agent(
    ChatAnthropic(model="claude-sonnet-4-5"),
    "skills/",
    tools=[bank_transfer],
)
```

`tools=` takes a list of LangChain tools or plain callables, **or** a
`{name: callable}` dict, in both entry points. A LangChain tool keeps its own
keyword signature; the flow calls a plain callable with ONE dict of the bound
arguments (`def bank_transfer(args: dict)`). Those tools are the registry a
skill's declarative `actions:` bind to *and* the ReAct loop's tools.

The full signature:

```python
create_agent(llm, skills_dir="skills/", *, tools=None, prompt="", a2ui=True,
             mode="flex", checkpointer=None, host=None,
             governed_tools="flow_only")
```

A `MemorySaver` is installed unless you pass your own `checkpointer`; use a
real one in production.

`skills_dir` must exist and must hold at least one flow-capable skill — one
declaring a `flow:`, or a `handlers.py` `@flow`. Otherwise `create_agent`
**raises** rather than handing back a plain, ungated ReAct agent that merely
looks like the real thing:

```
FileNotFoundError: skills directory not found: no/such/dir
ValueError: no flow-capable skill in /tmp/x: 1 SKILL.md found ('notes'), none
declaring a `flow:` or a handlers.py @flow. Nothing would be gated.
```

### An agent you already run

```python
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent

from aigenos_flow import flow_only, wrap_agent

llm = ChatAnthropic(model="claude-sonnet-4-5")

# A tool a skill governs must not reach the model raw: hand the shim to your
# agent and the real callable to tools=.
my_agent = create_react_agent(llm, tools=[lookup_balance,
                                          flow_only(bank_transfer,
                                                    skill="transfer-funds")])

safe = wrap_agent(
    my_agent,
    skills_dir="skills/",
    llm=llm,
    tools={"bank_transfer": bank_transfer},   # a dict works here too
)
```

`wrap_agent(agent, *, skills_dir, llm=None, tools=None, a2ui=True,
mode="flex", checkpointer=None, host=None, governed_tools="flow_only")`.
Your agent keeps its tools, its prompt and its state; extra keys on its own
state schema are passed through and merged back.

**It does not have to be `create_react_agent`.** Any compiled LangGraph graph
whose state has a `messages` key can be wrapped: your own think / tools /
summarize / respond nodes, your own state keys, your own context trimming
(messages your agent removes are removed in the wrapper too). Two things to
know for a hand-built graph:

- **Async nodes** (an MCP client, an async retriever): drive the wrapped agent
  with `ainvoke` / `astream`, and it delegates to yours the same way. `invoke`
  on an agent whose nodes are async-only raises LangGraph's usual error.
- **Tools called from your own node** cannot be inspected — only a `ToolNode`
  can — so `wrap_agent` warns that it checked nothing. Wrap any tool that does
  the job of a governed write with `flow_only` where your node calls it; the
  model's direct call then returns a refusal instead of running.
- **Tools from LangChain's MCP adapters** can go in `tools=` as they are, the
  same objects your agent uses: a flow step awaits them, reads their
  `readOnlyHint`, and binds their structured result.

**Where the flow runs.** Your whole graph becomes one node of the wrapper:

```
START → entry ─┬─ no flow ──────→ your graph (start → summarize → think ⇄ tools → end) → END
               └─ flow starts ──→ flow ⇄ wait (pauses for the user's answer) ──────────→ END
```

`entry` runs before your `START`, once per user message: one structured call
asks whether the message starts one of the skills. If it does, the turn is the
flow's and none of your nodes run; the flow calls its tools itself (the real
ones, through `tools=`), not through your tools node. While a flow is running,
every turn is the flow's: a question off its topic gets a short reply and the
flow's next question, and "cancel" sets the run aside so your agent has the
conversation until "continue". When the flow finishes, the next message goes to
your graph again, and the flow's questions and answers are in `messages` for
your summarize node to fold like any others; what the flow collected and did is
kept in its own state keys, so folding cannot erase it.

A skill in the same folder with no `steps:`, no `flow:` and no `handlers.py`
stays what it was: instruction-only, never routed by the classifier. Keep your
agent's own skills, Claude skills included, wherever your agent loads them, and
give `skills_dir` a folder of flows.

**After a flow finishes, the conversation is your agent's again.** A follow-up
about what was just done ("did my hotel fit the policy?") goes to your agent,
which has the flow's messages in its history; only a clear request to start a
task routes back into a flow.

### `flow_only` — why the shim is not optional

Classification is a judgement call. On a turn that names no skill, your agent
answers, and any write still in its tool list is reachable with no gates.
`flow_only` closes that path: it returns a drop-in replacement with the same
name, arguments and description that refuses every direct call, while the
flow runtime unwraps it and calls the real tool once the gates pass.

```python
shim = flow_only(bank_transfer, skill="transfer-funds")
shim.invoke({"amount": 1.0, "to_account": "x"})
# "REFUSED: 'bank_transfer' is governed by the 'transfer-funds' flow and
#  cannot be called directly. Nothing happened. Do not tell the user it did …"
```

This is the **default** for `create_agent`, which owns the model-facing tool
list: every governed write is replaced before the model sees it by a shim that
starts the governing flow instead of performing the action.

`wrap_agent` cannot edit a compiled graph's tool list, so it checks instead:

- a tool on your agent whose name a skill governs is an **error** —
  `wrap_agent: the wrapped agent can still call 'bank_transfer' (governed by
  skill 'transfer-funds') itself…`
- a tool nothing can vouch for is a **warning**: nothing can tell that your
  `transfer_money` is the same job as the skill's `bank_transfer`. Wrap those
  with `flow_only` too.

`governed_tools` takes exactly two values: `"flow_only"` (the default) and
`"allow"`, which hands the raw tool to the model and keeps the ungated path.
Anything else raises `ValueError: governed_tools must be 'flow_only' or
'allow'`.

`governed_write_tools(registry)` returns the `{tool name: skill name}` map the
checks are built on, so you can assert on it in your own tests. A tool counts
as a write unless the skill names it on its `readonly-tools:` line. In 0.3.1 an
action's own `readonly: true` does not exempt its tool here, so list a lookup on
`readonly-tools:` as well if your agent should keep calling it directly.

### What you get

- **General Q&A** — your ReAct loop, your tools, unchanged
- **Structured workflows** — typed fields, gates, replayed order
- **Mid-conversation digression** — "hold on, what's the refund policy?" runs
  the other skill and comes back with the collected fields intact
- **One checkpointer** across both modes, so `flow_run_id` and the effects log
  survive flow switches, digressions and cancellation: one confirmed intent
  fires exactly one side effect

### Serving it

`make_chat_router` serves an agent from `create_agent` / `wrap_agent` (it needs
the `[server]` extra, as in Pattern A):

```python
from aigenos_flow.integrations.langgraph import make_chat_router

app.include_router(make_chat_router(agent), prefix="/chat")
```

When the flow pauses to collect a field, the widget resolved for that field is
streamed as a `render_widget` tool call, so an AG-UI / A2UI / MCP-UI client
renders it straight from the stream.

---

## Pattern C — Bundled backend, your frontend

You want the whole stack but with **your own React UI and design system**.

### Backend

```bash
pip install -e "backend[server,ollama]"
FG_SKILLS_DIR=examples/studio_agent/skills \
  uvicorn aigenos_flow.main:app --port 8000 --app-dir backend
```

`/flow` serves the folder `FG_SKILLS_DIR` names. Without it the server uses
the monorepo's default skills dir, or outside a checkout an empty `skills`
folder it creates under the data dir. Point it at a folder that does not exist
and startup fails loudly rather than serving a stack of missing routes.
`GET /health` answers `{"status":"ok","protocols":["ag-ui","a2ui","mcp-ui"]}`.

Or build and run the images yourself — `Dockerfile.backend` and
`Dockerfile.frontend` are at the repo root, and `docker-compose.yml` wires
backend + frontend + redis (`docker compose --profile ollama up` adds a local
Ollama). Nothing is published to a registry; `docker compose up` builds. That
stack runs skills and lets you chat with them; Studio authoring is refused in
it (see [Deployment](#deployment)).

The `[server]` extra is self-sufficient: it pulls `langchain-anthropic` and
`langchain-ollama`, so a keyless install can still serve a turn against local
Ollama. For OpenAI or Gemini add that provider's extra — without it the chat
routes answer 503 naming the extra, never a 500.

The service exposes:

| Endpoint | Purpose |
|---|---|
| `POST /flow?protocol=ag-ui` | Start a chat turn |
| `POST /flow/{thread_id}/resume?protocol=ag-ui` | Resume after a widget submission |
| `GET /flow/skills` | Loaded skills + their FieldSpecs |
| `GET /flow/skills/{name}/outline` | The flow as structured steps |
| `GET /flow/skills/{name}/guarantees` | What the verifier proved about a skill |
| `GET /flow/llm-info` | Active LLM provider + model, for UI badges |
| `POST /flow/validate-field` | Validate one field value, server-side |
| `GET /flow/gallery` | The templates that ship in the repository's `examples/gallery/` folder, each with how many nodes it draws on the Canvas and how many guarantees hold (empty for a pip install) |

Protocol is `ag-ui` (default), `a2ui` or `mcp-ui`.

**The write surface serves loopback only by default.** `PUT /flow/skills/{name}`,
`DELETE /flow/skills/{name}`, `POST /flow/create-skill`,
`POST /flow/preview-skill`, `POST /flow/reload`,
`POST /flow/gallery/{name}/install`, `POST /flow/skills/{name}/restore/{v}`,
the widget and workflow writes, and `GET /flow/skills/{name}/files` (it
returns raw `handlers.py` source) answer **403 to any request from another
host**, because a skill can carry Python the engine executes. `FG_SKILL_WRITES`
takes `local` (the default: loopback clients only, from an allowed origin),
`token` (needs `FG_ADMIN_TOKEN`, sent as `Authorization: Bearer …` or
`X-Admin-Token: …`), `off` (closed to everyone, for serving chat publicly) or
`open` (no check, warned about on every startup and every request).
Setting `FG_ADMIN_TOKEN` or `FG_CONNECTOR_ADMIN_TOKEN` implies `token`.
In 0.3.1 the Studio UI does not send an admin token, so token mode is for API
and CI clients; use the default local mode to author in the Studio.
`python -m aigenos_flow.main` binds `127.0.0.1` and refuses a public bind
while writes are ungated. CORS defaults to the local dev origins
(`FG_CORS_ORIGINS` to change it), never `*`.

### Your own renderer

`useFlowChat` returns a normalized `ChatMessage[]`, identical across all three
protocols. Recurse over `m.payload.spec` with your own components:

```tsx
import type { ChatMessage, SpecNode } from "@aigenos/flow";
import { useFlowChat } from "@aigenos/flow";

export function MyFlowChat() {
  const { messages, send, submitWidget } = useFlowChat({ endpoint: "/api/flow" });

  const render = (m: ChatMessage) => {
    if (m.kind === "text") {
      return <Bubble key={m.id} role={m.role}>{m.content}</Bubble>;
    }
    return (
      <MyWidget
        key={m.id}
        spec={m.payload.spec}
        submitSchema={m.payload.submit_schema}
        disabled={m.resolved}
        onSubmit={(payload) => submitWidget(m.tool_call_id, payload)}
      />
    );
  };

  return <>{messages.map(render)}</>;
}
```

A `SpecNode` is `{type, props, children}`. The bundled renderer covers 16
primitives — `alert`, `autosuggest`, `badge`, `button`, `card`, `column`,
`datatable`, `divider`, `form`, `image`, `input`, `loading`, `row`, `select`,
`text`, `timeline` — exported as `PRIMITIVES` so you can swap one out and keep
the rest. Inline per-field validation is opt-in through `validateEndpoint` or
`FlowConfigProvider`; off by default the primitives make no requests of their
own.

The hook's old pre-0.3.1 names still resolve as deprecated aliases and are
removed in 0.4; your editor flags them, so rename to `useFlowChat`,
`UseFlowChatOptions` and `UseFlowChatReturn`.

---

## Pointing a skill at a custom widget

By default a pause on a field auto-generates a form from that field's
`FieldSpec`. To render a widget from the catalog instead, name it in the field:

```yaml
fields:
  duration_minutes:
    type: integer
    min: 15
    max: 480
    widget: duration_picker
    description: Meeting length in minutes.
```

Resolution order at pause time, as the bundled `/flow` router applies it:

1. the widget the runtime already built for this pause — what a `pick:` or
   `widget:` step produces
2. `field.widget`
3. `skill.widget` (the skill-level fallback)
4. auto-generated from the `FieldSpec`

An id that is not in the catalog logs a warning and falls back to
auto-generation; a bad reference never breaks the flow.

The same order applies to an agent built by `create_agent` / `wrap_agent`: the
pause's `widget` (and `aigenos_widget` on the paused message) is the grid or
catalog widget a `pick:` or `widget:` step built.

### Bindings

A widget's `bindings` list names the `{{var}}` placeholders its string props
use. They resolve against the fields collected so far:

```json
{
  "id": "duration_picker",
  "bindings": ["date", "start_time", "room"],
  "spec": {
    "type": "card",
    "props": { "title": "How long?", "subtitle": "Booking {{room}} on {{date}}" }
  }
}
```

A placeholder whose field is not collected yet renders empty, so bind only
fields the flow has already asked for.

### Submissions are validated, not trusted

The widget's `submit_schema` names what the form posts. Match the property
names to the field names and the submission lands as typed field updates — no
LLM in that path:

```json
"submit_schema": {
  "properties": { "duration_minutes": { "type": "string", "enum": ["15", "30", "60"] } }
}
```

`min` / `max`, `pattern`, enum `values` and the type boundary still apply.
(`must_elicit` is not part of this path: it guards values the model extracts
from free text, and a submission never goes through the model.)
Submitting `5` against `min: 15` gets:

```
That duration minutes is too low: the minimum is 15.
Meeting length in minutes.
```

— the rejection, then the re-ask, with the flow still awaiting that field. A
submitted key that is not a declared field of the active skill is dropped with
a `logging.warning` rather than stored, so a typo shows up as a flow that
keeps asking the same question. Watch your logs when wiring a new widget.

### Authoring widgets

Write the JSON into your widget directory, or design it in the Studio's
Widgets surface. Either way a skill picks it up at its next pause — no backend
restart. (Pattern A's `render_widget` and `widget_catalog_prompt()` read a
catalog loaded once at import: a file you wrote by hand reaches them after a
restart or a call to `aigenos_flow.engine.runtime.reload_assets()`.) The
Studio's save path is a write endpoint: it works from your own machine. In
0.3.1 the Studio UI does not send an admin token, so token mode is for API and
CI clients; use the default local mode to author in the Studio (see Pattern C).

---

## Deployment

### Images

`Dockerfile.backend` and `Dockerfile.frontend` are multi-stage and run as
non-root — UID 1001 for the backend, UID 101 for the nginx frontend (listening
on 8080) — which fits OpenShift's `restricted-v2` SCC. `docker build -f
Dockerfile.backend .` and `docker build -f Dockerfile.frontend .` from the repo
root produce them; `docker compose up` builds both and wires them to Redis.
The backend image installs `backend[server,ollama,connectors]` plus the `redis`
client.

The compose stack runs skills and lets you chat with them. Opening a skill on
the Canvas, Create and Save are refused there: those requests reach the backend
from the container network, not from loopback, so the default local mode
answers 403. Author in the local dev setup.

On OpenShift: run as a UID in the project's SCC range, keep the root
filesystem read-only and mount an `emptyDir` for temp writes, mount a writable
volume at `/app/data` (the connector store is created there at startup, and the
server does not start if it cannot write it), mount your skills folder as a
`PersistentVolumeClaim` so skills saved through the API survive a pod restart,
and terminate TLS with an `edge` or `reencrypt` Route.
There is no Helm chart and no OpenShift manifest in the repo yet.

### Conversation persistence

**Experimental in 0.3.1.** The bundled server keeps conversations in memory and
loses them on restart. The `FG_TRACKER_STORE_URL` path below has not been
verified against current `langgraph-checkpoint-*` releases; for durable state
use `create_agent` / `wrap_agent` with your own `checkpointer=`.

The bundled server picks a LangGraph checkpointer from
`FG_TRACKER_STORE_URL`:

| URL prefix | Backend | Needs |
|---|---|---|
| `redis://…` / `rediss://…` | Redis | `pip install langgraph-checkpoint-redis` |
| `postgresql://…` / `postgres://…` | Postgres | `pip install langgraph-checkpoint-postgres` |
| `sqlite:///path/to/db` | SQLite | `pip install langgraph-checkpoint-sqlite` |
| `mongodb://…` / `mongodb+srv://…` | MongoDB (community) | `pip install langgraph-checkpoint-mongodb` |
| (unset) | In-memory | nothing — conversations lost on restart |

**None of those checkpointer packages is installed by any extra or by
`Dockerfile.backend`.** If the import fails the server logs a warning and
falls back to `MemorySaver`, so a `FG_TRACKER_STORE_URL` you set without
installing its package looks configured and persists nothing. Install it
explicitly and check the startup line: `[flow] tracker store: redis @ …`.

`create_agent` / `wrap_agent` do not read this variable — pass your own
`checkpointer=`.

---

## Migrating workflows

### From a hand-coded ReAct agent

Find the steps where the model invents field values or forgets required ones.
Move those into a skill; keep the free-form parts in the loop. Use Pattern B.

### From a slot-based dialogue framework

Each existing flow becomes one `SKILL.md` (plus `handlers.py` or `scripts.py`
if it needs Python):

| Slot/flow framework concept | aigenos flow |
|---|---|
| Flow definition (YAML/DSL) | `SKILL.md` frontmatter `fields:` + `flow:` |
| Collect-a-slot step | `- ask: field_name` |
| Action step | `- do: action_name`, guarded by a `when:` |
| Link to another flow | `- link: other-skill` (or `ctx.link("other-skill")` in a Python `@flow`) |
| Conditional branching | a step-level `when:` / `check:` |
| Built-in cancel pattern | the `Cancel` interpretation command |
| Built-in correction pattern | the `Correct` interpretation command |
| External action service (separate process) | a tool in `tools=`, same process |

Start with a workflow that has three to five fields and one happy path.

A few load-time rules bite on the first migration. Unknown keys anywhere in
`SKILL.md` are load errors. Every declared action is a write unless it says
`readonly: true`, and a write needs `requires:` naming a declared boolean
field, keeps `audit: true`, and has its `do:` step guarded by a `when:` that
proves the confirmation. `prompt:` on an `ask:` step is a load error — the
wording comes from the field's `description:`. `compute:` is comparison-only,
with no arithmetic. There is no `default:` on a field.

### From a visual bot builder

There is no automated converter. File an issue if your team would use one.

---

## Where to file issues

Tag with `engine` (runtime, interpretation, skill loading), `backend` (router,
protocol streaming), `frontend` (widgets, hook, editor), `integration` (any of
the three patterns) or `deployment` (Docker, K8s, OCP). The contributing guide
is [`CONTRIBUTING.md`](../CONTRIBUTING.md) at the repo root.

# Integration recipe — LangGraph + a React chat UI you already have

The step-by-step for the common case: a FastAPI service hosting one or more
LangGraph agents, your own React chat UI, and multi-step widget flows mixed
into free-form conversation over AG-UI, A2UI or MCP-UI.

**Start with the wrapper.** `create_agent` and `wrap_agent` do the routing,
the interrupts, the widget resolution and the checkpointing for you, and they
are what the rest of this page assumes:

```python
from aigenos_flow import wrap_agent

safe_agent = wrap_agent(my_existing_agent, skills_dir="skills/",
                        llm=llm, tools=[bank_transfer])
```

Your agent keeps its tools, its prompt and its state. See
[integration-patterns.md](integration-patterns.md) for the full signatures,
`flow_only`, and the three entry points side by side. Hand-wiring your own
router is the fallback — §8 covers when that is the right call, and
[`examples/embed_existing_agent/`](../examples/embed_existing_agent/) is the
runnable version with no LLM in the loop.

---

## 1. Know what widgets you have

Widgets are JSON specs in the widget directory — `aigenos_flow/data/widgets/`
inside the installed package, or the `widgets/` folder under `FG_DATA_DIR` when
that is set. Point `FG_DATA_DIR` at a writable directory you control (the
packaged one may be read-only) and copy the shipped `widgets/*.json` into its
`widgets/` folder if you want to keep them: a fresh directory starts with an
empty catalog.

The catalog that ships is `duration_picker`, `order_id_input`,
`order_summary`, `refund_confirm`, `subscription_details`,
`subscription_grid` and `trip_checkout`. Read the live list at runtime rather than hardcoding it
(this module needs the server extra — `pip install "aigenos-flow[server]"` —
because it imports FastAPI and `sse-starlette`):

```python
from aigenos_flow.integrations.langgraph import widget_catalog_prompt
print(widget_catalog_prompt())
```

A widget is an `id`, a `version`, a `description`, a `bindings` list (which
`{{placeholders}}` its props use), a `submit_schema` (what the form posts) and
a `spec` tree:

```json
{
  "id": "expense_basics",
  "version": "1.0.0",
  "description": "Asks for expense amount and merchant.",
  "bindings": [],
  "submit_schema": {
    "type": "object",
    "properties": {
      "amount": {"type": "number"},
      "merchant": {"type": "string"}
    },
    "required": ["amount", "merchant"]
  },
  "spec": {
    "type": "card",
    "props": {"title": "Expense details"},
    "children": [{
      "type": "form",
      "props": {"submitLabel": "Continue"},
      "children": [
        {"type": "input", "props": {"name": "amount", "type": "number", "label": "Amount (USD)", "required": true}},
        {"type": "input", "props": {"name": "merchant", "label": "Merchant", "required": true}}
      ]
    }]
  }
}
```

Drop that in the widget directory. `render_widget` and
`widget_catalog_prompt()` read a catalog that is loaded once at import, so a new
file is picked up on restart, or after you call
`aigenos_flow.engine.runtime.reload_assets()` and rebuild the prompt. On the
bundled server (`aigenos_flow.main`) `POST /widgets/reload` does the rescan
without a restart — but it is a write endpoint, so it serves loopback only until
`FG_SKILL_WRITES` says otherwise (see §7).

## 2. Wire `render_widget` into your agent

```python
from langgraph.prebuilt import create_react_agent       # or your own graph
from langgraph.checkpoint.memory import MemorySaver     # Sqlite/Postgres in prod
from aigenos_flow.integrations.langgraph import render_widget, widget_catalog_prompt

def build_agent(cfg):
    return create_react_agent(
        model=cfg.model,
        tools=[render_widget, *cfg.tools],
        prompt=f"{cfg.prompt}\n\n{widget_catalog_prompt()}",
        checkpointer=MemorySaver(),
    )
```

Three lines: add `render_widget` to `tools`, append `widget_catalog_prompt()`
so the model knows which ids are valid, and set a `checkpointer` —
`interrupt()` has nowhere to persist state without one.

**The model picks a widget, not a spec.** `render_widget(widget_id, props)`
takes the id of a registered widget plus values for the `{{bindings}}` that
widget declares. The spec lives in the server-side catalog and the model never
authors markup. Passing a spec object instead of an id is an error; an unknown
id returns `{"_error": "unknown widget '…'. Known: […]"}` and renders nothing.

This gets you widgets. It does **not** get you typed field validation or the
flow's gates — for those the tool list is not the integration point, the
wrapper is. Use `create_agent` / `wrap_agent`.

## 3. Mount the router on FastAPI

```python
from aigenos_flow.integrations.langgraph import make_chat_router

app.include_router(make_chat_router(your_agent), prefix="/chat")
```

That gives you:

- `POST /chat[?protocol=ag-ui|a2ui|mcp-ui]` — start or continue a turn
- `POST /chat/{thread_id}/resume` — deliver a widget submission

The body is a `ChatRequest`: `thread_id`, `message`, `resume_payload`, `skill`.
It is `extra="forbid"`, so a drifted body fails loudly instead of silently
dropping a field:

| Request | Status |
|---|---|
| an unknown body key | `422` |
| neither `message` nor `resume_payload` | `400` |
| `?protocol=` something unsupported | `400` |
| `skill=` on a graph with no flow state | `400` |
| a normal `ag-ui` turn | `200`, `RUN_STARTED` → `TEXT_MESSAGE_*` → … |

`skill=` pins the run to one skill instead of letting the first-turn
classifier choose. It is honoured only for a graph built by `create_agent` /
`wrap_agent`; `make_flow_router` 404s an unknown skill name, so a typo is
never silently ignored.

Serving several agents from one process means one router per agent:

```python
for agent_id, agent in agent_registry.items():
    app.include_router(make_chat_router(agent), prefix=f"/chat/{agent_id}")
```

Do not reach into `make_chat_router(agent).routes[0].endpoint` and call it
yourself — the endpoint takes its base path and protocol through FastAPI
dependencies, and calling it directly passes them wrong.

## 4. Consume the stream in your React UI

```tsx
import { useFlowChat, Widget } from "@aigenos/flow";

export function ChatPanel({ agentId }: { agentId: string }) {
  const { messages, send, submitWidget, status } = useFlowChat({
    endpoint: `/api/chat/${agentId}`,
    protocol: "ag-ui",
  });

  return (
    <YourChatShell>
      {messages.map((m) =>
        m.kind === "widget" ? (
          <Widget
            key={m.id}
            spec={m.payload.spec}
            disabled={m.resolved}
            onSubmit={(payload) => submitWidget(m.tool_call_id, payload)}
          />
        ) : (
          <YourBubble key={m.id} role={m.role}>{m.content}</YourBubble>
        ),
      )}
      <YourComposer
        disabled={status === "live" || status === "waiting_for_widget"}
        onSend={send}
      />
    </YourChatShell>
  );
}
```

`Widget` is the `SpecRenderer` the Studio itself uses — 16 primitives
(`alert`, `autosuggest`, `badge`, `button`, `card`, `column`, `datatable`,
`divider`, `form`, `image`, `input`, `loading`, `row`, `select`, `text`,
`timeline`), exported as `PRIMITIVES` so you can replace one and keep the
rest. Inline per-field validation is opt-in through `validateEndpoint` or
`FlowConfigProvider`; off by default no request is made.

## 5. Protocol switching

One backend, three protocols, chosen per request:

```bash
curl -N -X POST "http://localhost:8000/chat?protocol=ag-ui"  -H 'content-type: application/json' -d '{"message":"hi"}'
curl -N -X POST "http://localhost:8000/chat?protocol=a2ui"   -H 'content-type: application/json' -d '{"message":"hi"}'
curl -N -X POST "http://localhost:8000/chat?protocol=mcp-ui" -H 'content-type: application/json' -d '{"message":"hi"}'
```

Your agent code is unchanged. All three work through both `make_flow_router`
and `make_chat_router`, verified end to end by replaying captured SSE bytes
through the frontend's own parser. Both routers emit `TOOL_CALL_RESULT` on a
resume turn and a terminal `STATE_SNAPSHOT` before `RUN_FINISHED`.

**The honest caveat:** "works against this client" is proven. Conformance to a
published A2UI or MCP-UI spec revision is not — both adapters track drafts.

## 6. Authoring widgets in the Studio

The bundled Vite app (`frontend/`) has a Widgets surface where a
non-developer can build a widget visually or edit its JSON, and a Canvas
surface where they build the flow itself. Both save through write endpoints,
which work on the machine running the server. In 0.3.1 the Studio UI does not
send an admin token, so token mode is for API and CI clients; use the default
local mode to author in the Studio. See
[user-guide.md](user-guide.md) for what the Studio is and is not.

## 7. Production checklist

| Concern | Action |
|---|---|
| Flow state | Give `create_agent` / `wrap_agent` a real `checkpointer=`; `MemorySaver` is the default and loses everything on restart |
| Bundled server state | In memory, lost on restart. `FG_TRACKER_STORE_URL` is experimental in 0.3.1 and needs a `langgraph-checkpoint-*` package you install yourself; for durable state use the row above |
| Write surface | `FG_SKILL_WRITES=token` + `FG_ADMIN_TOKEN` for API and CI clients (in 0.3.1 the Studio UI does not send the token), or `off`; it executes operator Python, so never `open` on a reachable interface |
| Bind address | `python -m aigenos_flow.main` binds loopback and refuses a public bind unless `FG_SKILL_WRITES` is `off` or `token` |
| CORS | Defaults to the local dev origins; set `FG_CORS_ORIGINS`, never `*` |
| Multi-tenancy | Scope `thread_id` to `{org_id}:{user_id}:{conversation_id}` |
| Auth | Add your existing auth dependency in front of the router |
| Observability | Wrap `agent.astream` in your spans; count AG-UI events |
| Rate limits | Cap concurrent SSE streams per user |
| Connector admin | `FG_CONNECTOR_ADMIN_TOKEN` — see [managed-connectors.md](managed-connectors.md) |

## 8. Hand-wiring it yourself

Reach for this when the wrapper cannot fit: a custom state schema it would
have to guess at, a non-LangGraph framework, or a review that needs to see
exactly what the abstraction does. You own the state dict between turns.

```python
from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.hosts.langgraph_host import LangGraphHost
from aigenos_flow.interpret import classify_skill

flow = FlowAgent.from_dir("skills/", host=LangGraphHost(llm))   # build once

def handle_turn(session, user_text=None, widget_submit=None):
    state = session.get("flow_state") or initial_state()

    # Deterministic WHEN, LLM-judged WHICH — one structured call, no regex.
    if (not state.get("flow_id") or state.get("done")) and user_text:
        picked = classify_skill(host=flow.host, user_message=user_text,
                                registry=flow.registry)
        if picked:
            state = initial_state()
            state["flow_id"] = picked

    if state.get("flow_id") and not state.get("done"):
        if widget_submit is not None:
            state["pending_field_updates"] = widget_submit   # structured, no LLM
        elif user_text is not None:
            state["pending_user_message"] = user_text        # free text
        state = flow.turn(state)
        session["flow_state"] = state
        return {
            "messages": flow.host.drain_pending_messages(),
            "awaiting_field": state.get("awaiting_field"),
            "done": state.get("done"),
        }

    return your_agent.invoke({"messages": [("user", user_text)]})
```

The frontend contract is `awaiting_field` plus its `FieldSpec`:

| `FieldSpec.type` | render as | notes |
|---|---|---|
| `string` | text input | honour `pattern` |
| `number` / `integer` | number input | honour `min` / `max` |
| `boolean` | yes/no or toggle | a confirmation field is a boolean |
| `enum` | select or radio | options are `spec.values` |

Plus `spec.description` (the label, and the question the engine asks for a
missing field — keep it readable), `spec.required`, and `spec.must_elicit`.
There is no `default` on a `FieldSpec`.

A `must_elicit` field, every boolean an action names in `requires:`, and every
`sensitive: true` field must come from an explicit user action:
`skill.confirmation_fields` lists the second group. A value the interpreter
proposes for any of them is kept only when it is grounded in the user's own
message (and, for a confirmation or a `sensitive` field, only on the turn the
flow asked for it); otherwise it is dropped.

Post submissions back as `widget_submit={field: value}`. That path bypasses the
interpreter entirely, and a corrected value re-arms any gate that depended on
it — clearing a verification value already collected and every memoized action
result derived from it.

Use the exact declared field names. A key that is not a declared field of the
active skill is dropped with a `logging.warning` rather than stored, so a typo
shows up as a flow that keeps asking the same question.

You do not need `@aigenos/flow` on npm and you do not need the `[server]`
extra for this hand-wired path. Determinism is entirely backend.

## 9. Common pitfalls

1. **No checkpointer → no resume.** `interrupt()` has nowhere to persist
   state and the resume call fails.

2. **Forgot `widget_catalog_prompt()`.** The model guesses widget ids and gets
   `{"_error": "unknown widget …"}` back.

3. **Streaming text inside a tool call.** If your agent calls `render_widget`
   mid-sentence the SSE stream ends there by design; the client reconnects on
   resume. Handle that in your UI — the bundled hook does.

4. **Treating `render_widget` as the safety story.** It renders UI. Nothing
   about it validates a submission or gates a side effect. A write a skill
   governs needs `create_agent`'s `flow_only` default, or an explicit
   `flow_only(tool, skill=...)` in your own tool list.

5. **Trusting a submission shape.** Through `render_widget` alone, the tool
   returns whatever the client posted — validate it against the widget's
   `submit_schema` before you store it. Through the flow runtime, the typed
   `FieldSpec` already did: `min` / `max`, `pattern`, enum `values`, and a type
   boundary that refuses NaN and infinities.

6. **A drifted request body.** `ChatRequest` is `extra="forbid"`; an unknown
   key is a 422, not a silently ignored field.

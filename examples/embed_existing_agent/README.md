# Embed aigenos/flow in the LangGraph backend you already have

You already run a LangGraph agent with skills, and a chat UI that renders
a2ui widgets. You want the **deterministic flow engine** dropped in with
minimum friction — **pip install only**, your frontend untouched.

This folder is the proof and the recipe. `verify_roundtrip.py` drives the
transfer-funds gate purely through **structured field submissions** (what your
a2ui widget posts back) with **zero LLM calls**, and asserts the 2FA gate can't
be skipped — including after a mid-flow correction.

```
python verify_roundtrip.py     # no API key, deterministic, ~instant
```

## 0. The one-liner (use this unless you have a reason not to)

If your agent is a compiled LangGraph graph, you don't need to hand-wire
anything — wrap it:

```python
from aigenos_flow import wrap_agent

safe_agent = wrap_agent(my_existing_agent, skills_dir="skills/",
                        llm=llm, tools=[bank_transfer])
```

Your agent keeps its tools, prompt, and state. Skill-matching messages run
deterministically; everything else is delegated to your agent untouched.
The rest of this README is the **manual** version of that wrapper — for
custom state schemas, non-LangGraph frameworks, or when you want to see
exactly what the abstraction does.

## 1. Install

The core is lean: `pydantic`, `pyyaml`, `langgraph`, `langgraph-prebuilt` and
`langchain-core`. No web server, no UI. You already run LangGraph, so it adds
almost nothing.

```bash
pip install aigenos-flow
# (no npm needed — see §4)
```

Behind a firewall? It is a plain package with those five runtime dependencies:
mirror it on your internal index like any other, or build the wheel from
`backend/` with `python -m build`.

## 2. Drive a flow (the whole API surface)

One `FlowAgent`, one `turn()` per user action. **You** own the state dict between
turns (put it in your session/checkpointer).

```python
from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.hosts.langgraph_host import LangGraphHost

flow = FlowAgent.from_dir("skills/", host=LangGraphHost(llm))   # build once

state = initial_state()
state["flow_id"] = "transfer-funds"          # or let the router pick (see §3)

# free-text turn  → interpreted by the LLM
state["pending_user_message"] = "send $5000 to mom from checking"
state = flow.turn(state)

# structured widget submit → applied by pure Python, NO LLM (deterministic)
state["pending_field_updates"] = {"verification_code": "481920"}
state = flow.turn(state)

assistant_lines = flow.host.drain_pending_messages()   # what to show the user
awaiting       = state["awaiting_field"]                # the next field to render
done           = state["done"]
```

## 3. Route between your agent and the flow (no graph surgery)

The minimal embed: your chat handler keeps a `FlowState` per session. If a flow
is active, drive it; otherwise your existing agent runs — and the classifier
decides when a new flow should take over.

```python
from aigenos_flow import initial_state
from aigenos_flow.interpret import classify_skill

def handle_turn(session, user_text=None, widget_submit=None):
    state = session.get("flow_state") or initial_state()

    # start a flow when the user's message matches one (deterministic WHEN,
    # LLM-judged WHICH — one structured call, no regex)
    if (not state.get("flow_id") or state.get("done")) and user_text:
        picked = classify_skill(host=flow.host, user_message=user_text,
                                registry=flow.registry)
        if picked:
            state = initial_state(); state["flow_id"] = picked

    if state.get("flow_id") and not state.get("done"):
        if widget_submit is not None:
            state["pending_field_updates"] = widget_submit     # structured
        elif user_text is not None:
            state["pending_user_message"] = user_text          # free text
        state = flow.turn(state)
        session["flow_state"] = state
        skill = flow.registry.get(state["flow_id"])
        field = state.get("awaiting_field")
        return {
            "messages": flow.host.drain_pending_messages(),
            "awaiting_field": field,
            "field_spec": skill.fields.get(field) if field else None,
            "done": state.get("done"),
        }

    # no flow active → your existing agent owns this turn
    return your_agent.invoke({"messages": [("user", user_text)]})
```

This is exactly what `wrap_agent` builds for you (plus interrupts, widget
resolution, and checkpointing).

## 4. Render in YOUR a2ui UI (no npm package)

After each `turn()` you get `awaiting_field` + its `FieldSpec`. Map that to a
widget in your existing a2ui renderer — that's the entire frontend contract:

| `FieldSpec.type` | render as | notes |
|---|---|---|
| `string` | text input | |
| `number` / `integer` | number input | honor `min` / `max` |
| `boolean` | yes-no / toggle | the `confirm` step is a boolean |
| `enum` | select / radio | options = `spec.values` |

Plus `spec.description` (label/help — it is also the question the engine asks
for a missing field, so keep it readable), `spec.required`, and
`spec.must_elicit` (don't pre-fill or infer this one — it must come from an
explicit user action). The same user-only rule applies, without any authoring
key, to every boolean an action names in `requires:` and to every field marked
`sensitive: true`: `skill.confirmation_fields` lists the former.

On submit, post the structured value back as `widget_submit={field: value}`. That
goes through `pending_field_updates` and **bypasses the interpreter entirely** —
the field-fill is deterministic, and a corrected value re-arms any policy gate
that depended on it.

Use the exact field names from `GET`-ing the skill (or from `skill.fields`): a key
that is not a declared field of the active skill is **dropped** with a
`logging.warning` rather than stored, so a typo shows up as a flow that keeps
asking the same question. Watch your logs when wiring a new widget.

You do **not** need `@aigenos/flow` on npm, and you do **not** need the `[server]`
extra. Determinism is entirely backend.

## 5. The gate-bypass this example caught

Driving the gate through structured submissions surfaced a real bug: a widget
**correction** (e.g. bumping `$500 → $5000` on a review screen) used to apply with
`is_correction=False`, so the memoized `check_policy` result survived and the
2FA gate did **not** re-engage — a $5000 transfer could fire with no code. Fixed
in `runtime.py` (a re-submitted field is now treated as a correction, mirroring
the LLM `Correct` path). `verify_roundtrip.py` scenario **C** is the regression
guard.

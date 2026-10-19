# aigenos-flow

> **The LLM fills fields. Your program does everything else.**

You probably already have the setup this package upgrades: a LangGraph ReAct
agent that loads **skills as natural-language instructions**. It mostly works,
and two things keep failing in the same way:

1. **Policies are advisory.** "Always require a 2FA code above $1000" holds on
   most runs — until a rephrase, a correction, or a pushy user lands on the run
   where the model skips it. The instruction was a suggestion, and the model is
   the one deciding.
2. **Widgets are a coin flip.** If rendering a widget is a tool the model *may*
   call, your forms and grids show up sometimes. The UI is a choice the LLM
   makes per turn, so it drifts like everything else.

aigenos-flow fixes both **without replacing your agent**: you keep ReAct for
open-ended turns, and promote the workflows that touch real things — money,
bookings, tickets — into typed, deterministic flows.

## Install

```bash
pip install aigenos-flow            # the engine — no server, no UI required
pip install "aigenos-flow[server]"  # optional FastAPI server that streams widgets
```

Python 3.11+. The engine's whole dependency list is pydantic, PyYAML,
langchain-core, langgraph and langgraph-prebuilt.

## What changes in a skill: before → after

**Before** — the instruction skill you have today. Every rule is enforced by
hoping the model complies:

```yaml
---
name: transfer-funds
description: Send money to a saved payee.
---
Help the user transfer money. Collect the amount and the payee.
ALWAYS require a 6-digit verification code for amounts of $1000 or more.
Confirm with the user before calling transfer_money. Never skip confirmation.
```

**After** — same file, same English body, plus a list of steps. The rules move
from prose into structure the runtime enforces:

```yaml
---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000 and above require a 2FA code.
steps:
  - ask: { amount: "How much do you want to send? (USD)" }
    check: { amount > 0: "Enter an amount above zero." }
  - ask: { payee: "Who is it going to?" }
    choices: [mom, landlord, gym]
  - ask: { code: "Enter the 6-digit code we texted you." }
    when: amount >= 1000            # the 2FA gate, re-evaluated on every replay
    type: secret                    # never logged, never supplied by the model
    pattern: '^[0-9]{6}$'           # a shape, so "skip" cannot satisfy the gate
  - say: "You're sending ${{amount}} to {{payee}}."
  - confirm: Send this transfer?
  - do: bank_transfer               # changes something: unreachable until the yes
  - say: "Sent ${{amount}} to {{payee}}."
---
Help the user transfer money. Collect the amount and the payee.
(The body stays — it's now LLM context for extraction and tone. The 2FA rule
above no longer lives here, because prose can't enforce anything.)
```

What you **added**: `steps:`. Five kinds of step (`ask`, `say`, `do`,
`confirm`, `chat`), with `when:` for the gate and `check:` for what an answer must
satisfy. What you **kept**: the English. What you **deleted**: nothing. Skills
that are purely advisory — tone, escalation, style — stay instruction-only and
unchanged.

There is no schema to write. `amount` is a number because the `when:` compares
it with one, `payee` is a select because of `choices:`, and `bank_transfer`
receives the answers whose names match its parameters. It is not marked
read-only, so it needs the `confirm:` above it, runs at most once per flow run,
and goes on the audit trail with the code redacted.

The loader verifies the skill **before any conversation starts**, and a
violation is a load error, not a warning: a misspelled word, a tool that
changes something with no `confirm:` above it, a step that reads an answer
nothing has asked for, or a condition that compares text with a number. And one
thing it does for you: `amount` decides whether a code is needed, so the flow
makes it user-only. The model may pass on a number the user typed; any other
number is dropped.

This exact before/after is a **runnable proof** — the same hostile input thrown
at both architectures, 42 checks, about 1 second, no API key:
[`examples/before_after/prove.py`](https://github.com/aigenos/flow/tree/main/examples/before_after).
The model is scripted in both halves, so the proof is about the executor: when
a model emits a $5000 transfer with no code, the prompt-only agent runs it; as
structure, the transfer is unreachable — and the legitimate path still
completes. It runs from a clone on the bare install; no extra is needed.

## Two ways to run it

### 1 — Let aigenos-flow be the agent

```python
from aigenos_flow import create_agent

agent = create_agent(llm, "skills/", tools=[bank_transfer])

agent.invoke({"messages": [("user", "send $5000 to mom")]},
             config={"configurable": {"thread_id": "t1"}})
```

That's the whole integration. A query that matches a skill runs its flow
**deterministically** — typed extraction, gates, replayed order. A query that
doesn't (and the conversation *after* a flow completes) is ordinary ReAct
question/answer over the accumulated context. Activation is a structured
classifier per message — deterministic *when*, LLM-judged *which*, no regex.

```python
create_agent(llm, skills_dir="skills/", *, tools=None, prompt="", a2ui=True,
             mode="flex", checkpointer=None, host=None,
             governed_tools="flow_only")
```

- `tools=` takes a list of LangChain tools / plain callables, or a
  `{name: callable}` dict. They are both tools a skill's steps may name and
  the ReAct loop's tools.
- **Tools can live with the skills instead.** Every public function in
  `skills/tools.py` or `skills/tools/*.py` is a tool the steps can name
  (`tools_dir=` adds a folder kept elsewhere). Mark the ones that only look
  things up with `@readonly`; any other tool needs a `confirm:` above it.
  Adding a flow never means editing the agent's own code.
- **A write a skill governs never reaches the model raw.** That is
  `governed_tools="flow_only"`, the default: on a turn that lands in the ReAct
  loop, the model gets a shim that refuses and starts the governing flow
  instead, with gates armed. `governed_tools="allow"` opts out and keeps the
  ungated path.
- `skills_dir` must exist and hold at least one flow-capable skill. Otherwise
  this **raises** — it never hands back a plain ReAct agent that merely looks
  gated.
- A `MemorySaver` checkpointer is installed by default so paused flows survive
  across `invoke` calls. Pass your own `checkpointer=` in production.

### 2 — Wrap the agent you already have

Already running `create_react_agent`, or a LangGraph graph of your own? Keep
it. One call adds the deterministic boundary in front:

```python
from aigenos_flow import wrap_agent

safe_agent = wrap_agent(my_existing_agent, skills_dir="skills/",
                        llm=llm, tools=[bank_transfer])
```

Your agent keeps its tools, its prompt and its state; extra keys on its own
state schema pass through and merge back. The wrapper intercepts messages that
match a skill, runs the flow to completion, then hands the conversation back.

Wrapping cannot edit a compiled graph's tool list, so it **checks** instead of
repairing: a tool on your agent whose name a skill governs is an error, and
tools it cannot vouch for are a warning (nothing can tell that your
`transfer_money` is the skill's `bank_transfer`). Fix either one by wrapping the
tool — the flow still executes the real thing:

```python
from aigenos_flow import flow_only

my_existing_agent = create_react_agent(llm, tools=[flow_only(bank_transfer)])
```

Prefer wiring the router yourself (custom state schemas, non-LangGraph
frameworks)? The explicit recipe with a zero-LLM proof is in
[`examples/embed_existing_agent`](https://github.com/aigenos/flow/tree/main/examples/embed_existing_agent).

## What the file gives you besides the gate

**What you approve is what runs.** The confirmation is a card the engine
builds: the tool a yes will run and the inputs it will run with. What was shown
is kept and compared when the tool is called. If an input changed after the
yes, the tool does not run and the yes is asked for again.

**It reads back as a procedure.**

```bash
python -m aigenos_flow.procedure skills/ transfer-funds
```

prints each step in plain words, every way through the flow, and what must be
true before each tool that changes something. It is generated from the file
and the tools with no model involved, so it cannot say something the flow does
not do.

**A yes can be someone else's.**

```yaml
  - confirm: Approve this transfer?
    by: compliance
    when: amount >= 10000
```

The flow stops and files a request showing the question, the tool and its
inputs. The answer reaches the flow only through the approvals store
(`store.decide(id, True, by="dana")`, or `POST /flow/approvals/{id}`): sent
through the conversation, by the user, by the model or by a posted form, it is
refused. `by:` says who is asked; checking who answers is yours to put in
front.

**Tools from an MCP server** work like any others:

```python
from aigenos_flow.mcp_tools import MCPTools          # pip install mcp

agent = create_agent(llm, "skills/", tools=MCPTools([sys.executable, "server.py"]).tools())
remote = MCPTools(url="https://mcp.example.com/mcp")   # a remote server, over streamable HTTP
```

A tool the server annotates `readOnlyHint` is a lookup. Any other needs a
`confirm:` above it and runs at most once, like a function that is not
`@readonly`. Tools you already load with LangChain's MCP adapters, and
`async def` tools, can be passed in `tools=` as they are.

## Widgets stop being a coin flip

In a flow, a widget is **not a tool the model may call** — it's what an `ask:`
step *is*. Every run, the same step renders the same thing:

| step | what renders — always |
|---|---|
| `ask: { amount: ... }` | number input |
| `ask: { payee: ... }` with `choices:` | select built from the choices |
| `ask: { code: ... }` with `type: secret` | masked input — exactly when the gate fires |
| `ask:` with `from: <tool>` | that tool's rows as a selectable grid |
| `say: "{{tool.result}}"` | a card, grid or timeline — chosen by the shape of the data |
| `confirm:` | a card naming the tool a yes will run and its inputs, with Yes and No |
| `do: bank_transfer` | nothing — the one place money can move |

With `a2ui=True` (the default), a pause interrupts with exactly
`{"prompt", "field", "flow_id", "widget"}`, and the same widget spec rides on
the paused assistant message as `additional_kwargs["aigenos_widget"]`, so an
AG-UI / A2UI client renders it straight from the stream. For an `ask:` with
`from:` or `widget:` that widget is what the step built: the selectable grid
with its rows, or the designed widget with its data.

Resume with a widget submission
(`Command(resume={"code": "481920"})`) or free text
(`Command(resume="481920, yes send it")`). A submission comes back as **typed
field updates that bypass the LLM entirely** — pure validation and gate
re-evaluation, so a form can't hallucinate, and a corrected value (the user
bumps $500 → $5000 on a review screen) re-arms the 2FA gate and clears the code
already collected. `a2ui=False` skips widget resolution entirely.

The widget itself either has no asset at all (derived from the `FieldSpec` on
the fly) or is a JSON file in your widgets catalog, in git, named by
`widget: <id>`. The catalog is the `widgets/` folder under `FG_DATA_DIR` (one
`*.json` per widget, matched by its `id`); with `FG_DATA_DIR` unset that is the
package's own data folder, so set it to a folder in your repository.

**And you do not need our frontend.** Determinism is entirely backend. Smallest
tier first: read `chat.awaiting` + `chat.spec` after each turn, map the spec to
your own components, and post back with `chat.submit(field=value)` — no npm
package, no `[server]` extra. Or take the wire protocols from `[server]`
(`?protocol=ag-ui|a2ui|mcp-ui`); what is proven there is that this project's own
client consumes those bytes end to end, not conformance to a published A2UI or
MCP-UI spec revision — both adapters track drafts. Or use the renderer:
[`@aigenos/flow`](https://www.npmjs.com/package/@aigenos/flow) on npm, a
`<Widget>` component plus 16 primitives, zero runtime deps.

## Test whole conversations with no API key

`TestHost` stands in for the LLM: you script what the model *would* extract;
the **real engine** runs the turn — validation, gates, order, effect dedup:

```python
from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

def bank_transfer(amount: float, payee: str, code: str = "") -> dict:
    # a tool is a plain function: it receives the answers whose names
    # match its parameters (LangChain tools with .invoke() work as-is)
    return {"txn": "TXN-1"}

host = TestHost()                                     # stands in for the LLM
agent = FlowAgent.from_dir("skills/", host=host,
                           tools={"bank_transfer": bank_transfer})

# "When the user says this, the model would extract these fields."
host.queue_commands("send $5000 to mom",
                    SetField(field="amount", value=5000),
                    SetField(field="payee", value="mom"))

chat = agent.session("transfer-funds").user("send $5000 to mom")
assert chat.awaiting == "code"                  # $5000 -> the gate is armed

chat.submit(code="481920").submit(confirm=True)
assert chat.done                                # fired once, only with the code
```

Your policy gates become CI assertions instead of prayers.

## Where LangGraph fits

Built **on** LangGraph, not hiding it: `agent.as_langgraph()` *compiles* the
skill folder into an ordinary graph you can embed, checkpoint and stream — or
skip the graph entirely, `agent.turn(state)` is a plain function. The
determinism comes from the replay runtime (the flow re-runs from the top each
turn, so corrections re-arm every downstream gate), not from the graph layer.

## Serving it

The `[server]` extra binds loopback and defaults CORS to local dev origins
(never `*`). Every endpoint that writes a skill or widget, or hands back a
skill's source, is **reachable from loopback only by default** — a skill can
carry Python this process then imports — so the Studio (the authoring UI; it
runs from a clone of the repository and is not inside this package) works on
your own machine while a request from any other host gets a 403. Set
`FG_SKILL_WRITES=off` to drop that surface entirely, or `=token` with
`FG_ADMIN_TOKEN` to keep it behind a secret, before exposing the port. The
Studio UI does not send that token in 0.3.1, so token mode is for API and CI
clients. Chatting, listing skills and reading widgets are open. Sensitive
fields are redacted in the audit trail. Full table:
[the developer guide](https://github.com/aigenos/flow/blob/main/docs/developer-guide.md#rest-api-server-extra).

## Links

- [Steps reference](https://github.com/aigenos/flow/blob/main/docs/steps.md) — the whole short form on one page
- [Developer guide](https://github.com/aigenos/flow/blob/main/docs/developer-guide.md) — install, tool library, REST API, ship checklist
- [10-minute tutorial](https://github.com/aigenos/flow/blob/main/docs/GUIDE.md) · [long-form reference](https://github.com/aigenos/flow/blob/main/docs/skill-authoring.md)
- [Runnable proofs, no API key](https://github.com/aigenos/flow/tree/main/examples)
- Source: https://github.com/aigenos/flow · Issues: https://github.com/aigenos/flow/issues
- Site: https://aigenos.io

## License

Apache-2.0

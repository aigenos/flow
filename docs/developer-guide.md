# Developer guide — aigenos/flow

*The map of everything a developer needs: install, embed, author, persist,
test, ship. Each section links to the deep-dive doc.*

## Install

Starting a new project? Scaffold the whole thing — FastAPI server, web client,
and a first working skill — in one command:

```bash
npx aigenos-agentic-app my-app
```

Adding it to something that already exists:

```bash
pip install aigenos-flow            # engine only — embeds in any LangGraph agent
pip install "aigenos-flow[server]"  # + FastAPI server (AG-UI/A2UI/MCP-UI, Studio backend)
```

The Studio's user interface is not in the pip package. It runs from a clone of
the repository; the [Studio guide](user-guide.md) has the commands.

Frontend (optional, for embedding the chat + widget renderer in your app):

```bash
npm install @aigenos/flow
```

Python **3.11+** (3.13 is what we exercise locally); Node **20.19+ or 22.12+**
to run or build the frontend. The engine's whole dependency list is `pydantic>=2.7.4`, `pyyaml`,
`langchain-core>=0.3.67`, `langgraph>=0.6.0,<2.0` and
`langgraph-prebuilt>=0.6.0,<2.0` with five bad patches excluded (1.0.9 to
1.0.13). That upper cap is deliberate: we import `langgraph.prebuilt.create_react_agent`, which
LangGraph removes in 2.0 — relaxing the cap is a patch release once that import
moves. The `[server]` extra is self-sufficient: it pulls `langchain-anthropic`
and `langchain-ollama`, so a keyless install can still serve a turn against a
local model, and a missing provider answers **503 naming the extra to install**
rather than a 500.

## The 60-second mental model

> **The LLM fills fields. The runtime does everything else.**

A skills folder is the program. Each skill is a `SKILL.md` whose `steps:` say
what is asked, what an answer must satisfy, and which tool runs when. The tools
are plain functions in `skills/tools/`. `FlowAgent.from_dir()` compiles the
folder into a LangGraph agent. Corrections work because the flow replays from
the top each turn; a tool that changes something cannot fire twice because it
is deduped on a per-run effects log keyed to `flow_run_id`.

```python
from aigenos_flow import FlowAgent
from aigenos_flow.hosts.langgraph_host import LangGraphHost

agent = FlowAgent.from_dir("skills", host=LangGraphHost(llm))
graph = agent.as_langgraph()                       # or: agent.turn(state), bare
```

A step that names a tool nobody provides is a load error, so a folder that
loads is a folder whose every step can run. Where tools come from is in
[Tools](#tools). The language itself is one page: the
[steps reference](steps.md).

### More than one skill

One `skills_dir`, many folders, one agent: every immediate subfolder holding a
`SKILL.md` is a skill, and each entry point loads the whole directory. With no
flow active, a classifier (`classify_skill`, one structured LLM call) picks the
skill; mid-flow the interpreter can emit `StartFlow`, which suspends the current
skill and resumes it afterwards with its answers intact.

**Branching.** Two ways, and the program decides in both; no model call picks
the branch.

*Inside one skill*, give each branch a block:

```yaml
steps:
  - ask: { wants: "Do you want a refund, or to open a ticket?" }
    choices: [refund, ticket]
  - when: wants == 'refund'
    steps:
      - ask: { order_id: "Which order?" }
      - confirm: Refund this order?
      - do: issue_refund
  - when: wants == 'ticket'
    steps:
      - ask: { topic: "What is it about?" }
      - confirm: Open this ticket?
      - do: open_ticket
  - say: Done. Anything else?
```

`wants` decides which tool runs, so only the user can answer it. Nobody marked
it; the loader works that out from the conditions that read it. A condition
takes `and`, `or`, `not`, parentheses and comparisons, over answers and tool
results (`confirm_vip.ok and (plan == 'team' or seats > 10)`). It takes no
arithmetic. Any step may carry one, `say:` included, so a branch can say which
way it went.

*Across skills*, a flow hands off with `do:` and the other skill's name. This
is the whole front door of the send-money example, and it is a skill file with
no Python:

```yaml
steps:
  - ask: { pay_to: "Is this payment to someone new, or to a payee you have already saved?" }
    choices: [someone_new, a_saved_payee]
  - when: pay_to == 'someone_new'
    steps:
      - say: They are not saved yet, so first add them as a payee.
      - do: add-payee                             # runs that skill, then comes back here
        else: No payee was added.                 # cancelled or declined: stop here
      - say: Now the payment. Pick them from your payees.
      - do: pay-someone
        else: No payment was made. The payee you added is still saved.
  - when: pay_to == 'a_saved_payee'
    steps:
      - say: Here are your saved payees.
      - do: pay-someone                           # the same second flow, on the other branch
        else: No payment was made.
  - say: That is everything. Anything else?
```

A hand-off is a call, not a jump. The target starts with empty answers and its
own confirmations; when it finishes, the caller carries on from the next step,
and a hand-off that has run is not taken again on later turns. If the target
did not do its job, because the user cancelled it or said no at one of its
confirmations and none of its tools ran, the flow says `else:` and **stops
there**: nothing after a hand-off that did nothing runs as if it had. A `do:`
naming a skill that is not in the folder, or one with no flow to run, is
refused when the folder loads, and so are skills that hand off in a circle.
(In the long form this step is `link:`, with `then_say:` and `else_say:`.)

The same hand-off exists in a Python `@flow` as `ctx.link("other-skill")`, which
returns `True` or `False` and leaves the decision to you. Use it when a failed
hand-off should lead somewhere other than the end (a Python flow that is itself
declined still reports `True`). While the target runs,
the flow that handed off is not offered to the model as somewhere else to go:
a user who restates the errand part-way ("lets send to Vivek" while a payee is
being added) stays in the step they are in. Say which branch you took
before you hand off: two branches that end in the same flow look identical from
the chat otherwise. When a flow that was suspended resumes, whether after a
hand-off or after the user's own side trip into another skill, the lookups it
*shows* (the ones feeding a `from:` list or a card) are read again,
because the other flow may have changed what they return. A lookup that a
`check:` already passed, such as a code verification, is not repeated. Nothing else
is passed in or comes back: the two skills do not see each other's answers, so
share state through your own backend. A skill cannot hand off to itself.
[`examples/studio_agent/proofs/send_money`](../examples/studio_agent/proofs/send_money) is this flow in full, with
a proof.

## Entry points: pick one of three

| | Use when | Call |
|---|---|---|
| `create_agent` | aigenos-flow *is* your agent — ReAct for open turns, flows for the ones that touch real things | `create_agent(llm, "skills/")` |
| `wrap_agent` | you already have a compiled LangGraph agent and want a deterministic boundary in front of it | `wrap_agent(my_agent, skills_dir="skills/", llm=llm)` |
| `FlowAgent.from_dir` | you want the flow runtime bare — your own router, your own state schema, or a test | `FlowAgent.from_dir("skills", host=...)` |

```python
create_agent(llm, skills_dir="skills/", *, tools=None, prompt="", a2ui=True,
             mode="flex", checkpointer=None, host=None,
             governed_tools="flow_only", tools_dir=None)

wrap_agent(agent, *, skills_dir, llm=None, tools=None, a2ui=True,
           mode="flex", checkpointer=None, host=None,
           governed_tools="flow_only", tools_dir=None)

FlowAgent.from_dir(skills_dir, host, *, mode="flex", tools=None, audit=None,
                   allow_empty=False, tools_dir=None)

flow_only(tool_obj, *, skill=None, message=None) -> BaseTool
governed_write_tools(registry) -> dict[str, str]
```

Every entry point finds the tools in `skills/tools/` by itself. `tools=` adds
your own, as a list of LangChain tools / plain callables **or** a
`{name: callable}` dict, and `tools_dir=` adds a folder kept somewhere else.
Both front doors install a `MemorySaver` checkpointer
unless you pass your own — so a paused flow, its `flow_run_id` and its effects
log survive across `invoke` calls. The rest:

- `host=` — reuse a configured `Host` (a `LangGraphHost`, or a `TestHost` in a
  test) instead of building one from `llm=`. `wrap_agent` needs one of the two.
- `prompt=` — extra system text for `create_agent`'s ReAct side only; it cannot
  reach the flow runtime, which is the point.
- `mode=` — how the interpreter is asked for its commands. `"flex"` (the
  default) uses free-form JSON structured output and works on any modern
  instruction model; `"strict"` uses constrained tool calling, so a wrong field
  name is impossible at the API layer. `"strict"` falls back to `"flex"` when
  the host reports no tool-use support, rather than crashing.
- `a2ui=` — `True` (the default) resolves and attaches the paused field's widget
  spec; `False` skips widget resolution entirely.

### `governed_tools="flow_only"` is the default protection

A write tool that a skill governs is not handed to the model raw. Before the
model sees the tool list, that tool is replaced by a shim: calling it refuses
and hands the turn to the governing flow (empty field set, gates armed) instead
of performing the action. `governed_tools="allow"` is the only opt-out, and it
keeps the ungated path on purpose.

`wrap_agent` cannot edit a compiled graph's tool list, so there it **checks**
rather than repairs: a tool on your inner agent whose name a skill governs is
an error; a tool it cannot vouch for is a warning (nothing can tell that your
`transfer_money` is the skill's `bank_transfer`). Repair either by wrapping the
tool yourself — the flow still executes the real one:

```python
from aigenos_flow import flow_only

my_existing_agent = create_react_agent(llm, tools=[flow_only(bank_transfer)])
```

`governed_write_tools(registry)` returns `{tool_name: skill_name}` for
everything a loaded skills folder governs, if you want to audit the list.

### No silent downgrade

`create_agent` **raises** rather than hand back a plain, ungated ReAct agent
that merely looks like the gated one:

| `skills_dir` | `create_agent` | `wrap_agent` |
|---|---|---|
| missing / typo'd | `FileNotFoundError` | `FileNotFoundError` |
| exists, no `SKILL.md` | `FileNotFoundError` | `ValueError: no skills found in …` |
| only instruction-only skills | `ValueError: no flow-capable skill …` | returns the wrapped graph |

That last cell is the one to know: `wrap_agent` has nothing to gate in an
instruction-only folder, so it wraps your agent and delegates every turn to it.
That is a boundary with no gates behind it — check the folder you pass. If you
genuinely want the prompt-only agent from `create_agent`'s side, ask for it
explicitly with `build_react_with_skills(..., use_flow=False)`.

## Tools

A tool is a Python function. A step names it, and its parameters are the
answers it needs, so nothing is declared twice.

```python
# skills/tools/payments.py
from aigenos_flow import Secret, readonly


@readonly                                   # only looks something up
def quote(payee_id: str, amount: float) -> dict:
    """What a payment would cost."""
    return {"needs_code": amount >= 1000, "fee": 0.0}


def send_payment(payee_id: str, amount: float, code: Secret = "") -> dict:
    """Send the money."""                   # not @readonly: needs a confirm, runs once, is audited
    return {"reference": "PAY-0001"}
```

- **`@readonly` is the tool's own statement.** A lookup may run whenever a step
  needs its result. Any other tool needs a `confirm:` above it on every path
  that reaches it, runs at most once per flow run, and goes on the audit trail.
  A skill file cannot relabel a write as a lookup.
- **The signature is the contract.** `float` is a number box, `int` a whole
  number, `bool` a yes/no, `Literal["a", "b"]` a select, `Secret` a masked
  value that is never logged. An input with a default is optional. The first
  line of the docstring is shown wherever the tool is listed, including on the
  confirmation card.
- **A tool can keep a rule in Python.** Return
  `{"ask_again": "amount", "say": "That is more than your balance."}` and the
  flow says the sentence and asks that question again.
- A function written the older way, `def tool(args: dict)`, receives every
  answer asked above it as one dict. LangChain tools work as they are.

| Source | How | Who can name it |
|---|---|---|
| The library | every public function in `skills/tools.py` or `skills/tools/*.py`; a file whose name starts with `_` holds helpers | every skill |
| Another folder | `tools_dir="shared/tools"` on any entry point | every skill |
| One skill's own | `scripts.py` in that skill's folder | that skill only |
| Your code | `tools=[fn]` or `tools={"name": fn}`; wins over a library function of the same name | every skill, and the ReAct loop |
| An MCP server | `tools=MCPTools([...]).tools()` | every skill |
| Built in | `save_request`: records the answers in `records.jsonl` beside the skill (never a secret) and returns `reference` | every skill |

**MCP.** `pip install mcp`, then:

```python
import sys
from aigenos_flow import create_agent
from aigenos_flow.mcp_tools import MCPTools

travel = MCPTools([sys.executable, "mcp_server.py"])          # a server it starts (stdio)
bank = MCPTools(url="https://mcp.example.com/mcp",            # a remote server
                headers={"Authorization": f"Bearer {token}"})
agent = create_agent(llm, "skills/", tools={**travel.tools(), **bank.tools()})
```

A remote server is spoken to over streamable HTTP; `transport="sse"` is for an
older one that only speaks SSE. Each tool arrives with its input schema, so an
answer's type comes from the
server, and with its `readOnlyHint` annotation: a tool that carries it is a
lookup, and any other needs a `confirm:` above it like a function that is not
`@readonly`. `readonly=[...]` names lookups on a server that does not annotate,
and `only=[...]` takes a subset. The flow still decides which tool is called,
when, and with which inputs; the server only ever sees validated values.

**Tools you already load with LangChain's MCP adapters** work as they are:
pass the same objects in `tools=`. Their JSON Schema gives the inputs, the
server's `readOnlyHint` makes a tool a lookup, and their structured result is
what `{{tool.key}}` reads. They can only be awaited, so a step awaits them on a
background event loop; an `async def` function in the library works the same
way. A tool tied to a session you opened yourself in your own event loop has to
be called on that loop: use the adapters' client, which opens a session per
call, or `MCPTools`.
[The trip example](../examples/studio_agent/proofs/conference_trip) runs the
same skill against Python functions and against an MCP server, and proves the
two runs identical.

In the Studio, the **Tools** page lists all of this: every tool, what it
takes, whether it changes anything, which skills use it, an editor for the
library files, and a Try button.

## Which word, when

Steps are the language: **5 kinds of step** (`ask`, `say`, `do`, `confirm`,
`chat`), **2 words for conditions** (`when`, `check`), `repeat` for a list of
items, and 12 options, **20 words in all**, every one checked by the loader. The seven skills the Studio ships as
steps use between 7 and 14 of them. Pick by *intent*:

| You want to… | Write | Not |
|---|---|---|
| Collect one typed value | `- ask: { amount: "How much?" }`. The type and the widget come from the tool that receives the answer | a schema block |
| Offer fixed choices | `choices: [refund, exchange]` on the `ask:` | |
| Let the user pick a row from live data | `from: list_payees.payees` on the `ask:` | choices typed into the file |
| Refuse an answer and ask again | `check: { amount > 0: "Enter an amount above zero." }` | policy text in the prompt |
| Let an answer be left blank | end the question with `(optional)` | |
| Ask, say or run only sometimes | `when:` on the step, or a `when:` block with its own `steps:` | a second skill |
| Say something, or show a tool's result | `- say: "..."`; a result that is data is shown as a card | JSON pasted into a sentence |
| Look something up | nothing: name `tool.key` in a `when:`, `check:`, `from:` or `say:`, and the lookup runs when it is needed | a step for it |
| Change something in the world | `- confirm: "...?"` then `- do: tool` | a tool the model may call |
| Need someone else's yes | `by: manager` on the `confirm:` | a code the user reads out |
| Show a page you designed for the yes | `widget: checkout` and `with:` on the `confirm:` | |
| Hand off to another skill, then carry on | `- do: other-skill`, with `else:` for what to say if it did not finish | a Python router |
| Collect a list (travellers, line items) | `- repeat: { travellers: "Add another traveller?" }` with the questions under it; later steps and tools read `travellers` | a Python loop |
| Show a widget you designed | `widget:` and `with:` on a `say:` (shown) or an `ask:` (it collects the answer) | |
| Answer open questions at the end | `- chat: "..."` with `tools:` listing lookups; inputs named like an answer are fixed to it | an unscoped agent |
| Keep a rule in Python | the tool returns `{"ask_again": "amount", "say": "..."}` | a second copy of the rule in the file |
| Add, average or otherwise compute | a tool | arithmetic in a condition (a load error) |
| Tone, language, escalation | `guidelines:` beside `steps:` — When/Then rules for the model's wording; they never decide a step | tone written as steps |
| Anything else (a tool per item, parallel work) | a Python `@flow` in `handlers.py` — same runtime, same guarantees | contorting the steps |

### The long form underneath

`steps:` is shorthand. It expands into the long form (`fields:` / `actions:` /
`flow:`), which the same loader checks: 9 step kinds (`run:` is an alias of
`do:`), 2 guards and the frontmatter blocks, 64 exact names in all.
`aigenos_flow.shortform.explain(frontmatter, registry)` shows what a `steps:`
block expands to. You need the long form for two things steps do not say:
free-form questions and answers inside a flow (`chat:`, `assist:`), and
per-argument bindings. It is documented in the
[long-form reference](skill-authoring.md); the same table in its terms:

| You want to… | Use | Not |
|---|---|---|
| Collect one typed value from the user | `ask:` — renders a validated widget automatically from the `fields:` spec | `chat:` (free text can't guarantee a typed value) |
| Word that question | the field's `description:` | `prompt:` on the `ask:` step — that is a load error |
| Say something fixed or templated | `say: "Send {{amount}} to {{recipient}}?"` | a guideline (those shape tone, not steps) |
| Run a tool / cause a side effect | `do:` naming an entry in `actions:` — a write needs `requires:` and a `when:` guard; `then_say:` tells the user the outcome, once | letting a chat leaf call a write (the loader forbids it anyway) |
| Let the user pick a row from live data | `pick:` (auto grid/autosuggest from the prior result) or `pick:` + `widget: <id>` + `save:` for a designed grid | an `enum` field (enums are for closed, static sets) |
| Show data with no input | `show:` (auto-widget by data shape). A designed card/table/timeline is a `widget: <id>` step, which always pauses for a submission and must list the field(s) it fills in `save:` | `say:` with JSON pasted in |
| Enforce a policy precondition | `check:` (fail-closed gate with `else_say:` and a required `else_clear:`) or `requires:` on the action itself | policy text in the prompt |
| Branch on state | a `when:` guard on any step — safe-eval'd over fields, computes and prior action results | a second skill |
| Hand off to another skill, then carry on | `link: other-skill`, with an optional `then_say:` and `else_say:` — if that skill is cancelled or declined this flow stops there | a Python router for something the file can express |
| Skip steps that are already satisfied | `when_missing: <field>` — the listing/grid steps vanish when the user already named the thing | `when: "field == None"` gymnastics |
| Derive a **comparison** instead of asking for it | `compute:` (pure, re-evaluated every replay, so corrections re-arm gates) | arithmetic in `compute:` — that is a load error |
| Add, average or otherwise compute a number | a tool called from `do:`, or a Python `@flow` | `compute: "a + b"` |
| Free-form Q&A within the workflow's scope | `chat:` — model picks among tools you listed in `readonly-tools:`; `pin:` overrides its args from validated state | an unscoped agent |
| Answer side questions *mid*-flow, then return | `assist:` frontmatter — same scoping rules; the replay re-prompts the pending step | breaking out of the flow |
| Tone, language, escalation, phrasing rules | `guidelines:` — When/Then rules followed in every reply; reads only, never writes | encoding tone as flow steps |
| Anything the YAML can't express (loops, complex logic) | a Python `@flow` in `handlers.py` — same runtime, same guarantees | contorting the YAML |

Two rules of thumb resolve most hesitation:

1. **`ask` vs `pick` vs `widget`:** `ask` collects a *field*; `pick` selects
   from *data a tool just returned*; `widget` is `pick`/`show` with a design you
   control from the catalog. All three end in typed field updates that bypass
   the LLM.
2. **`check` vs `when` vs `requires`:** `when` routes (step runs or skips),
   `check` refuses (explains, clears, re-collects), `requires` guards the side
   effect itself — the last line of defense, enforced even if you forget the
   other two.

### What you never author

Corrections, digressions, cancellation and natural replies are *interpreter
commands* (`Correct`, `StartFlow`, `Cancel`, `Reply`) — the model emits them,
the runtime handles them. You don't write "go back" arrows: the flow replays
from the top each turn, so a corrected field automatically re-arms every
downstream gate.

### What the conversation remembers

A cancel ends a run: its fields, results and approvals can never act again.
That is what keeps a flow deterministic. It is not the same as forgetting that
the run happened, so two things outlive it, and neither can cause an action.

| | What it holds | What it is for |
|---|---|---|
| `journal` | One entry per run that ended, was declined, was cancelled or was restarted: the flow, what the user had entered (secrets hidden), what its actions returned, what it was waiting for | The model reads it to answer *"who did I pick?"* and *"did that go through?"*, inside the next flow or with none running |
| `parked` | The run the user last cancelled part-way, with every flow of the errand | *"continue"* puts it back at the question it stopped on |

Four things a user can say, none of which you write a step for:

| The user says | Command | What the runtime does |
|---|---|---|
| "never mind" | `Cancel` | Ends the run, records it, sets it aside |
| "continue", "go back to the payment" | `Resume` (or the `resume` action when no flow is running) | Restores the parked run. A flow in progress waits underneath, like any flow under a side trip |
| "start over" | `Restart` | The errand in hand again from its first question, under a new run id |
| "let me pick a different payee" | `Reopen(field)` | Clears that one answer, voids what depended on it, asks it again |

The rules that keep this safe are in the runtime, not in a prompt:

- **A parked run holds no secret and no approval that has not been used.**
  "Never mind" withdraws a yes: every code is dropped when the run is set
  aside, and so is every confirmation whose write has not gone through.
  Picking it up shows the summary and asks again. A yes or a code in the
  message that resumes it lands nowhere: nothing is being asked yet.
- **A run that had already written comes back with that write done.** It is in
  the effects log under the same run id, so it stays at-most-once, and its
  approval is not asked for a second time.
- **The record is read, never written from.** A `must_elicit` field, a
  confirmation and a secret still need the user's own words in the current
  message, whatever the record says.
- **A list is read, never chosen from.** What a `pick:` step saves counts only
  when the user clicked it or typed the id. A user who names a row in words
  ("send it to Mom instead") gets the list again.
- **A question is answered.** When the user asks something while the flow is
  waiting on a choice, the model's answer is kept and the flow asks its
  question again; it is not replaced by "Please choose one of…".

Both keys are checkpointed by `FlowAgent.as_langgraph`, `create_agent` and
`wrap_agent`, and neither is reset when a flow starts or ends.

### What the vocabulary deliberately cannot do

It covers the conversational-workflow core — collect, branch, gate, act,
render, converse — and it is *intentionally not Turing-complete*, because that
is what keeps load-time verification decidable and replay semantics sound.
Four things are missing from the skill file by design, each with a sanctioned
escape hatch:

- **Loops / iteration over collections** → write a Python `@flow` (ordinary
  `for` loops on the same runtime), or model per-item confirmation as its own
  turn.
- **Parallel fan-out** → do it *inside* one `do:` action; your Python fans out
  and aggregates, and the flow sees one deterministic result.
- **Arithmetic** → a tool, or a Python `@flow`. A condition compares, it does
  not calculate.
- **Fully open-ended agency** → the long form's `chat:` leaf, deliberately
  scoped: the model chooses *which* read answers, never *what state changes*.

## The load-time contract

Everything below is checked **before any conversation starts**, and every one of
them is a load error, not a warning. The point is that a skill which would have
failed open cannot be loaded at all.

**For a skill written as steps**, the loader refuses, with a sentence that says
what to change:

- a word that is not one of the 17 (it names the nearest one), or a long-form
  word inside `steps:` (it says what to write instead);
- a tool that changes something with no `confirm:` above it, or with one that
  is only asked on some of the paths that reach it;
- a tool that needs an input nothing asks for, or that is only asked later or
  on another branch;
- a condition or a sentence that reads a name nothing provides, and a condition
  that compares text with a number;
- `from:` naming a tool that is not `@readonly`;
- a tool that changes something used twice without `as:`;
- `do:` naming a skill that is not there, or skills that hand off in a circle.

The Studio's canvas runs the same check as you edit and puts the sentence on
the step it is about (`POST /flow/skills/check` is the endpoint).

The same loader stands behind "change it in a sentence". `POST /flow/skills/edit`
takes a `SKILL.md` and an instruction, asks the model for the changed steps, and
puts the result through the loader before answering; a refusal goes back to the
model once, with the loader's sentence. The answer is a proposal (`skill_md`),
the model's description of it (`changed`), and the loader's verdict (`ok`,
`check`), or `cannot` when the model says a tool is missing or a rule forbids
it. Nothing is written. In code it is `aigenos_flow.skill_editor.propose(...)`,
which takes the loader as an argument.

The rest of this section is the same contract in the long form's terms, which
is what steps expand to.

**Typos can't fail open.** An unknown or misspelled key is rejected anywhere it
appears — frontmatter (`flowz:` → *did you mean 'flow'?*), a field spec, an
action spec, a guideline, a flow step, a `chat:`/`assist:` block. One verb per
step (the documented `pick:` + `widget:` pair excepted), no empty verb value,
and no modifier key the verb does not read — `ask:` + `prompt:` is refused with
*the wording of an `ask:` comes from the field's `description:`*.

**Every action is a write unless it says otherwise.** `readonly: true` is the
only way to declare a read. A write must name a declared **boolean** field in
`requires:`, its `do:` step must carry a `when:` that proves that confirmation,
and it cannot turn auditing off (`audit: false` is for reads only). If it sets
`idempotency_key:`, that key must include `{{flow_run_id}}` — a business key
alone would widen the dedup window so that a correction reuses a stale result.

**Only proven read-only tools reach a conversational leaf.** `chat:`,
`assist:` and `guidelines:` let the model choose the arguments, so every tool
they name must be declared in the new `readonly-tools:` frontmatter key (a
subset of `allowed-tools:`) or be a managed-connector read. An undeclared
callable is assumed to write.

**A gate's input is user-only, and you do not have to say so.** Any field a
`compute:`, a step's `when:` or a step's `check:` reads is marked user-only by
the loader. (It used to refuse the file and ask you to add `must_elicit: true`;
it knew exactly which field needed it.) Write the key yourself for a field that
decides something by another route, such as an argument of the tool whose
answer a `check:` reads.

**A `link:` names a skill that is there.** Checked once the whole folder is
loaded: a link to a skill that is missing, did not load, or has no flow is a
load error for the skill that links to it, with the nearest name suggested.

**Free text cannot reach a tool argument** by any spelling — as a whole
argument, interpolated inside a bigger string (`"note: {{memo}}"`), or nested
in a dict or list. Constrain the field with a `pattern:` or make it an enum.

**`compute:` is comparison-only.** Names, literals, comparisons, `and/or/not`
and `[key]`/`.key` lookups. No arithmetic, calls, f-strings or comprehensions —
do the sum in a tool or a Python `@flow`. A compute whose name collides with a
field is refused (they share one gate namespace), and `flow_run_id` is reserved.

**`workflow: false` means instruction-only for real.** Combining it with
`flow:`, `fields:`, `actions:`, `compute:` or a `handlers.py` is a load error.

**`FieldSpec.default` was removed.** A default value that the user never said
is exactly the thing a gate must not read.

`@action` gained `readonly=` and `requires=` keywords, and an `@action` that a
declarative `do:` dispatches must declare one of them:
`action(fn_or_name=None, /, depends_on=None, readonly=False, requires=None)`.

**How a bad skill behaves in a folder:** `load_skills_dir` is lenient by
default — the skill is logged by name, recorded in `SkillRegistry.errors` and
**skipped**, so it is unreachable while its siblings still load. Pass
`strict=True` to re-raise the first failure. A folder where *nothing* loaded is
always an error.

## Runtime guarantees

Each of these is pinned by a test, and each existed because the opposite once
happened:

- **NaN and infinities are refused at the type boundary.** They used to satisfy
  `min:` and silently switch a threshold gate off.
- **A model-proposed value for a `must_elicit` field, or for a boolean an
  action names in `requires:`, is dropped** — every time. Grounding is a
  whole-token match against what the user actually said, not a substring.
- **Correcting a gate input clears any verification value already collected**
  and every memoized action result derived from it.
- **`flow_run_id` and the effects log survive flow switches, digressions and
  cancellation**, and are checkpointed by `create_agent` / `wrap_agent`: one
  confirmed intent fires exactly one side effect.
- **Structured submissions silently drop keys** that are not declared fields of
  the active skill.
- **Declining a trailing gated `do:`** answers "Okay — no changes were made."
  instead of falling silent.
- **Picking a cancelled run back up never carries an approval or a code with
  it**, and a model that sends both with "continue" changes nothing.
- **A value a `pick:` step saves is the user's click or the id they typed**,
  never a row the model chose from the list it can now read.
- **What the user approved is what runs.** A confirmation that releases a tool
  is a card the engine builds: the tool and the inputs it will run with. What
  was shown is kept in state and compared when the tool is called, for every
  tool that yes releases. A difference means nothing runs and the yes is asked
  for again. A secret is shown as hidden and compared by digest.
- **A yes that is someone else's cannot arrive through the conversation.** Not
  from the user, the model, or a posted form: only through the approvals store.
  A request the flow has moved away from is withdrawn in the same turn.

## A yes from someone else

```yaml
  - confirm: Approve this refund?
    by: manager
    when: amount >= 200
```

The flow stops at the step and files a request in the host's approvals store.
The request carries who is asked, the question, which tool a yes will run with
which inputs, and what the user has answered (never a secret). Its id is a
digest of all of that, so the same request is found again on every turn and a
changed one is a new request.

```python
from aigenos_flow.approvals import ApprovalStore

host.approvals = ApprovalStore()             # or your own, with the same six methods
store = host.approvals
store.all("pending")                         # the inbox
store.decide("APR-3F9A1C", True, by="dana", note="Within policy.")
```

With no `host.approvals`, flows use `aigenos_flow.approvals.default_store`.
The flow takes the answer on its next turn; the bundled chat client looks in
every few seconds, so the requester's page carries on by itself. The server
exposes the same two calls as `GET /flow/approvals` and
`POST /flow/approvals/{id}`, and the Studio's **Approvals** page is built on
them.

What the engine guarantees: the yes cannot come from anywhere else, the tool
runs with what the approver was shown or not at all, cancelling withdraws the
request, and a request for something that has since changed is withdrawn. What
it leaves to you: `by:` is a label, not an identity check, the bundled store
is in memory, and nobody is notified. Put identity in front of `decide`, and
give the host a store that persists and notifies (`request`, `get`, `decide`,
`withdraw`, `all`, `for_run`).

## Reading a skill back

```bash
python -m aigenos_flow.procedure skills/ refund-order
```

prints the skill as a procedure: each step in plain words, every way through
the flow with what it asks and runs, and for each tool that changes something,
what must be true first and who was allowed to supply each input. In code it is
`aigenos_flow.procedure.for_skill(registry, name)`, which returns the page as
data plus `["markdown"]`; the server exposes it as
`GET /flow/skills/{name}/procedure`, and the Studio shows it under
**Procedure** on the Canvas.

It is computed from the file and the tools' signatures. No model is involved,
so it cannot say something the flow does not do. It is what you hand to the
person who has to sign off the workflow and does not read YAML.

## Authoring skills

- **[Steps reference](steps.md)** — the whole language on one page.
- **[The 10-minute tutorial](GUIDE.md)** — a tool, a skill, a run, a test, what
  the loader catches, and the procedure read back.
- **[Developer cookbook](cookbook.md)** — recipes to copy, run by the test
  suite as printed.
- **[Long-form reference](skill-authoring.md)** — fields, guards, guidelines,
  the three-layer binding contract, dynamic columns, auth headers.
- Skills follow the **Anthropic Agent Skills layout**: kebab-case folder
  matching `name:`, `scripts.py` (or `scripts/*.py`) for executable code scoped
  to that skill, `references/` for on-demand docs. Tools every skill shares
  live one level up, in `tools/`.

## Persistence: files + the workspace database

Files are the runtime contract — skills and widgets hot-reload from disk and
belong in git. On top of that, the server keeps a **workspace database**
(SQLite via the standard library — no service, no driver, one file):

| What | Where |
|---|---|
| Skills | `<skills_dir>/<name>/SKILL.md` + `scripts.py` |
| Widgets | `<data_dir>/widgets/<id>.json` |
| Version history + activity | `<data_dir>/studio.db` |

Every save through the API records an immutable revision (identical saves
dedupe). Restores are validated like any save and recorded as new versions.
Fields marked `sensitive: true` are redacted in the audit trail.

Environment variables:

| Var | Meaning | Default |
|---|---|---|
| `FG_SKILLS_DIR` | Skills folder served by `/flow` | `examples/studio_agent/skills` in the monorepo |
| `FG_DATA_DIR` | Widgets/workflows/db location | packaged data dir (set this in production) |
| `FG_STUDIO_DB` | Override just the workspace db path | `<data_dir>/studio.db` |
| `FG_MODE` | Interpreter mode `flex`/`strict` | `flex` |
| `FG_CORS_ORIGINS` | Allowed browser origins | `localhost`/`127.0.0.1` on ports 5173 and 3000 |
| `FG_SKILL_WRITES` | Who may reach the code-writing surface: `off`/`token`/`local`/`open` | `local` (loopback only) |
| `FG_ADMIN_TOKEN` | Bearer token for `FG_SKILL_WRITES=token` (setting it implies `token`) | unset |
| `FG_CONNECTOR_ADMIN_TOKEN` | Gates connector admin; also accepted as the management token | unset |

## REST API (server extra)

`python -m aigenos_flow.main` binds **127.0.0.1** and refuses a public bind
while writes are ungated. Interactive docs are at `/docs` when it is running.

**Read the Default column before you expose the port.** Part of this surface
writes Python to disk and imports it in-process — that is how the Studio edits a
skill, and it is remote code execution by design. With `FG_SKILL_WRITES` unset
it is **reachable from loopback only**: the Studio works on your own machine,
and every row below marked 403 answers 403 to a request from any other host.
Set `FG_SKILL_WRITES=off` to close it to everyone, or `=token` with
`FG_ADMIN_TOKEN` to keep it behind a secret:

| Area | Endpoint | Default |
|---|---|---|
| Chat | `POST /flow` (SSE), `POST /flow/{thread}/resume` | open |
| Chat | `POST /flow/validate-field` (inline field validation) | open |
| Read a skill | `GET /flow/skills`, `GET /flow/skills/{name}/outline`, `.../procedure`, `.../guarantees`, `.../scenarios` | open |
| List tools | `GET /flow/tools` (what each takes, whether it changes anything, which skills use it) | open |
| Read a skill's **source** | `GET /flow/skills/{name}/files` | **403** |
| Write a skill | `POST /flow/skills`, `PUT`/`DELETE /flow/skills/{name}`, `POST /flow/skills/check` (load it, write nothing), `POST /flow/skills/edit` (change it from a sentence, write nothing), `POST /flow/create-skill`, `POST /flow/preview-skill`, `POST /flow/reload`, `POST /flow/skills/{name}/restore/{v}`, `POST /flow/gallery/{name}/install` | **403** |
| Write or run a tool | `GET`/`PUT`/`DELETE /flow/tools/files`, `POST /flow/tools/{name}/run` | **403** |
| Someone else's yes | `GET /flow/approvals`, `POST /flow/approvals/{id}` | **403** |
| Someone else's yes | `GET /flow/approvals/{id}/status` (only whether it was answered) | open |
| Read widgets | `GET /widgets`, `GET /widgets/{id}` | open |
| Write widgets | `PUT`/`DELETE /widgets/{id}`, `POST /widgets/{id}/restore/{v}` | **403** |
| History | `GET /store/revisions/{kind}/{ref}[/{version}]` (its bodies are that same source) | **403** |
| Activity | `GET /store/activity`, `GET /health` | open |
| Protocols | every chat endpoint takes `?protocol=ag-ui\|a2ui\|mcp-ui` | — |

CORS defaults to the local dev origins, never `*`, so a random page in the
operator's browser cannot reach the surface. `FG_SKILL_WRITES=open` logs a
warning on every startup and every request.

The `/flow` rows are enforced by the router itself, so they hold when you
include `make_flow_router(...)` in your own FastAPI app as well as in the
bundled server. The `/widgets` and `/store` rows belong to the bundled server.

### Wire protocols

All three protocols work through **both** `make_flow_router` and
`make_chat_router`, verified end to end by replaying captured SSE bytes through
the frontend's own parser. Both routers emit `TOOL_CALL_RESULT` on a resume turn
and a terminal `STATE_SNAPSHOT` before `RUN_FINISHED`. `make_chat_router`'s
`ChatRequest` is `extra="forbid"` — an unknown body key is a 422, not a silently
ignored field — and it accepts `skill` only for a graph that has flow state.

The honest caveat: "works against this client" is proven; conformance to a
published A2UI or MCP-UI spec revision is not. Both of those adapters track
drafts.

### Frontend notes (`@aigenos/flow`)

The published package is `dist/lib/**` plus `LICENSE`, `NOTICE` and
`THIRD-PARTY-NOTICES.md`, behind a strict `files` allowlist and a prepack gate:
ESM + CJS + a CSS-free `node` condition build, with one rolled-up
`index.d.ts`. `<Chat>` takes `endpoint`, `protocol`, `skill`, `threadId`,
`placeholder`, `emptyState`, `className`, `validateEndpoint`, `onError` and
`voice` (default `true`; `false` hides the microphone and Talk controls).
**Inline field validation is opt-in** via `validateEndpoint` or
`FlowConfigProvider`; with neither, no request is made.
The hook is `useFlowChat` (with `UseFlowChatOptions` / `UseFlowChatReturn`).
The names it carried before 0.3.1 still resolve, are marked `@deprecated`, and
are removed in 0.4.

## Testing without an API key

`TestHost` stands in for the LLM: you script what the model *would* extract,
and the real engine runs the turn — validation, gates, order, effect dedup. A
whole workflow becomes a plain unit test:

```python
from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

host = TestHost()
agent = FlowAgent.from_dir("skills", host=host)    # tools come from skills/tools/
host.queue_commands("send $5000 to mom",
                    SetField(field="amount", value=5000),
                    SetField(field="payee", value="mom"))

chat = agent.session("transfer-funds").user("send $5000 to mom")
assert chat.awaiting == "code"                 # $5000 armed the gate
assert chat.spec.pattern == "^[0-9]{6}$"       # the contract your UI renders

chat.submit(confirm=True)                      # try to force the write...
assert chat.awaiting == "code"                 # ...the gate still holds

chat.submit(code="481920").submit(confirm=True)
assert chat.done
```

`agent.session(flow)` is the whole test surface: `.user(text)` and
`.submit(**fields)` advance a turn and return the session, and `.awaiting`,
`.spec`, `.widget` (the card on screen, such as the confirmation with what a
yes will run), `.done`, `.replies` (the assistant lines from the last turn)
and `.state` are what you assert on. For someone else's yes, `.approval` is
the request the flow is waiting on and `.decide(True, by="dana")` answers it. `host.queue_classification(message, skill)`
scripts routing the same way, for tests that go through `create_agent` /
`wrap_agent` instead of entering a flow by name. Driving raw state works too:
set `pending_user_message` or `pending_field_updates`, call `agent.turn(state)`,
and read `pending_widget` back.

Ten example scripts in [`examples/`](../examples) run with **no API key** and
assert their own claims — each one is a `prove.py` you can read as a spec:

| Script | What it pins |
|---|---|
| `examples/before_after/prove.py` | the prompt-vs-structure policy proof — 42 checks, ~1 second |
| `examples/context_isolation/prove.py` | one flow's state never leaks into another — 26 checks, under a second |
| `examples/studio_agent/proofs/conference_trip/prove.py` | twelve tools, three gates and one Pay button: every trap in one conversation, five identical runs, then the same run with the tools behind an MCP server — 22 checks (18 without the optional `mcp` package) |
| `examples/declarative_flow/prove.py` | a YAML-only skill on the same runtime |
| `examples/studio_agent/proofs/send_money/prove.py` | three flows as one errand: a branch, two hand-offs, each flow's own gates, separate answers, cancelling or saying no inside a flow, then continuing or starting over — 47 checks. The front door is a skill file with no Python: a hand-off is `do: add-payee` |
| `examples/embed_existing_agent/verify_roundtrip.py` | the hand-wired router recipe, zero LLM calls |
| `examples/insurance_account_insights/prove.py` | composed widgets over live-shaped data |
| `examples/subscription_insights/prove.py` | widget identity per step, LLM-free submits, pinned args, skip-ahead |
| `examples/subscription_insights/determinism_check.py` | the same input produces the same widget trace |
| `examples/widget_pipeline/prove.py` | designed widgets from the catalog, end to end |

`pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py` is the scenario
harness: **7 passed**, no key.

## Ship checklist

From the repo root, inside the virtualenv that
[CONTRIBUTING.md](../CONTRIBUTING.md) sets up:

```bash
python -m pytest -q -p no:cacheprovider                    # 1299 passed, 2 xfailed (3 skip without mcp)
npm --prefix frontend run build                            # tsc -b, then the app bundle
npm --prefix frontend run build:lib                        # ESM + CJS + node lib builds
npm --prefix frontend run check:lib                        # the prepack gate
```

`check:lib` is what `prepack` runs, so a failure there is a failure to publish:
it re-counts the primitive registry, asserts the numeric-bound and alias
behaviour of each one, and asserts that rendering fires **zero** network
requests.

The wheel and sdist exclude `studio.db*` and `connectors.sqlite3*`. `backend/`
keeps its own `.gitignore` so hatchling does not walk up and ship the
monorepo's — do not delete it. The npm lib entry
(`frontend/src/index.ts`) must stay dependency-light: nothing in the exported
component graph may import `react-router-dom`.

## Going deeper

- [User guide](user-guide.md) — the Studio, for non-developers
- [Integration patterns](integration-patterns.md) → [integration recipe](integration-recipe.md) — embedding into an existing agent or chat app
- [Managed connectors](managed-connectors.md) — wiring databases
- [Comparison](COMPARISON.md) — architecture-class comparison + evaluation rubric
- [The trip comparison](../examples/studio_agent/proofs/conference_trip/README.md#the-comparison-the-same-tools-with-no-flow)
  — the same twelve tools with and without the flow on a live model, with every
  run it counted.

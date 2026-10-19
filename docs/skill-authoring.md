# The long form: reference

> **Start with [steps](steps.md).** A skill is normally written as a list of
> steps: 20 words, one page, no schema. `steps:` is shorthand for what this
> page documents, and the same loader checks both. You need the long form for
> two things steps do not say: free-form questions and answers inside a flow
> (`chat:`, `assist:`), and per-argument bindings. It is 9 step kinds, 2 guards
> and the frontmatter blocks, 64 exact names in all.
> `aigenos_flow.shortform.explain(frontmatter, registry)` shows what a `steps:`
> block expands to.

How to write long-form `SKILL.md` flows that stay deterministic without
fighting you: field design, guards, guidelines, bindings, dynamic columns,
tool auth, and testing. The per-keyword decision table and the full load-time
contract live in the
[developer guide](developer-guide.md#the-long-form-underneath).

The file is the contract. The Studio's **Canvas** is an alternative editor for
the same file — verbs are buttons, widget ids are dropdowns, guards are labeled
inputs — and every Save round-trips through the same fail-closed loader you get
from `FlowAgent.from_dir`. Those endpoints serve loopback only by default — a
skill can carry Python the engine imports — so the Canvas works on your own
machine and nowhere else. In 0.3.1 the Studio UI does not send an admin token,
so `FG_SKILL_WRITES=token` with `FG_ADMIN_TOKEN` opens these endpoints to API
and CI clients, not to a Canvas on another machine.

---

## Anatomy (and Anthropic-skills alignment)

An aigenos skill uses the [Agent Skills](https://agentskills.io/specification)
layout: a kebab-case folder whose `SKILL.md` frontmatter carries `name` +
`description` (always loaded, drives routing) and whose markdown body is
instructions loaded when relevant. The loader enforces that layout — folder
name kebab-case, `name:` matching the folder, no `README.md` inside a skill
folder.

It is an extension of that format, not a conforming instance of it. The
specification defines six frontmatter keys (`name`, `description`, `license`,
`compatibility`, `metadata`, `allowed-tools`). The keys that make a skill
enforceable here — `steps:`, and in the long form `fields:`, `actions:` and
`flow:` — are this project's own, so the reference validator
(`skills-ref validate`) reports them as unexpected fields. `allowed-tools` also
means something stricter: here it is the list of tools the flow may call
(separated by commas or spaces) and the loader enforces it, where the
specification's field is an experimental pre-approval hint. A tool that reads
only `name` and `description` can discover these skills; one that validates
strictly will reject them.

- `name`: kebab-case, matches the folder.
- `description`: *what it does* + *"Use when …"* trigger phrases. The single
  highest-leverage line: it decides when the skill activates.
- Body: extraction guidance and tone for the interpreter. It is context, not
  policy.

What aigenos **adds** is the deterministic contract: `fields:` (typed slots),
`compute:` (pure comparisons), `actions:` (named tools with bound args and
gates), `flow:` (control flow as data), `allowed-tools:` (capability allowlist)
and `readonly-tools:` (the proven-read subset). Everything the LLM may influence
goes through `fields:`; everything that must not drift lives in the others.

## The mental model (one sentence)

**The LLM fills fields; the runtime does everything else.** Fields are the only
door between natural language and your flow — so field design *is* skill design.

---

## Field design: the questions that matter

### "What if the user gives a last name instead of a first name?"

The field *name* is an internal contract key — extraction is driven by the
**description**. So don't over-model:

```yaml
first_name:
  type: string
  pattern: '^[A-Za-z .-]{1,40}$'
  description: >-
    Any name the user gives for the account holder — first, last, or full
    name. Capture their words.
```

…and make the *tool* match broadly. The pattern is not there to guess the
name's shape — it is there because the value is bound into a tool call, and
constrained input is the security boundary.

The `description:` is also the words the user sees. An `ask:` step takes its
wording from it and nothing else — `prompt:` on an `ask:` step is a load error,
because two places to word one question is one place to forget.

### "Enums are hardcoded — what about a growing catalog?"

Two honest patterns, pick per field:

| | Closed enum | Open catalog |
|---|---|---|
| Declare | `type: enum, values: [...]` | `type: string, pattern: ...` |
| Filled by | the user's words **or** a widget submit | widget submit (ids aren't guessable) |
| Skip-ahead | **zero-click** — "show my netflix sub" skips the grid | one-click — the grid renders (filtered) and the user confirms |
| Fits | stable, policy-relevant sets (`checking`/`savings`) | anything that grows (products, subscriptions, clients) |

`examples/subscription_insights` uses a closed enum *deliberately* — it demos
zero-click skip-ahead. A production catalog wants the open pattern: the id field
stays a constrained string, rows come from the tool, and the LLM can still
fast-path by filling a *hint* field that filters the grid down to one row. Never
make the LLM produce catalog ids from memory: ids enter via widget submits
(LLM-free) or exact user statements only.

### Numbers: what the type boundary accepts

`type: number` / `integer` take the money shapes people actually type and
nothing fuzzier — `$3,000` → `3000.0`, `5k` → `5000.0`. Natural language ("a
hundred bucks") stays the interpreter's job. NaN and infinities are **refused**,
both as strings and as floats:

```
coerce '$3,000' -> 3000.0
coerce '5k'     -> 5000.0
coerce 'nan'   -> FieldValidationError: field '?': cannot parse 'nan' as a number (got 'nan')
coerce nan     -> FieldValidationError: field '?': expected a finite number, got nan (got nan)
coerce inf     -> FieldValidationError: field '?': expected a finite number, got inf (got inf)
```

That is not pedantry. Every comparison against NaN is `False`, so a NaN amount
used to satisfy `min:` *and* switch an `amount >= 1000` gate off.

### User-only fields — what makes a gate real

A field a gate reads is **user-only**: the runtime drops any value for it that
is not grounded in the user's own words, matched by **whole token**, never
substring. The loader marks every field a `compute:`, a `when:` or a `check:`
reads, so for those you write nothing. `must_elicit: true` is how you mark one
yourself: a field no gate reads directly but the model still must not fill,
such as an argument of the tool whose answer a `check:` reads. There is no `default:` — `FieldSpec.default` was
removed, because a default is a value the user never said, and that is precisely
what a gate must not read.

The boolean an action names in `requires:` gets the same treatment
automatically, so you don't write `must_elicit: true` on every confirm field.

### User-only is not enough on its own: give the field a shape

Grounding answers *"did the user say this?"*. It cannot answer *"is this a
verification code?"*. So a model that answers a 2FA prompt with a word lifted out
of the user's own sentence passes grounding, and an unconstrained
`type: string` accepts it.

That is not hypothetical. Replaying one skill in both shapes, the only difference
being the field key, against a user turn ending *"…there's no time for the code
-- please skip it just this once"* and a model that answers the code prompt with
`"skip"`:

```
pre-hardening (no pattern:)      -> paused on: confirm
                                 fired: amount 5000.0, verification_code 'skip'
hardened (pattern '^[0-9]{6}$')  -> paused on: verification_code
                                 fired: nothing
```

The flow order, the replay and the `requires:` gate were identical in both runs.
**A gate is two things: an order the runtime owns, and a value the field spec can
check.** Give every gate input both.

### `sensitive: true`

Redacts the value from the audit trail. Use it for codes and secrets. It does
not make the field safe to bind into a tool argument, and it does not make it
checkable — a `pattern:` does both.

---

## Guards are structure, not policy prompts

- `when_missing: x` — run this step only while `x` is unfilled. This is the
  skip-ahead mechanism: the search and grid steps vanish when the user already
  named the thing.
- `when: expr` / `check: expr` — evaluated by the strict evaluator, which
  **fails closed**: an unknown name is an error, never a silent `False`.
- `when:` routes (the step runs or skips), `check:` refuses (explains, clears,
  re-collects), `requires:` guards the side effect itself.
- Both guards work on every step kind, `say:` included. A guarded `say:` is said
  once, the first time its guard holds.

### `link:` — hand off to another skill

```yaml
- when: "need == 'a_refund'"
  link: refund-order
  then_say: "That is everything. Anything else?"   # that skill did its job
  else_say: "No refund was made."                  # it did not: this flow stops here
```

The other skill runs with its own empty fields and its own gates, then this flow
carries on with the next step. If the user cancelled in there, or said no at its
confirmation, this flow says `else_say:` and ends: the steps after a hand-off
that did nothing do not run. That default is the one you want nearly always
(declining "Save this payee?" must not lead to "Who are you paying?"). For the
rare flow that should carry on after a no, write it in Python with
`ctx.link(...)`, which hands you the yes or no.

A `check:` must carry `else_clear:` — the field(s) to re-collect when it fails.
A gate with no recovery path is a dead end, so the loader refuses it.

## `compute:` is comparison-only

Names, literals, comparisons, `and`/`or`/`not`, and `[key]` / `.key` lookups.
No arithmetic, no calls, no f-strings, no comprehensions — do the sum in a tool
or a Python `@flow`. A compute is never memoized: it is recomputed on every
replay, which is *why* a corrected field re-arms every downstream gate.

```yaml
compute:
  needs_2fa: "amount >= 1000"
```

Names live in one namespace with fields and action results, so a compute that
collides with a field name is a load error, and `flow_run_id` is reserved.

## Guidelines — natural-language rules, first-class

Not everything is a step. Tone, language, escalation policy, "never promise
delivery dates" — these are **guidelines**: rules the model follows in every
reply.

```yaml
guidelines:
  - Always answer in the user's own language.        # bare string = always
  - when: the user asks about cancelling
    then: explain the steps neutrally; never persuade them to stay
    tools: [get_subscription_details]                # must be in readonly-tools
```

They are injected into the interpreter and into every `chat:` / `assist:`
answer. **The honest split:** guidelines are *followed by the model*; gates and
actions stay *engine-enforced*. A guideline can steer wording and suggest a
read — it can never fire a side effect, and the loader proves it: a guideline
naming a tool outside `allowed-tools`, an unregistered tool, or a tool that is
not declared read-only refuses to load, with the reason.

Two ends of the spectrum, same file format:

- **Deterministic workflow** — `flow:` steps own the path; guidelines polish the
  voice along it.
- **Guideline-driven agent** — almost no fixed path: a flow that collects the one
  or two fields it must scope on and then ends in `chat:` (its own tool
  allowlist, `pin:` arguments taken from validated state), plus guidelines for
  everything else. Natural conversation, policy still structural — the model
  picks *which* read answers a question, but never the pinned arguments:

  ```
  chat tools  : ['get_subscription_details']
  pin         : {'customer_id': 'customer_id'}
  guideline in the chat system prompt: True
  ```

---

## Actions: read or write, never ambiguous

**Every action is a write unless it declares `readonly: true`.** That default is
the point: a tool nobody classified is assumed to have consequences.

```yaml
actions:
  get_subscriptions:                  # a read
    tool: list_subscriptions
    readonly: true
    audit: false                      # allowed only because it is read-only
    args: { customer: "{{first_name}}" }

  cancel_subscription:                # a write
    tool: cancel_subscription
    requires: confirm                 # a declared BOOLEAN field
    idempotency_key: "{{flow_run_id}}"
    args: { subscription_id: "{{subscription_id}}" }
```

A write must:

- name a declared **boolean** field in `requires:`;
- keep `audit: true` (the default) — `audit: false` is for reads only;
- be dispatched from a `do:` step guarded by a `when:` that *proves* that
  boolean. `when: "confirm"` proves it. `when: "confirm is not None"` and
  `when: "not confirm"` do not, and are refused, because they let the step run
  after the user declines;
- include `{{flow_run_id}}` in `idempotency_key:` if it sets one. A bare
  business key widens the dedup window, so a correction would re-fire the effect
  inside the same run.

Reads re-run automatically after a correction; a completed write does not.

A `handlers.py` `@action` plays by the same rules —
`@action(readonly=True)` or `@action(requires="confirm")`, and `ctx.run`
re-checks the gate at dispatch, so the Python escape hatch is not a hole.

### Telling the user what happened: `then_say:`

A `say:` step can only read fields, and an action cannot speak. So a flow that
ends on its write would end in silence. Put the outcome on the `do:` step:

```yaml
  - when: "confirm"
    do: book_table
    then_say: "Your table is booked. Reference: {{book_table.reference}}."
```

`then_say:` is emitted **once, at the moment the action actually fires** — not
on the replays that follow, and not at all if the user declined. It is the one
place a template may quote an action's own result with `{{action.key}}`; a key
the result does not have renders blank rather than failing a write that has
already happened. The loader checks that every name in it is declared.

Keep the gated `do:` as the **last** step. When the user declines, the runtime
then says nothing was changed — a `say:` placed after it would run either way.

### Walk every answer before you ship

A flow that passes its happy path can still leave a person staring at the
screen. Each of these shipped in this repo's own examples and was found by
answering every question both ways, as someone who had never seen the skill:

- **A Yes with no reply.** A gated `do:` with no `then_say:` books the room and
  says nothing. The only answer that got an acknowledgement was No. Give every
  write a `then_say:`, and let the tool build the sentence so it is also right
  when the write fails.
- **A question nothing reads.** An `ask: confirm` at the end of a flow with no
  action behind it: Yes and No land on the same empty screen. If no step reads
  the answer, do not ask.
- **A yes/no that hides a choice.** "Is this someone you have not paid
  before?" made No mean "go on and pay". Ask a choice as an `enum`, so each
  option names where it leads, and have each branch say which way it went.
- **A refusal for the wrong reason.** One `check:` covering two failures says
  the wrong one half the time ("outside the 30-day window" for an order number
  that does not exist). Use one `check:` per reason.
- **A refusal that ends in a question.** "Is there another order?" followed by
  a list that cannot take a no. End an `else_say:` on what to do next.
- **A tool that raises on a mistyped id.** The turn fails with nothing on
  screen. Return `ok: false` and let a `check:` say it in words.

Then walk the ways out of the flow, at more than one step: **cancel**
("never mind"), **continue** afterwards, **start over** part-way, and **"let me
change X"** for something already answered. You do not author any of them (see
[What the conversation remembers](developer-guide.md#what-the-conversation-remembers)),
but your wording is what the user reads when they come back: a resumed flow
re-asks the question it stopped on, so that question has to make sense on its
own.

What the runtime now does for you, so you do not write it: a value that fails
validation is refused in words (an `enum` with the same labels as its dropdown,
a bound as "the minimum is 15"); a typed answer to a user-only question that
cannot be tied to the user's own words is answered with what will be accepted
instead of "Okay."; a change that voids an approval says so; a list is read
again when its flow resumes after another one ran; a `pick:` over an empty
result says nothing matched and asks again for what the lookup was given; and
an empty box is not accepted as the answer to a required question.

Three rules about a yes, because each one was a way to send money without
being asked:

- **A yes counts for the question on screen.** A confirmation submitted while
  the flow is asking for something else is dropped, so a yes delivered twice
  cannot answer the next flow's "Send this payment?".
- **A yes that arrives with a change that did not land is not a yes.** "Yes,
  but one more night": if the runtime cannot tie the new value to the user's
  words, nothing is changed and the confirmation is asked again.
- **A question is not a yes.** "Is that the right category?" does not confirm,
  whatever the model makes of it.

On a compiled graph, a step that fails after one of its writes has fired keeps
the record of that write. The user is told, and the next message carries on
from the step that failed; what already went through is not repeated.

### What "Create skill" generates

Describing a workflow in the Studio writes a complete skill: one `ask:` per
field (including whatever the action needs that you did not mention — *which*
restaurant, not only when), a summary, an `ask: confirm`, a gated `do:` with a
`then_say:`, and a `handlers.py` holding the one `@action`. A rule in the
description becomes a rule in the flow: "anything over $500 needs a manager's
name" is written as `when: amount > 500` on that question, not left in the
prose, and a code or password is marked `sensitive: true`. It writes
straight-line flows with conditions; lists to pick from, checks and hand-offs
are still yours to add on the canvas. As generated, that
action appends the request to `records.jsonl` in the skill folder and returns a
reference, so the flow runs end to end. Replace its body with the call to your
real system; nothing else in the skill has to change.

---

## Widgets and dynamic data

### Binding contract — what `{{get_subscription_details.plan}}` really does

Three layers, three behaviors — know which one you're in:

1. **Load time (fail-closed):** every binding **root**
   (`get_subscription_details`) must be a declared field, compute, or action
   result. A typo'd root is a rejected save with the reason.
2. **Display bindings** (widget `data:`, `show:`, `then_say:`): resolved
   leniently. If the tool's shape changed and `.plan` is missing, you get a
   blank — a cosmetic bug, never a crash and never a wrong action. `say:` is
   narrower: it takes whole `{{field}}` / `{{compute}}` names only, and a dotted
   path in a `say:` or `else_say:` is a load error that names the fix (quote a
   result in `then_say:` on the `do:` step, or render it with `show:`).
3. **Gates and tool arguments** (`when:` / `check:` / action `args`): strict.
   Missing names fail closed. Display can degrade; decisions can't.

Tool arguments are stricter still. A `{{...}}` in an action argument must name a
whole declared field or compute — not a dotted path into a result, and not
nested inside a dict or list, because the binder only resolves the top level of
an argument and would otherwise hand the tool the literal template text.

The corollary: **the tool's return shape is a contract**. Keep an adapter at the
tool boundary that returns stable keys (`{"subscriptions": [...]}`) and version
it like any API. The flow references the adapter's shape, not the upstream's.

### How a `pick:` renders

The grid is the default; three modes, one step:

```yaml
- pick: "{{search_clients.matches}}"      # 1. auto-generated GRID
  save: [client_id]
  prompt: Select a client

- pick: "{{find_accounts.accounts}}"      # 2. auto-generated AUTOSUGGEST
  as: autosuggest                         #    (type-to-filter)
  display: account_number                 #    which column is shown
  save: [account_number]

- pick: "{{find_accounts.accounts}}"      # 3. YOUR designed widget, by id
  widget: account_picker
  save: [account_number]
```

`as:` accepts `datatable` or `autosuggest`, and `as:` + `widget:` on the same
step is refused — pick one. Driving a skill that uses modes 1 and 2, then a
`show:` and a `chat:`, gives the trace:

```
1. client grid  : pick:client_id -> ['card', 'form', 'datatable']
   columns from the data: ['client_id', 'full_name']
2. account pick : pick:account_number -> ['card', 'form', 'autosuggest']
3. show:                               -> ['card', 'column', 'row', 'text', 'text', 'row', 'text', 'text']
4. chat open    : True
5. pinned arg   : {'account_number': 'account_number'}
```

### Dynamic columns

Two options, by intent:

- **Columns follow the data** → auto-widgets (`pick:` / `show:` with no widget
  id). Columns are derived from the row keys at render time, as the trace above
  shows; a new key in the payload appears on its own.
- **Columns are designed** → a catalog widget, and bind them:
  `"columns": "{{columns}}"` in the widget spec plus
  `data: { columns: "{{find_accounts.columns}}" }` on the step. Whole-value
  bindings substitute arrays, not just strings — `rows: "{{rows}}"` is the same
  mechanism.

Fixed-by-id and dynamic are not opposites: the widget *identity* (layout,
typography, section order) is fixed; the data flowing through it is live.

A catalog id is **not** resolved at load time, so a skill naming a widget you
have not designed yet still loads. At render the step emits the id with
`spec: None`, and resolving it is the client's job:

```
widget id : account_picker_does_not_exist
spec      : None
keys      : ['data', 'fields', 'spec', 'submit_schema', 'widget']
```

So a typo in a `widget:` id is not a load error and not a crash. The engine
hands every catalog step over as an id with `spec: None`, valid or not; the
bundled `/flow` router then looks the id up, and for one it cannot find logs a
warning and falls back to an auto-generated input for the step's first `save:`
field — the widget you designed silently never appears. Keep widget ids under
test (drive the step with `TestHost` and assert
`chat.state["pending_widget"]["widget"]` is a key of
`aigenos_flow.engine.loader.load_widgets()`), or use an auto-widget, where
there is no id to get wrong.

### Composition

A widget is a spec **tree**, so one catalog id is as composed as you like.
[`examples/subscription_insights/widgets/subscription_details.json`](../examples/subscription_insights/widgets/subscription_details.json)
is one id whose spec walks:

```
card, row, badge, badge, badge, alert, divider, text, datatable, divider, text,
timeline, form, input
```

If a step must show "a combination of widgets", design the combination as one
widget.

---

## Tools, auth, and MCP

**Credentials never appear in SKILL.md and never pass through the LLM.** A skill
references tools by *name*; what a name does — and what `Authorization` header it
sends — is wired where the tool is registered. Three places, in precedence
order:

1. **Injected** — `FlowAgent.from_dir(..., tools=...)`, `create_agent(tools=...)`,
   `wrap_agent(tools=...)`. A list of LangChain tools / plain callables, or a
   `{name: callable}` dict. Wins every collision, so a test can always
   substitute a double.
2. **Managed connectors** (Studio → Connectors): PostgreSQL / MongoDB operations
   with validated parameters, gated writes, an at-most-once boundary and audit.
   A connector descriptor also *proves* whether an operation reads or writes, so
   a managed read needs no `readonly-tools:` entry.
3. **The skill's own scripts** (the Anthropic layout) — `<skill>/scripts.py` or
   `<skill>/scripts/*.py`: plain Python callables (your REST client with its
   auth header, an MCP wrapper, mocks while prototyping). Auto-registered **for
   that skill only**, so a sibling skill cannot call them and `allowed-tools:`
   stays the complete audit answer. Export `TOOLS = {…}` or just define public
   functions. Same trust boundary as `handlers.py`: operator code on the
   operator's server.

Injected and managed names win over a skill's own scripts, and a shadowing
attempt is logged.

```
subscription-insights/          kebab-case, matches `name:`
├── SKILL.md                    frontmatter contract + body instructions
├── scripts.py                  this skill's tools (auth lives here)
└── references/
    └── subscription-api.md     shape contract, loaded on demand
```

That is a real skill:
[`examples/subscription_insights/skills/subscription-insights/`](../examples/subscription_insights/skills/subscription-insights).

```python
# scripts.py — adapter from your upstream API to the tool contract
import os, httpx

_H = {"Authorization": f"Bearer {os.environ['ACCOUNTS_API_TOKEN']}"}

def search_clients(args):
    r = httpx.get(f"{os.environ['API']}/clients",
                  params={"q": args["name"]}, headers=_H, timeout=10)
    r.raise_for_status()
    return {"matches": [{"client_id": c["id"], "client_full_name": c["name"]}
                        for c in r.json()["items"]]}
```

MCP servers that need auth headers: wrap the MCP call in a local tool — the
header lives in that wrapper's config/env. The flow calls a tool
**synchronously**, and tools loaded with `langchain-mcp-adapters` are
async-only, so passing one straight into `tools=` fails at the first call with
*"StructuredTool does not support sync invocation"*. Wrap it in a plain
function that takes the args dict and runs the call to completion:

```python
import asyncio

def subscription_details(args: dict) -> dict:
    return asyncio.run(mcp_tool.ainvoke(args))    # mcp_tool: from load_mcp_tools(...)
```

> A `tools.py` at the **root of a skills folder** is a Studio-server
> convenience, loaded by the bundled `/flow` router so a declarative skill is
> runnable from the UI while you prototype. `FlowAgent.from_dir` does **not**
> read it — a skill whose action names a tool from there fails to load with
> *"not registered with the host ToolRegistry — grant it via
> FlowAgent.from_dir(tools=...)"*. Prefer per-skill `scripts.py`.

### Databases

`aigenos_flow.integrations.databases` exposes `MongoTools` and `RedisTools`,
which build read tools from a query template you write: `find(...)` returns
`{"rows": [...], "count": n}` (bind a `pick:` / `show:` to `{{tool.rows}}`) and
`find_one(...)` returns `{"found": bool, "doc": {...}}` (gate with
`check: "validate_account.found"`). The model and the flow only fill typed
values into your template; an unreferenced argument is ignored, so a model
cannot widen a query, and a referenced-but-missing one fails closed. A value
bound into a `$regex` is still a regex — keep the feeding field
`pattern:`-constrained (the loader enforces this for tool args) or pass
`regex_escape=["name"]`. Constructing either class needs the driver:
`pip install "aigenos-flow[mongodb]"` / `"aigenos-flow[redis]"`.

Redis writes (`set_value`) are side effects: declare them as `actions:` with
`requires:` like any other effect.

*Conversation persistence* is separate: the bundled server holds conversations
in memory by default, and they are lost on restart. `FG_TRACKER_STORE_URL`
(`redis://…`, `mongodb://…`, `postgresql://…`, `sqlite:///…`) is experimental
and needs a `langgraph-checkpoint-*` package you install yourself; without it
the server logs a warning and stays in memory.

---

## What is now a load error

These are the ones an author actually trips over. Several are **breaking
changes** — a SKILL.md written for the pre-0.3.1 syntax may not load now. All of them fail
before any user talks to the skill, and the message names the fix.

Every message below is quoted from an actual load, with its leading
`skill '<name>': ` (or `SKILL.md in <path> is not loadable: `, for the ones
pydantic raises) removed and long lines wrapped.

**Typos, everywhere.** An unknown or misspelled key is rejected in frontmatter,
in a field spec, in an action spec, in a guideline, in a flow step, and in a
`chat:` / `assist:` block. `require:` instead of `requires:` used to be
swallowed, which deleted a gate.

```
frontmatter has unknown key 'allowed-tool' (did you mean 'allowed-tools'?)
field 'verification_code' has unknown key 'sensitve' (did you mean 'sensitive'?)
field 'verification_code' has unknown key 'default'
flow step 4 check: references undeclared name(s) ['balnce'] (fields/computes
  ['amount', 'confirm', 'contractor', 'flow_run_id', 'needs_2fa',
  'verification_code'], action results ['pay'])
```

**One verb per step, with a value, and no modifier the verb ignores.** The
documented `pick:` + `widget:` pair is the one exception.

```
flow step 1 carries 2 verbs ['ask', 'say'] — a step does exactly one thing and
  the extra one would be silently dropped. Split it into separate steps.
flow step 1 ask: is empty — give it a value (e.g. `ask: amount`)
flow step 1 is an `ask:` step, which never reads 'prompt' — the wording of an
  `ask:` comes from the field's `description:`. Remove it or split the step.
```

**Reads must say so; writes must be gated.**

```
action 'pay' is a write (every action is, unless it declares `readonly: true`),
  so it must declare `requires:` naming a declared boolean confirmation field —
  declare `readonly: true` if it only reads
flow step 4 runs gated action 'pay' but is not guarded — add `when: "confirm"`
  to the step
flow step 4 runs gated action 'pay' under `when: 'confirm is not None'`, which
  does not prove 'confirm' is true (a negated test, or `is not None`, lets the
  step run after the user declines). Use `when: "confirm"`.
write action 'pay' has idempotency_key: '{{contractor}}', which does not include
  {{flow_run_id}} — a business key widens the dedup window, so a correction
  would re-fire the effect in the same run. Use "{{flow_run_id}}" (optionally
  with more parts).
```

**A gate input is user-only — no longer an error, because the loader does
it.** Leaving `must_elicit: true` off a field that a `compute:`, a `when:` or a
`check:` reads used to stop the file loading. The loader now marks that field
itself, wherever the comparison is written.

**A `link:` to a skill that is not there.**

```
skill 'help-desk': `link: refund-ordr` names a skill that is not a skill in this
  folder (did you mean 'refund-order'?)
```

**Free text cannot reach a tool argument, by any spelling.**

```
action 'pay' binds 'verification_code' into tool arg 'otp', but it is an
  unconstrained string field — add a `pattern:` or use an enum (prevents
  injection through a free-text value). Only constrained values (pattern, enum,
  number, integer, boolean) may reach a tool argument.
action 'pay' arg 'meta' puts the binding '{{amount}}' inside a nested structure;
  the runtime does not resolve bindings below the top level of an argument, so
  flatten it into its own argument (or pass the container as a fixed literal)
```

**`compute:` is comparison-only, and names don't collide.**

```
compute 'needs_2fa' uses BinOp, which the expression evaluator cannot run:
  'amount * 2'. A condition may use names, literals, comparisons, and/or/not,
  and [key]/.key lookups — no arithmetic, calls, f-strings, or comprehensions.
compute ['amount'] collide with declared field name(s) — fields and computes
  share one gate namespace, so the collision would silently decide which value a
  gate reads. Rename the compute.
```

**A conversational leaf only gets proven reads, and `chat:` goes last.**

```
chat: tool 'get_balance' is not declared read-only, so it may not be used where
  the model chooses the arguments. Add it to `readonly-tools:` if it only reads;
  if it writes, run it from a `do:` step with `requires:` instead.
flow step 3 chat: must be the LAST step — the flow stays in open conversation
  until the user switches or cancels
```

**A gate needs a recovery path, and `workflow: false` means what it says.**

```
flow step 4 check: needs `else_clear:` — the field(s) to re-collect when the
  check fails (that's its recovery path)
`workflow: false` means instruction-only (body-as-prompt, never routed), but the
  skill also has fields:, actions:, compute:, flow:. Remove `workflow: false`,
  or remove the typed blocks / handlers file.
```

**How a bad skill behaves in a folder.** `load_skills_dir` is lenient by
default: the skill is logged by name, recorded in `SkillRegistry.errors` and
**skipped** — unreachable, while its siblings still load. Skipping is
fail-closed, so a rejected skill can never run. Pass `strict=True` to re-raise
the first failure. A folder where *nothing* loaded is always an error:

```
no skill in skills_py could be loaded: pay-contractor (ParserError: while
  parsing a flow mapping ... expected ',' or '}', but got '?')
```

(That one is plain YAML: an unquoted `?` inside a `{ }` flow mapping. Quote
descriptions that end in a question mark.)

---

## Testing (the guide's metrics, made executable)

Anthropic's guide asks for trigger rate, "workflow completes without
correction", and consistency across sessions. `TestHost` makes those into
assertions: you script what the model *would* extract, and the real engine runs
the turn — validation, gates, order, effect dedup.

```python
from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import SetField

host = TestHost()
agent = FlowAgent.from_dir("skills", host=host, tools={"send_payment": send_payment})
chat = agent.session("pay-contractor")

host.queue_commands("pay acme 2500", SetField(field="amount", value=2500),
                    SetField(field="contractor", value="acme"))
chat.user("pay acme 2500")
assert chat.awaiting == "verification_code"       # $2500 armed the gate
assert chat.spec.pattern == "^[0-9]{6}$"          # the contract your UI renders

chat.submit(confirm=True)                         # try to force the write…
assert chat.awaiting == "verification_code"       # …the gate still holds

chat.submit(verification_code="481920").submit(confirm=True)
assert chat.done
```

`host.queue_classification(message, skill)` scripts routing, for tests that go
in through `create_agent` / `wrap_agent` rather than entering a flow by name.
`host.queue_chat_answer(substring, answer)` scripts a `chat:` reply.

### Ask the loader what it proved

For a skill that *did* load, `aigenos_flow.guarantees` reports which properties
are actively doing work — and `n/a` honestly means "nothing to bite on here",
not "passed":

```python
from pathlib import Path
from aigenos_flow.guarantees import analyze_markdown

report = analyze_markdown(Path("skills/pay-contractor/SKILL.md").read_text(encoding="utf-8"))
for p in report["properties"]:
    print(f"{p['status']:<6} {p['id']:<24} {p['title']}")
```

```
holds  names-resolve            Every name resolves
holds  no-free-text-to-tools    Free text never reaches a tool argument
holds  gated-effects            Side effects run only behind their gate
holds  audited-writes           Writes are audited and idempotent
n/a    gate-recovery            Every gate has a recovery path
n/a    chat-leaf                Chat is a terminal, read-only leaf
holds  acyclic-compute          Computed values are acyclic
holds  at-most-once             Gated writes fire at most once per run
```

---

## Worked examples

All of these run with **no API key** and assert their own claims:

| Example | What it shows |
|---|---|
| [`examples/before_after/`](../examples/before_after) | the same policy as prose and as structure — 42 checks, about a second |
| [`examples/subscription_insights/`](../examples/subscription_insights) | REST list → single-select grid → MCP details → composed card+grid+timeline → scoped chat, with zero-click skip-ahead |
| [`examples/insurance_account_insights/`](../examples/insurance_account_insights) | the same shape with two grids, a validation gate with recovery, `assist:` and pinned chat |
| [`examples/widget_pipeline/`](../examples/widget_pipeline) | designed catalog widgets end to end, each submit driving the next step |
| [`examples/context_isolation/`](../examples/context_isolation) | one flow's state never leaks into another — 26 checks |
| [`examples/studio_agent/proofs/send_money/`](../examples/studio_agent/proofs/send_money) | more than one flow: a router skill that branches on an answer and hands off to two other skills, plus a branch inside one of them — 47 checks |
| [`examples/studio_agent/proofs/conference_trip/`](../examples/studio_agent/proofs/conference_trip) | twelve tools wired into one flow, as Python functions and as an MCP server: tool → grid → gate, twice, then a tool-priced total, an approval gate and a checkout widget |

`examples/subscription_insights/determinism_check.py` re-runs each scenario and
asserts one distinct decision trace per scenario.

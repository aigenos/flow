# Steps: the whole language on one page

A skill is a list of steps. There are five kinds of step, two words for
conditions, one for a list of items, and twelve more words that are options on
a step: **20 in all**.
Every one is checked when the file loads, and a word that is not one of them is
an error that names the nearest valid one.

Nothing here needs a schema, a graph or Python in the skill. The skill names
tools; the tools are plain functions in one shared folder.

- [A complete example](#a-complete-example)
- [The five kinds of step](#the-five-kinds-of-step): [ask](#ask), [say](#say), [do](#do), [confirm](#confirm), [chat](#chat)
- [Conditions](#conditions-when-and-check): `when`, `check`
- [Repeat: a list of items](#repeat-a-list-of-items)
- [Tools](#tools)
- [What is worked out for you](#what-is-worked-out-for-you)
- [What you approve is what runs](#what-you-approve-is-what-runs)
- [A yes from someone else](#a-yes-from-someone-else)
- [What the loader refuses](#what-the-loader-refuses)
- [Reading a skill back](#reading-a-skill-back)
- [Every word](#every-word)
- [When steps are not enough](#when-steps-are-not-enough)

## A complete example

Two files. The test suite writes them to a folder exactly as printed and runs
the conversation below, so this page cannot drift from the engine.

**`skills/refund-order/SKILL.md`**

```yaml
---
name: refund-order
description: Refund a customer's order. Use when the user wants a refund or wants to return something.
steps:
  - ask: { order_id: "Which order is this about?" }
    from: list_orders.orders
    check:
      find_order.found: "I can't find that order. Pick one from the list."
      find_order.refundable: "That order is outside the 30-day refund window. Pick another."
  - say: "{{find_order.summary}}"
    title: Your order
  - ask: { reason: "What went wrong with it?" }
    choices: [damaged, wrong_item, late, changed_mind]
  - ask: { notes: "Anything else we should know? (optional)" }
  - confirm: Refund this order?
  - confirm: Approve this refund?
    by: manager
    when: find_order.large
  - do: refund
  - say: "Refunded. Reference {{refund.reference}}."
---
Help the customer get a refund. Never guess an order from the item they
describe: the flow shows them their orders.
```

**`skills/tools/orders.py`**

```python
"""The order desk's tools. Every public function here is a tool a skill can name."""
from aigenos_flow import readonly

ORDERS = {
    "A-100": {"item": "Headphones", "total": 249.0, "days": 12},
    "A-200": {"item": "Desk lamp", "total": 64.0, "days": 45},
}
REFUNDS: list = []


@readonly
def list_orders() -> dict:
    """The customer's recent orders."""
    return {"orders": [{"order_id": k, "item": v["item"], "total": v["total"]} for k, v in ORDERS.items()]}


@readonly
def find_order(order_id: str) -> dict:
    """One order, and what the refund policy says about it."""
    order = ORDERS.get(order_id)
    if order is None:
        return {"found": False, "refundable": False, "large": False}
    return {
        "found": True,
        "refundable": order["days"] <= 30,
        "large": order["total"] >= 200,
        "summary": {"Item": order["item"], "Total": f"${order['total']:.2f}"},
    }


def refund(order_id: str, reason: str, notes: str = "") -> dict:
    """Refund the order to its original payment method."""
    REFUNDS.append((order_id, reason, notes))
    return {"reference": f"RF-{len(REFUNDS):04d}"}
```

What the file never says, and the engine does anyway:

- `order_id` is a pick from a list, so only the user can give it.
- `find_order` is never run as a step. It runs when a rule first needs its
  result, and again when `order_id` changes.
- `refund` is not marked `@readonly`, so the file would not load without a
  `confirm:` above it. It runs at most once.
- The yes is given to a card that says `refund` will run, with which order and
  which reason. If any of that changes afterwards, `refund` does not run.
- An order of $200 or more waits for a manager. Neither the customer nor the
  model can answer for the manager.

## The five kinds of step

### ask

Get an answer and keep it under a name.

```yaml
- ask: { amount: "How much do you want to send?" }
```

The name is how later steps and tools refer to the answer: `{{amount}}` in a
sentence, `amount >= 1000` in a condition, a tool input called `amount`.

| Option | What it does |
|---|---|
| `choices: [a, b]` | The answer is one of a fixed list. Shown as a select. |
| `from: tool.rows` | The answer is a pick from the rows a tool returns. Shown as a list to pick from. Only a tool marked `@readonly` can fill one. |
| `type:` | `text`, `number`, `whole number`, `yes/no` or `secret`. Usually not needed: see [what is worked out](#what-is-worked-out-for-you). |
| `pattern:` | The shape of a typed answer (a code, an id, a date), as a regular expression the whole answer must match. It also makes the answer the user's own to give. |
| `check:` | What the answer must satisfy. See [conditions](#conditions-when-and-check). |
| `widget:` | Show a widget you designed instead of the built-in input. `with:` gives it data. |
| `when:` | Ask only if a condition holds. |

A question that ends `(optional)` may be left blank. Only a text answer can be:
for a number or a choice, ask a yes/no first and put `when:` on the question.

`ask: amount` on its own takes the question from the tool that receives the
answer, or asks "What is the amount?". One `ask:` may hold several questions;
they are asked one after another.

A pick fills the answer from the row's key of the same name. An answer called
`something_id` takes the row's id when the row has exactly one.

### say

Tell the user something.

```yaml
- say: "You're sending ${{amount}} to {{payee}}."
```

`{{name}}` is an answer. `{{tool.key}}` is a tool's result, and the tool runs
when the line needs it. Quote a sentence that starts with `{{`.

A line that is only a result, such as `say: "{{find_order.summary}}"`, depends
on what the result is: a record or a list is shown as a card (`title:` names
it), and a sentence or a number is said.

```yaml
- say: "Here are the details."
  widget: subscription_details
  with: { plan: "{{get_details.plan}}", next_billing: "{{get_details.next_billing}}" }
```

`widget:` shows a widget you designed below the sentence, and `with:` gives it
its data. It is shown, not answered: nothing waits on it.

### do

Run a tool from the library, or another skill.

```yaml
- do: send_payment
```

A tool receives the answers whose names match its inputs. Nothing else is
passed, and nothing is declared twice.

| Option | What it does |
|---|---|
| `with: { to: "{{email}}" }` | Pass an input under another name, or pass a fixed value. |
| `as: second_quote` | Read this result by another name. Needed to run the same tool twice. |
| `else: "No payee was added."` | For `do: another-skill` only: what to say if that skill did not finish. |
| `when:` | Run only if a condition holds. |

A tool that is not marked `@readonly` changes something. It needs a `confirm:`
above it on every path that reaches it, it runs at most once per conversation,
and each run is on the audit trail.

You rarely write `do:` for a lookup. Mention its result and it runs.

`do: add-payee` hands the user to another skill. That skill asks its own
questions and keeps its own rules. When it finishes, this flow carries on at
the next step. If it is cancelled, or the user says no in it, this flow says
the `else:` line and stops.

### confirm

Get a yes.

```yaml
- confirm: Send this payment?
```

"No" stops the flow, and nothing after the step runs. Only the user can answer:
a yes the user did not give is dropped.

| Option | What it does |
|---|---|
| `by: manager` | The yes is someone else's. See [a yes from someone else](#a-yes-from-someone-else). |
| `widget:` | A page you designed for the yes (a checkout, a review screen). `with:` gives it data. |
| `when:` | Ask only if a condition holds. A yes that was not asked for blocks nothing. |

`confirm: { pay: "Pay for this trip?" }` gives the yes a name of your choosing,
for a designed widget that submits it under that name.

### chat

The last step: answer the user's questions.

```yaml
- chat: "Ask me anything about this subscription."
  tools: [get_details, list_invoices]
```

The model answers whatever the user asks, calling only the tools listed, and
every one of them must be marked `@readonly`: nothing can change here. A tool's
input that has an answer of the same name above is given that answer, fixed,
and the model chooses the rest. Such an answer has to be a checked one (a
choice, a number, or one with a `pattern:`), so that only a checked value
reaches the tool. The conversation stays in the chat until the user moves on.

| Option | What it does |
|---|---|
| `tools: [a, b]` | The lookups it may call. |
| `when:` | Chat only if a condition holds. |

## Conditions: `when` and `check`

A condition is a short expression: names, numbers, quoted text, comparisons,
and `and` / `or` / `not`.

```yaml
when: amount >= 1000
when: plan == 'gold' and not is_trial
when: find_order.found
```

A name is an answer asked above, a tool's result (`tool.key`), or a `compute:`
name. A condition compares; it does not calculate. Put arithmetic in a tool.

**`when:`** makes one step conditional. For several steps, write a block:

```yaml
- when: pay_to == 'someone_new'
  steps:
    - say: First we add them as a payee.
    - do: add-payee
```

**`check:`** says what an answer must satisfy, with the sentence to say when it
does not. The question is then asked again.

```yaml
- ask: { amount: "How much?" }
  check:
    amount > 0: "Enter an amount above zero."
    quote_payment.within_balance: "That is more than your balance."
```

A rule that reads only its own answer (`amount > 0`) is applied the moment a
value arrives, so a form can refuse it before it is submitted. A rule that
reads a tool is written under the answer it judges and runs as soon as the tool
has what it needs: a rule on the routing number may need the account number
asked two lines later.

## Repeat: a list of items

Some things come in lists: the travellers on a booking, the lines of an
expense report. `repeat:` asks the questions under it once per item.

```yaml
- repeat: { travellers: "Add another traveller?" }
  steps:
    - ask: { traveller_name: "What is the traveller's full name?" }
    - ask: { passport: "Passport number?" }
      pattern: '^[A-Z0-9]{6,9}$'
      check: { check_passport.valid: "That passport is not valid." }
- confirm: Book these seats?
- do: book_tickets
```

When an item's questions are answered, the repeat's question is asked. A yes
starts the next item; a no ends the list. Each item's answers become one
record of `travellers`, which the steps after the repeat read by that name:
`say: "{{travellers}}"` shows them as a table, and a tool with an input called
`travellers` receives the whole list (`def book_tickets(travellers: list, ...)`).
The confirmation shows the whole list, and the tool runs once.

- Only `ask:` steps go inside a repeat, with their rules and conditions. Say
  things before or after it, and run tools once, after it.
- An item's answers are read inside the repeat. After it, read the list.
- At least one item is collected. Only the user says whether there is another.
- A secret cannot be one item of a list: ask it before or after the repeat.
- Changing another answer later keeps the list. "Add one more" opens it again.

## Tools

A tool is a Python function. Every public function in `skills/tools.py` or
`skills/tools/*.py` is one, by its function name; a file whose name starts with
`_` holds helpers. Adding a flow means adding a skill file and, at most, a
function to that folder. Nobody edits the agent's own code.

```python
from typing import Literal
from aigenos_flow import Secret, readonly

@readonly
def quote_payment(payee_id: str, amount: float) -> dict:
    """What a payment would cost."""          # shown where the tool is listed
    return {"within_balance": amount <= 2500, "needs_code": amount >= 1000}

def send_payment(payee_id: str, amount: float, speed: Literal["standard", "fast"] = "standard") -> dict:
    """Send the money."""
    return {"reference": "PAY-0001"}

@readonly
def verify_code(code: Secret) -> dict:
    return {"valid": code == "481920"}
```

- **`@readonly`** says the function only looks something up. That is the tool's
  statement, not the skill's: a skill file cannot relabel a write as a lookup.
- **The signature is the contract.** `amount: float` is a number, `int` a whole
  number, `bool` a yes/no, `Literal[...]` a choice, `Secret` a masked value
  that is never logged. An input with a default is optional.
- **A tool can keep a rule in Python.** Return
  `{"ask_again": "amount", "say": "That is more than your balance."}` and the
  flow says the sentence and asks that question again. Return `{"say": "..."}`
  and the sentence is said once, when the tool runs.

Other places a tool can come from:

| Source | How |
|---|---|
| Your code | `create_agent(llm, "skills/", tools=[bank_transfer])`. A tool passed in wins over a file of the same name. |
| Another folder | `tools_dir="shared/tools"` on `create_agent`, `wrap_agent` or `FlowAgent.from_dir`. |
| One skill only | `scripts.py` in the skill's own folder. Only that skill can name those. |
| An MCP server | `tools=MCPTools([sys.executable, "server.py"]).tools()` from `aigenos_flow.mcp_tools`, or `MCPTools(url=...)` for a remote one; tools loaded with LangChain's MCP adapters work as they are. A tool the server annotates `readOnlyHint` is a lookup; any other needs a `confirm:`. |
| Built in | `save_request` keeps a record of the answers in `records.jsonl` beside the skill and returns `{{save_request.reference}}`. A flow with no Python at all can end on it. |

A callable written the older way, `def tool(args): ...`, receives every answer
asked above it as one dict.

## What is worked out for you

- **Types.** From the tool that receives the answer. With no tool to say,
  `choices:` makes a select, `when: amount >= 1000` makes a number, a bare
  `when: is_gift` makes a yes/no, and `type:` settles anything else.
- **Lookups.** A tool marked `@readonly` runs when its result is first needed,
  and again when an answer it used changes.
- **Who may give an answer.** Only the user can give a confirmation, a secret,
  a pick from a list, an answer with a `pattern:`, and any answer a `when:`
  reads (directly, or through a tool whose result a `when:` reads). For those
  the model may pass on a value the user typed, and a value the user did not
  type or pick is dropped. Any other answer the model may work out from what
  the user said ("for an hour" as 60 minutes), and the confirmation shows it
  before anything runs.
- **Corrections.** The flow is re-run from the top on every turn. Change an
  earlier answer and every rule below it is applied again, any code typed for
  the old value is thrown away, and the yes is asked for again.
- **Cancelling, resuming, starting over, switching to another skill and coming
  back.** None of it is authored.

## What you approve is what runs

A confirmation that lets a tool run shows, built by the engine, which tool a
yes will run and with which inputs. The sentence you wrote is the title; the
evidence is the engine's. A secret is shown as hidden.

What was shown is kept. When the tool is called, its inputs are compared with
it. If one differs, nothing runs, the user is told what changed, and the yes is
asked for again on a card that shows the inputs as they are now. One yes covers
everything it lets run: a change to the second tool's input stops the first as
well.

## A yes from someone else

```yaml
- confirm: Approve this refund?
  by: manager
  when: find_order.large
```

The flow stops and files a request. The person named sees the question, which
tool a yes will run, its inputs, and what the user answered. Until they answer,
nothing below the step runs.

Their answer is the only way that yes reaches the flow. A value for it sent
through the conversation, by the user, by the model or by a posted form, is
refused. If an answer changes while it waits, the request is withdrawn at once,
and a new one is filed when the flow reaches the step again.

`by:` says who is asked. It does not check who answers: that belongs to whatever
you put in front of the store. The bundled server puts the Approvals page and
its endpoint behind the same access rule as authoring (your own machine by
default, a token otherwise), keeps requests in memory, and notifies nobody.

In the Studio, requests are on the Approvals page. In your own deployment they
are in the store your host carries (`host.approvals`, or
`aigenos_flow.approvals.default_store`): `store.all("pending")` lists them and
`store.decide(id, True, by="dana")` answers one. The server exposes the same
two as `GET /flow/approvals` and `POST /flow/approvals/{id}`. In a test:
`chat.decide(True, by="dana")`.

## What the loader refuses

Before any conversation starts, and with a sentence that says what to change:

- a word that is not one of the 20 (with the nearest valid one), or a long-form
  word (with what to write instead);
- a tool that changes something with no `confirm:` above it, or with one that is
  only asked on some of the paths that reach it;
- a tool that needs an input nothing asks for, or that is only asked later or
  on another branch;
- a condition or a sentence that reads a name nothing provides;
- a condition that compares text with a number;
- `from:` naming a tool that is not `@readonly`;
- a tool that changes something used twice without `as:`;
- an answer with the same name as a tool;
- `do:` naming a skill that is not there, or skills that hand off in a circle;
- `(optional)` on anything but a text answer;
- inside `repeat:`, anything but a question, a secret, or another repeat; and
  after it, reading one item's answer instead of the list;
- a `chat:` that is not the last step, lists a tool that changes something, or
  fixes a tool's input to a free-text answer;
- `widget:` with no widget named.

## Reading a skill back

```bash
python -m aigenos_flow.procedure skills/ refund-order
```

prints the skill as a procedure: each step in plain words, every way through
the flow, and for each tool that changes something, what must be true first and
who was allowed to supply each input. It is generated from the file and the
tools with no model involved, so it cannot say something the flow does not do.
The Studio shows the same page under **Procedure** on the Canvas.

## Every word

| Word | Where | Meaning |
|---|---|---|
| `steps` | top of the file, or under `when:` or `repeat:` | the list of steps |
| `ask` | a step | get an answer |
| `say` | a step | tell the user something |
| `do` | a step | run a tool or another skill |
| `confirm` | a step | get a yes |
| `chat` | the last step | answer the user's questions with lookups only |
| `when` | any step, or a block | only if a condition holds |
| `repeat` | a block | ask the steps under it once per item, collecting a list |
| `check` | `ask` | what the answer must satisfy, and what to say if it does not |
| `choices` | `ask` | a fixed list to choose from |
| `from` | `ask` | rows from a tool to pick from |
| `type` | `ask` | `text`, `number`, `whole number`, `yes/no`, `secret` |
| `pattern` | `ask` | the shape of a typed answer |
| `widget` | `ask`, `say`, `confirm` | a widget you designed |
| `with` | `do`, or beside `widget` | pass a value |
| `tools` | `chat` | the lookups it may call |
| `title` | `say` | the title of a card |
| `as` | `do` | another name for the result |
| `else` | `do` (a skill) | what to say if that skill did not finish |
| `by` | `confirm` | whose yes it is |

`name` and `description` come first in every skill file. `compute:` (named
conditions) and `guidelines:` (tone and wording rules for the model) are
optional blocks beside `steps:`.

## When steps are not enough

Steps are a list with conditions and lists of items. They do not run two
things at once, and a repeat collects answers rather than running a tool per
item. Two escape hatches, both loaded by the same engine:

- **The long form** (`fields:` / `actions:` / `flow:`) is what steps expand to.
  It adds side questions answered part-way through a flow (`assist:`), and
  per-argument bindings. See the [long-form reference](skill-authoring.md).
- **A Python flow** (`@flow` in `handlers.py`) for a loop or anything else.
  The same guarantees hold: a write needs its yes and runs at most once.

`aigenos_flow.shortform.explain(frontmatter, tools)` prints what a `steps:`
block expands to.

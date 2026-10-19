# Build a deterministic workflow in 10 minutes

One skill file, **five kinds of step**, no schema, no graph, no training. If you
can read a checklist you can read a flow.

| step | what it does |
|---|---|
| `ask:` | get an answer and keep it under a name (the user sees a typed input) |
| `say:` | tell the user something |
| `do:` | run a tool from the library, or another skill |
| `confirm:` | get a yes; "no" stops the flow |
| `chat:` | the last step: answer the user's questions with lookups only |

Two words make a step conditional: `when:` (only if this holds) and `check:`
(what an answer must satisfy, and what to say when it does not). One more,
`repeat:`, asks the steps under it once per item. That is the language. The
[steps reference](steps.md) has all 20 words on one page.

One rule explains all of it:

> **The LLM fills fields. Your program does everything else.**
> The model reads what the user typed and proposes answers. Which question is
> asked, which rule applies, which tool runs and when: those are the steps, and
> the model cannot change them.

Corrections work because the flow is **re-run from the top** on every turn:
change an earlier answer and every rule below it is applied again.

Every file on this page is run by the test suite exactly as printed, and every
output shown is compared with what the engine prints.

---

## Step 1 — install

```bash
pip install aigenos-flow        # the engine
npm i @aigenos/flow             # the React renderer (only if you have a web UI)
```

Python **3.11+**; Node **20.19+ or 22.12+** for the frontend.

## Step 2 — write the tools

A tool is a Python function. Put it in `skills/tools/` and every skill can name
it. Its parameters are the answers it needs, so nothing is declared twice.

**`skills/tools/payments.py`**

```python
"""Tools for paying contractors. Every public function here is a tool a skill can name."""
from aigenos_flow import Secret, readonly

CONTRACTORS = {"acme": "Acme Design Ltd", "globex": "Globex Plumbing"}
SENT: list = []


@readonly
def quote(contractor: str, amount: float) -> dict:
    """What this payment involves."""
    return {
        "needs_code": amount >= 1000,
        "summary": {"To": CONTRACTORS.get(contractor, contractor), "Amount": f"${amount:,.2f}"},
    }


@readonly
def verify(code: Secret) -> dict:
    """Is this the code we texted?"""
    return {"valid": code == "481920"}


def send_payment(contractor: str, amount: float) -> dict:
    """Pay the contractor."""
    SENT.append((contractor, amount))
    return {"reference": f"PAY-{len(SENT):04d}"}
```

`@readonly` is the one decision you make here: does this function only look
something up, or does it change something? `quote` and `verify` only look.
`send_payment` is not marked, so it changes something, and the engine will hold
every skill to that.

## Step 3 — write the skill

**`skills/pay-contractor/SKILL.md`**

```yaml
---
name: pay-contractor
description: Pay a contractor. Use when the user wants to pay or send money to a contractor.
steps:
  - ask: { contractor: "Which contractor?" }
    choices: [acme, globex]
  - ask: { amount: "How much? (USD)" }
    check: { amount > 0: "Enter an amount above zero." }
  - ask: { code: "Payments of $1,000 or more need the 6-digit code we texted you." }
    when: quote.needs_code
    pattern: '^[0-9]{6}$'
    check: { verify.valid: "That code is not valid." }
  - say: "{{quote.summary}}"
    title: Payment
  - confirm: Send this payment?
  - do: send_payment
  - say: "Paid. Reference {{send_payment.reference}}."
---
Help the user pay a contractor.
```

Read it top to bottom; that is the order it runs in. What the file does not
say, the engine works out:

- `amount` is a number, because `quote(amount: float)` takes it as one.
- `code` is a secret (masked, never logged), because `verify(code: Secret)` says so.
- `quote` and `verify` are never run as steps. A rule mentions their result, so
  they run when it is needed, and again when an answer they used changes.
- `contractor` and `amount` decide whether a code is needed, so only the user
  can give them. The model may pass on what the user typed, as it does in the
  run below. A value the user did not type or pick is dropped.

## Step 4 — run it

**`run.py`**

```python
"""One conversation, with no model: TestHost says what a model would have understood."""
from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

host = TestHost()
agent = FlowAgent.from_dir("skills", host=host)
chat = agent.session("pay-contractor")


def turn(user: str, act) -> None:
    print(f" user: {user}")
    act()
    for line in chat.replies:
        print(f" flow: {line}")
    print(f"       -> {'finished' if chat.done else 'waiting for ' + chat.awaiting}")


# "When the user says this, the model would understand these two answers."
host.queue_commands("pay acme 2500", SetField(field="contractor", value="acme"),
                    SetField(field="amount", value=2500))
turn("pay acme 2500", lambda: chat.user("pay acme 2500"))
turn("(presses Yes without the code)", lambda: chat.submit(confirm=True))
turn("(types a wrong code)", lambda: chat.submit(code="000000"))
turn("(types the code)", lambda: chat.submit(code="481920"))
turn("(presses Yes)", lambda: chat.submit(confirm=True))
turn("(presses Yes again)", lambda: chat.submit(confirm=True))
```

```
 user: pay acme 2500
 flow: Got it.
 flow: Payments of $1,000 or more need the 6-digit code we texted you.
       -> waiting for code
 user: (presses Yes without the code)
 flow: Payments of $1,000 or more need the 6-digit code we texted you.
       -> waiting for code
 user: (types a wrong code)
 flow: That code is not valid.
 flow: Payments of $1,000 or more need the 6-digit code we texted you.
       -> waiting for code
 user: (types the code)
 flow: Send this payment?
       -> waiting for confirm
 user: (presses Yes)
 flow: Paid. Reference PAY-0001.
       -> finished
 user: (presses Yes again)
       -> finished
```

No API key was used: `TestHost` stands in for the model, and the real engine
ran every turn. Look at what happened. Pressing Yes without the code did
nothing. The wrong code was refused in the skill's own words. The second Yes
paid nobody: a tool that changes something runs at most once.

With a real model it is one call. A message that matches a skill runs its
flow; everything else is ordinary chat over the same thread:

```python
from aigenos_flow import create_agent

agent = create_agent(llm, "skills/")      # llm: any LangChain chat model
agent.invoke({"messages": [("user", "pay acme 2500")]},
             config={"configurable": {"thread_id": "t1"}})
```

Already have a LangGraph agent? `wrap_agent(my_agent, skills_dir="skills/", llm=llm)`
puts the same flows in front of it and leaves the rest of it alone. Both are in
the [developer guide](developer-guide.md#entry-points-pick-one-of-three).

## Step 5 — test it

The whole workflow is a unit test.

**`tests/test_pay.py`**

```python
from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost


def start():
    host = TestHost()                                  # stands in for the model
    agent = FlowAgent.from_dir("skills", host=host)
    host.queue_commands("hello")
    return agent.session("pay-contractor").user("hello")


def test_a_large_payment_needs_the_code_and_the_yes():
    chat = start().submit(contractor="acme").submit(amount=2500)
    assert chat.awaiting == "code"                     # $2,500: the rule applies
    chat.submit(confirm=True)                          # try to skip it
    assert chat.awaiting == "code"                     # still there
    chat.submit(code="000000")
    assert chat.replies[0] == "That code is not valid."
    chat.submit(code="481920").submit(confirm=True)
    assert chat.done and chat.replies == ["Paid. Reference PAY-0001."]


def test_a_small_payment_skips_the_code():
    chat = start().submit(contractor="globex").submit(amount=120)
    assert chat.awaiting == "confirm"


def test_no_means_nothing_is_sent():
    chat = start().submit(contractor="globex").submit(amount=120).submit(confirm=False)
    assert chat.done and chat.replies == ["Okay — no changes were made."]


def test_changing_the_amount_asks_for_the_yes_again():
    chat = start().submit(contractor="globex").submit(amount=120)
    chat.submit(amount=5000)                           # a correction, at the confirmation
    assert chat.awaiting == "code"                     # now it is a large payment
```

```bash
pytest
```

```
4 passed
```

## Step 6 — meet the loader

The file is checked before any conversation starts. Take the `confirm:` line out:

```
skill 'pay-contractor': step 5: `send_payment` changes something (it is not marked @readonly), so it needs the user's yes first. Put a `- confirm: "...?"` step before it. If `send_payment` only looks something up, mark it `@readonly` where it is defined.
```

Misspell a word:

```
skill 'pay-contractor': step 3: unknown word `wen` (did you mean 'when'?). A step is one of ask, say, do, confirm or chat; `when:` makes it conditional, and `repeat:` holds steps asked once per item.
```

Name a tool that is not there:

```
skill 'pay-contractor': step 6: `send_paymnt` is not a tool in the library (did you mean 'send_payment'?)
```

A mistake in the file is an error with a sentence that says what to change. It
is never a rule that quietly stopped applying.

## Step 7 — read it back

```bash
python -m aigenos_flow.procedure skills/ pay-contractor
```

prints the skill as a procedure a person can sign off. It is generated from the
file and the tools, with no model involved, so it cannot say something the flow
does not do.

```
1. Ask: “Which contractor?”
   - The answer is one of: acme, globex.
   - Only the user can give this answer. A value the user did not type or pick is dropped.
2. Ask: “How much? (USD)”
   - The answer is a number.
   - Refused, and asked again, unless amount is more than 0: “Enter an amount above zero.”
   - Only the user can give this answer. A value the user did not type or pick is dropped.
3. Ask: “Payments of $1,000 or more need the 6-digit code we texted you.” *(only when quote says needs code)*
   - The answer is a secret: masked on screen, never logged.
   - It must look like: 6 digits.
   - Refused, and asked again, unless verify says valid: “That code is not valid.”
   - Only the user can give this answer. A value the user did not type or pick is dropped.
4. Show `quote.summary` as a card titled “Payment”.
5. Ask for a yes: “Send this payment?”
   - A yes lets `send_payment` run. The user sees which tool will run and with which inputs before answering.
   - “No” stops the flow here: nothing below runs. Only the user can answer.
6. Run `send_payment`: Pay the contractor.
   - This changes something. It runs once, and only after the yes at step 5.
7. Say: “Paid. Reference {{send_payment.reference}}.”
```

It lists every way through the flow:

```
2 ways through this flow, taking each tool result both ways.

| # | quote says needs code | Asks | Yeses | Runs |
|---|---|---|---|---|
| 1 | yes | contractor, amount, code | 1 | send_payment |
| 2 | no | contractor, amount | 1 | send_payment |

At every confirmation, “no” ends the flow there.
```

And, for each tool that changes something, what must be true first:

```
**`send_payment`** (step 6): Pay the contractor.
- The user said yes to “Send this payment?” (step 5).
- Amount is more than 0 (step 2).
- Verify says valid (step 3) (when quote says needs code).
- Input `contractor`: the answer at step 1 — only the user.
- Input `amount`: the answer at step 2 — only the user.
- It runs at most once, and each run is on the audit trail (a secret is redacted).
```

## What you get without asking

- **A typed input for every question.** A select for `choices:`, a number box,
  a masked box for a secret, a list to pick from for `from:`. The model does
  not decide whether a form appears. [Widgets](../README.md#widgets) has the detail.
- **What you approve is what runs.** The confirmation shows which tool a yes
  will run and with which inputs. If one of them changes afterwards, the tool
  does not run and the yes is asked for again.
- **Corrections, cancelling, resuming, starting over.** "Actually make it
  5000" re-applies every rule. "Never mind" stops the flow and "continue" picks
  it up where it was, with any code or yes asked for again.
- **An audit trail.** Every run of a tool that changes something is recorded
  with its inputs; a secret is redacted.

## Where next

| You want to… | Read |
|---|---|
| See every word and option | [Steps reference](steps.md) |
| Copy a recipe: a list to pick from, a rule in Python, one skill running another | [Cookbook](cookbook.md) |
| Wait for someone else's yes (a manager, finance) | [Steps reference → a yes from someone else](steps.md#a-yes-from-someone-else) |
| Use tools from an MCP server | [Steps reference → tools](steps.md#tools) |
| Build it by clicking instead | [Studio guide](user-guide.md) |
| Embed, persist, serve, ship | [Developer guide](developer-guide.md) |

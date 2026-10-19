# Expense filing example

A minimal, runnable demonstration of aigenos flow: one SKILL.md, one handlers.py,
one main.py that compiles the skill into a LangGraph agent and drives it from
a terminal REPL.

```
examples/expense/
├── skills/
│   └── file-expense/         # kebab-case, per the Anthropic Skills spec
│       ├── SKILL.md          # frontmatter (fields + triggers) + LLM context
│       └── handlers.py       # the flow function + actions, plain Python
├── main.py                   # LangGraph host wiring + REPL
└── README.md
```

## Run it

**This demo needs an LLM** — it is a live REPL against a real model. It picks
the first provider it finds (`ANTHROPIC_API_KEY`, then `OPENAI_API_KEY`, then a
local Ollama) and prints which one it chose. With no key and no `langchain-ollama`
installed it prints the install line you need and exits without starting.

```bash
# from repo root. The install metadata lives in backend/, not at the root.
pip install -e "backend[anthropic]"   # or backend[openai] / backend[ollama]
export ANTHROPIC_API_KEY=sk-ant-...   # the key matching the extra you chose
cd examples/expense
python main.py
```

`python-dotenv` is optional here: if it happens to be installed, `main.py` reads
a local `.env`; if not, it carries on. It is not a dependency of `aigenos-flow`
and no install line above provides it.

Want to see the guarantees with **no** LLM and **no** key? Those live next door:

```bash
python ../declarative_flow/prove.py          # 2FA gate, correction re-arm, no double-spend
python ../before_after/prove.py              # the same policy as prompt vs as a flow
```

Sample session (transcript captured from the skill itself, driven with
`TestHost` so the wording matches the code):

```
aigenos flow · expense demo (thread a1b2c3d4)
Try: 'I want to file an expense for $100 at Whole Foods, food'

you> file an expense for $750 at Delta Airlines, travel
bot> That's over policy — why is it needed? (one short sentence)
you> client visit to Chicago
bot> Ready to submit $750.00 at Delta Airlines (travel).
bot> Submit it? (yes/no)
you> yes
bot> ✅ Submitted as #EXP-6509.
```

Notice what's happening:

- The user provided three fields in one sentence; the extractor returned three
  `SetField` commands and the flow ran to the policy check without asking.
- The policy check determined a justification was required, so the next
  `ctx.ask("justification")` paused for user input.
- After the user provided the justification, the flow resumed from the same
  place (field replay), showed the summary, and stopped on the confirmation —
  the submit only happened on the turn the user said yes.

## The gate

`submit_expense` is declared `@action(requires="confirm")`. That is not a comment:
`ctx.run` re-checks the gate at dispatch, so the write cannot fire on a turn where
`confirm` is not true — and a value for `confirm` that the *model* proposed rather
than the user is dropped before it reaches state. Say no and nothing is filed:

```
you> file an expense for $100 at Whole Foods, food
bot> Ready to submit $100.00 at Whole Foods (food).
bot> Submit it? (yes/no)
you> no, cancel that
bot> OK — nothing was submitted.
```

## Try the correction case

```
you> file an expense for $100 at Whole Foods, food
bot> Ready to submit $100.00 at Whole Foods (food).
bot> Submit it? (yes/no)
you> actually change the amount to 800
bot> That's over policy — why is it needed? (one short sentence)
```

The `Correct` command cleared the memoized action results, so when the flow re-ran
it re-evaluated the policy against $800 and discovered the justification was now
required — and it will ask for the confirmation again afterwards. Correcting a
value can never leave a stale approval standing.

## Without LangGraph

The skill works without LangGraph too — use `agent.turn(state)` directly:

```python
from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost     # or any custom Host

agent = FlowAgent.from_dir("skills/", host=TestHost())
state = {"flow_id": "file-expense",
         "pending_user_message": "file an expense for $100 at Whole Foods, food"}
state = agent.turn(state)
print(state["awaiting_field"])        # the field to render next
```

That makes aigenos flow drop into any agent framework that gives you a per-turn
hook — your orchestrator's tasks or callbacks, custom FastAPI handlers, anything.

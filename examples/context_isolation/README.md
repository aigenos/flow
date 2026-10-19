# Context isolation — a runnable proof

The most common objection to putting an LLM in front of a real workflow:

> "If the user starts one flow — say a money transfer — and then switches to
> something unrelated, the chatbot keeps the first flow in context and starts
> giving polluted answers."

This directory answers that with a program you can run, not a claim — no API
key, 26 checks, under a second.

```bash
python examples/context_isolation/prove.py
```

It drives two deliberately-unrelated flows — `transfer-funds` (money) and
`restaurant-reservation` (dinner) — through two switches, and asserts that the
second flow's interpreter is **never handed the first flow's fields or values**.

## Why this is evidence and not a scripted trick

The model's outputs are scripted (via `TestHost`) so the conversation is
deterministic and costs zero API quota. But the assertions are not made on the
scripted output. They are made on **what the runtime feeds the model**: the
exact `current_fields` dict and the "Currently filled fields" block that
`interpret_turn` builds. That construction is pure engine code — scripting a
reply can't fake it.

One honest caveat the script calls out itself: the interpreter prompt
*intentionally* lists sibling flows' descriptions so the model can route
between them, so transfer's description words legitimately appear in restaurant
prompts. The isolation is therefore asserted on the passed fields and on the
runtime **values** the user actually entered ("300", "mom", "checking") — never
on description text.

Both skills ship in [`skills/`](skills) next to the proof, so it runs from a
clean clone with nothing else set up.

# Book a room: a ReAct agent that hands a workflow to the runtime

A LangGraph ReAct agent with one extra tool. When the model activates the
[`book-meeting-room`](../../skills/book-meeting-room) skill, the tool returns a
`Command` that switches state, and an aigenos/flow node runs the flow declared
in that skill's own `steps:`. Everything outside the flow (greetings,
"what rooms are there?", listing bookings) stays in the ReAct loop.

Skill activation is the model's decision, from the name and description in its
system prompt. No regex, no keyword routing.

> Looking for the with-and-without comparison? That lives in
> [`examples/before_after/`](../../../before_after/), which needs no API key.

| | |
|---|---|
| The flow | [`book-meeting-room/SKILL.md`](../../skills/book-meeting-room/SKILL.md), with its two tools in [`scripts.py`](../../skills/book-meeting-room/scripts.py) |
| A lookup to digress to | [`list-rooms`](../../skills/list-rooms) |
| The fake calendar | [`shared/rooms.py`](../../shared/rooms.py), instrumented with call counters |
| The agent | [`main.py`](main.py) |
| The same scenarios with no LLM | [`test_scenarios_ci.py`](test_scenarios_ci.py) |

## Run the scenarios with no LLM and no key

```bash
python -m pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py -q
# 7 passed
```

Seven scenarios: happy path, mid-flow correction, a missing day (the flow asks
instead of assuming), cancel, **declined at the gate** (nothing is written and
the user is told so), **a model-emitted `confirm` is dropped** (only a real user
yes opens the gate), and **a calendar conflict** (explained, then re-collected).

The correction scenario, copied verbatim from that file:

```python
    # Turn 2 — correct start_time
    state["pending_user_message"] = "actually make it 4pm not 3pm"
    state = agent.turn(state)
    msgs_2 = host.drain_pending_messages()
    assert state["fields"]["start_time"] == "16:00"
    assert state["fields"]["duration_minutes"] == 60, "duration must NOT change"
    assert state["fields"]["room"] == "redwood", "room must NOT change"
    assert any("16:00" in m for m in msgs_2), "the summary must show the new time"
    # The correction invalidated the memo, so availability was re-checked against
    # the NEW time — 16:00-17:00, not the stale 15:00-16:00.
    assert state["results"]["check_room"]["available"] is True
    assert state["results"]["check_room"]["end_time"] == "17:00"
```

That is the part worth taking away: a correction mid-conversation clears the
cached availability check and re-runs it, the booking cannot fire without a
confirmation the user actually gave, and both are regression-testable without a
model in the loop.

## Run the agent interactively

Needs a model. With no provider configured it prints the install line and the
key it needs, then exits.

```bash
pip install -e "backend[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-...
python examples/studio_agent/proofs/book_room/main.py
```

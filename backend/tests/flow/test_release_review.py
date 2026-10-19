"""What two independent reviewers broke before the first public release.

Each test is a way a write fired without its confirmation, fired twice, or a
hand-off was told the wrong thing. All offline: TestHost stands in for the model.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Correct, Reply, SetField, StartFlow, _is_grounded_in_user

PAY = """\
---
name: pay
description: Pay someone. Use when the user wants to pay.
allowed-tools: verify, send
fields:
  person:
    type: enum
    values: [ann, bob]
    description: Who?
  amount:
    type: number
    min: 1
    must_elicit: true
    description: How much?
  code:
    type: string
    pattern: '^[0-9]{6}$'
    sensitive: true
    description: Enter the 6-digit code.
  confirm:
    type: boolean
    description: Send?
actions:
  verify:
    tool: verify
    readonly: true
    args: { code: "{{code}}" }
  send:
    tool: send
    requires: confirm
    args: { person: "{{person}}", amount: "{{amount}}" }
flow:
  - ask: person
  - ask: amount
  - ask: code
  - do: verify
  - check: "verify.ok"
    else_say: "That code is not valid."
    else_clear: [code]
  - ask: confirm
  - when: "confirm"
    do: send
    then_say: "Sent to {{person}}."
---
Pay.
"""

ADD = """\
---
name: add
description: Add a person. Use when the user wants to add someone.
allowed-tools: add_person
fields:
  name:
    type: string
    pattern: '^[a-z]+$'
    description: Their name?
  confirm:
    type: boolean
    description: Add them?
actions:
  add_person:
    tool: add_person
    requires: confirm
    args: { name: "{{name}}" }
flow:
  - ask: name
  - ask: confirm
  - when: "confirm"
    do: add_person
    then_say: "Added."
---
Add.
"""


def _write(root: Path, skills: dict[str, dict[str, str]]) -> None:
    for name, files in skills.items():
        (root / name).mkdir()
        for fname, text in files.items():
            (root / name / fname).write_text(textwrap.dedent(text), encoding="utf-8")


@pytest.fixture
def desk(tmp_path: Path):
    """(agent, host, log): pay and add, with every tool call recorded."""
    _write(tmp_path, {"pay": {"SKILL.md": PAY}, "add": {"SKILL.md": ADD}})
    log: list[tuple] = []
    tools = {
        "verify": lambda a: (log.append(("verify", a.get("code"))), {"ok": a.get("code") == "135790"})[1],
        "send": lambda a: (log.append(("send", a["person"], a["amount"])), {"ok": True})[1],
        "add_person": lambda a: (log.append(("add_person", a["name"])), {"ok": True})[1],
    }
    host = TestHost()
    host.checkpointer = MemorySaver()
    return FlowAgent.from_dir(tmp_path, host=host, tools=tools), host, log


def _at_confirm(agent):
    chat = agent.session("pay").submit(person="ann").submit(amount=50).submit(code="135790")
    assert chat.awaiting == "confirm"
    return chat


def _sent(log) -> list[tuple]:
    return [entry for entry in log if entry[0] == "send"]


# --------------------------------------------------------------------------- the consent guard
@pytest.mark.parametrize(("typed", "value"), [
    ("no wait yes", "yes"),            # the word, as a string
    ("yes, hold on", "yes"),
    ("no, I never said yes", "true"),
    ("no, make it 75", 75),            # a number, coerced to True
    ("no", 1),
])
def test_a_yes_the_model_wrote_as_a_word_or_a_number_still_gets_the_yes_no_rule(desk, typed, value):
    """The polarity rule ran only for a Python bool. "yes" was checked as a
    word of the message and 75 as a number in it, then coerced to True."""
    agent, host, log = desk
    chat = _at_confirm(agent)
    host.queue_commands(typed, SetField(field="confirm", value=value))

    chat.user(typed)

    assert _sent(log) == [], f"{typed!r} with confirm={value!r} must not send"
    assert not chat.done


def test_yes_with_a_change_that_did_not_land_is_not_a_yes(desk):
    """"yes, but double it": the change is dropped, so the yes is not a yes to
    what would run. It used to send the old amount."""
    agent, host, log = desk
    chat = _at_confirm(agent)
    host.queue_commands("yes, but double it", Reply(text="Sure, 100."),
                        Correct(field="amount", value=100), SetField(field="confirm", value=True))

    chat.user("yes, but double it")

    assert _sent(log) == [] and chat.awaiting == "confirm"
    assert chat.state["fields"]["amount"] == 50
    assert chat.replies[0] == "I could not take that change, so nothing was changed. Please state the new value exactly."


def test_the_same_dropped_change_sent_as_setfield_is_reported_too(desk):
    agent, host, log = desk
    chat = _at_confirm(agent)
    host.queue_commands("double it", SetField(field="amount", value=100))

    chat.user("double it")

    assert "nothing was changed" in chat.replies[0] and chat.state["fields"]["amount"] == 50


def test_a_hint_is_not_printed_in_front_of_a_line_that_contradicts_it(desk):
    """A turn that also switches flow or changes a value explains itself."""
    agent, host, log = desk
    chat = _at_confirm(agent)
    host.queue_commands("wait, add cy first", StartFlow(flow_id="add"), SetField(field="confirm", value=False))

    chat.user("wait, add cy first")

    assert chat.state["flow_id"] == "add"
    assert not any("yes or no" in line for line in chat.replies)


def test_an_ordinal_grounds_its_own_day_and_nothing_else():
    assert _is_grounded_in_user("date", "2026-05-23", "book it on the 23rd")
    assert not _is_grounded_in_user("date", "2026-12-25", "the 23rd please")
    assert not _is_grounded_in_user("date", "2026-12-25", "the room on the 2nd floor, at 3pm")
    assert not _is_grounded_in_user("date", "2026-12-25", "this is my 1st time booking")
    assert _is_grounded_in_user("start_date", "2026-12-25", "next monday")
    assert not _is_grounded_in_user("days", "2026-12-25", "next monday"), "`days` is not a date field"
    assert not _is_grounded_in_user("nights_day", 3, "tomorrow"), "a number is never grounded by a date word"


# --------------------------------------------------------------------------- stale and repeated answers
def test_a_yes_for_a_question_that_is_not_on_screen_is_dropped(desk):
    """A yes delivered twice (double click, retried request) landed in the next
    flow's `confirm`, and its payment went out without "Send?" being shown."""
    agent, _host, log = desk
    chat = agent.session("pay")
    chat.submit(person="ann")                 # the flow is now asking for the amount
    chat.submit(confirm=True)                 # a stale yes: answers nothing being asked

    assert "confirm" not in chat.state["fields"]
    chat.submit(amount=50).submit(code="135790")
    assert chat.awaiting == "confirm" and _sent(log) == []
    chat.submit(confirm=True)
    assert _sent(log) == [("send", "ann", 50.0)]


def test_a_payload_that_answers_the_question_may_fill_the_next_ones(desk):
    agent, _host, log = desk
    chat = agent.session("pay")
    chat.submit(person="ann", amount=50, code="135790", confirm=True)   # answers `person`, and more

    assert chat.done and _sent(log) == [("send", "ann", 50.0)]


def test_resuming_a_flow_by_name_does_not_inherit_the_side_trips_question(desk):
    """pay is suspended; add is asking "Add them?". "yes, and go back to paying"
    with a model-supplied confirm used to be accepted as pay's own yes."""
    agent, host, log = desk
    host.queue_commands("pay someone")
    chat = agent.session("pay").user("pay someone").submit(person="ann").submit(amount=50).submit(code="135790")
    host.queue_commands("add cy first", StartFlow(flow_id="add"))
    chat.user("add cy first")
    chat.submit(name="cy")
    assert chat.state["flow_id"] == "add" and chat.awaiting == "confirm"
    msg = "yes, and go back to paying"
    host.queue_commands(msg, StartFlow(flow_id="pay"), SetField(field="confirm", value=True))

    chat.user(msg)

    assert chat.state["flow_id"] == "pay"
    assert _sent(log) == [], "pay never asked 'Send?' on this turn"
    assert chat.awaiting == "confirm"


def test_a_code_that_was_accepted_is_not_verified_again_after_a_side_trip(desk):
    """Re-running every lookup on resume asked for a one-time code twice."""
    agent, host, log = desk
    host.queue_commands("pay someone")
    chat = agent.session("pay").user("pay someone").submit(person="ann").submit(amount=50).submit(code="135790")
    assert [e for e in log if e[0] == "verify"] == [("verify", "135790")]
    host.queue_commands("add cy first", StartFlow(flow_id="add"))
    chat.user("add cy first")

    chat.submit(name="cy").submit(confirm=True)

    assert chat.state["flow_id"] == "pay" and chat.awaiting == "confirm"
    assert [e for e in log if e[0] == "verify"] == [("verify", "135790")], "verified once"


def test_sending_back_the_value_already_stored_voids_nothing(desk):
    """"yes, 50 please" with the amount already 50 threw the yes away."""
    agent, host, log = desk
    chat = _at_confirm(agent)
    host.queue_commands("yes, 50 please", SetField(field="amount", value=50), SetField(field="confirm", value=True))

    chat.user("yes, 50 please")

    assert _sent(log) == [("send", "ann", 50.0)]
    assert not any("no longer applies" in line for line in chat.replies)


# --------------------------------------------------------------------------- the compiled graph
TWO = """\
---
name: two
description: Book two things. Use when booking.
allowed-tools: hold_a, hold_b
fields:
  confirm:
    type: boolean
    description: Book both?
actions:
  hold_a:
    tool: hold_a
    requires: confirm
  hold_b:
    tool: hold_b
    requires: confirm
flow:
  - ask: confirm
  - when: "confirm"
    do: hold_a
  - when: "confirm"
    do: hold_b
    then_say: "Both booked."
---
Two.
"""


def test_a_graph_step_that_fails_part_way_keeps_the_writes_that_fired(tmp_path: Path):
    """A step that raises is not checkpointed. hold_a fired, hold_b timed out,
    the user pressed yes again, and hold_a fired a second time."""
    _write(tmp_path, {"two": {"SKILL.md": TWO}})
    log: list[str] = []
    fail = {"left": 1}

    def hold_b(args):
        if fail["left"]:
            fail["left"] -= 1
            raise TimeoutError("hold_b timed out (once)")
        log.append("hold_b")
        return {"ok": True}

    host = TestHost()
    host.checkpointer = MemorySaver()
    agent = FlowAgent.from_dir(tmp_path, host=host,
                               tools={"hold_a": lambda a: (log.append("hold_a"), {"ok": True})[1], "hold_b": hold_b})
    graph = agent.as_langgraph()
    cfg = {"configurable": {"thread_id": "t"}}
    host.queue_classification("book", "two")
    host.queue_commands("book")
    graph.invoke({"pending_user_message": "book"}, config=cfg)

    graph.invoke(Command(resume={"confirm": True}), config=cfg)      # hold_b raises inside this step

    values = graph.get_state(cfg).values
    assert log == ["hold_a"]
    assert any("hold_a" in key for key in values["effects_log"]), "the fired write is on record"
    said = [m["content"] for m in values["history"] if m["role"] == "assistant"]
    assert any("will not be repeated" in line for line in said), "and the user is told"

    host.queue_commands("carry on")
    graph.invoke({"pending_user_message": "carry on"}, config=cfg)   # any message replays the flow

    assert log == ["hold_a", "hold_b"], "what fired is skipped; what failed is tried again"
    assert graph.get_state(cfg).values["done"] is True


def test_a_failure_with_nothing_fired_still_raises(tmp_path: Path):
    _write(tmp_path, {"two": {"SKILL.md": TWO}})

    def hold_a(args):
        raise TimeoutError("hold_a timed out")

    host = TestHost()
    host.checkpointer = MemorySaver()
    agent = FlowAgent.from_dir(tmp_path, host=host, tools={"hold_a": hold_a, "hold_b": lambda a: {"ok": True}})
    graph = agent.as_langgraph()
    cfg = {"configurable": {"thread_id": "t"}}
    host.queue_classification("book", "two")
    host.queue_commands("book")
    graph.invoke({"pending_user_message": "book"}, config=cfg)

    with pytest.raises(TimeoutError):
        graph.invoke(Command(resume={"confirm": True}), config=cfg)


# --------------------------------------------------------------------------- what a hand-off is told
PARENT = """\
---
name: front
description: Front desk. Use for anything.
handlers: ./handlers.py
fields: {}
---
Front.
"""
PARENT_PY = """\
from aigenos_flow import flow


@flow
def front(ctx):
    if ctx.link("child"):
        ctx.say("PARENT: done.")
    else:
        ctx.say("PARENT: nothing was done.")
"""
CHILD_TRAILING_SAY = """\
---
name: child
description: Save a payee. Use to save.
allowed-tools: save
fields:
  confirm:
    type: boolean
    description: Save them?
actions:
  save:
    tool: save
    requires: confirm
flow:
  - ask: confirm
  - when: "confirm"
    do: save
    then_say: "Saved."
  - say: "That is all for payees."
---
Child.
"""
CHILD_OPTIONAL_SECOND_WRITE = """\
---
name: child
description: Book a hotel and maybe a car. Use to book.
allowed-tools: save, car
fields:
  wants_car:
    type: boolean
    must_elicit: true
    description: Add a car?
  confirm:
    type: boolean
    description: Book it?
actions:
  save:
    tool: save
    requires: confirm
  car:
    tool: car
    requires: confirm
flow:
  - ask: wants_car
  - ask: confirm
  - when: "confirm"
    do: save
    then_say: "Hotel booked."
  - when: "confirm and wants_car"
    do: car
---
Child.
"""


def _front(tmp_path: Path, child: str):
    _write(tmp_path, {"front": {"SKILL.md": PARENT, "handlers.py": PARENT_PY}, "child": {"SKILL.md": child}})
    fired: list[str] = []
    tools = {"save": lambda a: (fired.append("save"), {"ok": True})[1],
             "car": lambda a: (fired.append("car"), {"ok": True})[1]}
    host = TestHost()
    host.queue_commands("go")  # the model extracts nothing: front hands off, the child asks
    return FlowAgent.from_dir(tmp_path, host=host, tools=tools).session("front").user("go"), fired


def test_a_no_is_reported_even_when_the_gated_write_is_not_the_last_step(tmp_path: Path):
    """One closing `say:` after the write hid the refusal, and the parent went
    on as if the payee had been saved."""
    chat, fired = _front(tmp_path, CHILD_TRAILING_SAY)
    chat.submit(confirm=False)

    assert fired == [] and "PARENT: nothing was done." in chat.replies


def test_a_yes_is_still_reported_as_done_with_a_closing_line(tmp_path: Path):
    chat, fired = _front(tmp_path, CHILD_TRAILING_SAY)
    chat.submit(confirm=True)

    assert fired == ["save"] and "PARENT: done." in chat.replies


def test_a_skipped_optional_write_is_not_a_refusal_after_the_first_one_fired(tmp_path: Path):
    """The hotel was booked, the optional car was not wanted, and the child said
    "no changes were made" while the parent was told nothing happened."""
    chat, fired = _front(tmp_path, CHILD_OPTIONAL_SECOND_WRITE)
    chat.submit(wants_car=False).submit(confirm=True)

    assert fired == ["save"]
    assert "PARENT: done." in chat.replies
    assert not any("no changes were made" in line for line in chat.replies)


# --------------------------------------------------------------------------- second batch
SEARCH = """\
---
name: lookup
description: Find a client. Use to look someone up.
allowed-tools: search, open_file
fields:
  query:
    type: string
    pattern: '^[a-z ]{2,30}$'
    description: Who are you looking for?
  client_id:
    type: string
    pattern: '^C-[0-9]{3}$'
    description: Which client?
  confirm:
    type: boolean
    description: Open their file?
actions:
  search:
    tool: search
    readonly: true
    args: { q: "{{query}}" }
  open_file:
    tool: open_file
    requires: confirm
    args: { client_id: "{{client_id}}" }
flow:
  - ask: query
  - do: search
  - pick: "{{search.matches}}"
    save: [client_id]
    prompt: Which client?
  - ask: confirm
  - when: "confirm"
    do: open_file
    then_say: "Opened {{client_id}}."
---
Lookup.
"""


@pytest.fixture
def lookup(tmp_path: Path):
    _write(tmp_path, {"lookup": {"SKILL.md": SEARCH}})
    people = {"ann": [{"client_id": "C-001", "name": "Ann"}]}
    opened: list[str] = []
    tools = {
        "search": lambda a: {"matches": people.get(a["q"], [])},
        "open_file": lambda a: (opened.append(a["client_id"]), {"ok": True})[1],
    }
    return FlowAgent.from_dir(tmp_path, host=TestHost(), tools=tools), opened


def test_a_search_that_matches_nothing_says_so_and_asks_again(lookup):
    """`pick:` over an empty list raised an authoring error at the end user
    ("Add `map:` to the step"), and kept raising on every later message."""
    agent, opened = lookup
    chat = agent.session("lookup").submit(query="bob")

    assert chat.awaiting == "query", "the search term is asked for again"
    assert "Nothing matched that, so there is nothing to choose from." in chat.replies
    assert "query" not in chat.state["fields"]

    chat.submit(query="ann")
    assert chat.awaiting == "client_id"
    chat.submit(client_id="C-001").submit(confirm=True)
    assert opened == ["C-001"]


def test_an_empty_box_is_not_an_answer_to_a_required_question(lookup):
    agent, _ = lookup
    chat = agent.session("lookup")
    for blank in ("", "   "):
        chat.submit(query=blank)
        assert chat.awaiting == "query" and not chat.state["fields"].get("query")
    assert any("I need a value" in line for line in chat.replies)


@pytest.mark.parametrize("question", [
    "is that the right client?",
    "are you sure it is correct",
    "what happens after I confirm",
    "yes?",
])
def test_a_question_is_not_consent(lookup, question):
    """One affirmative-looking word anywhere used to ground a model's yes."""
    agent, opened = lookup
    chat = agent.session("lookup").submit(query="ann").submit(client_id="C-001")
    assert chat.awaiting == "confirm"
    agent.host.queue_commands(question, SetField(field="confirm", value=True))

    chat.user(question)

    assert opened == [] and chat.awaiting == "confirm"


@pytest.mark.parametrize("reply", ["yes", "sure", "confirm it", "that is correct", "ok go ahead", "right"])
def test_a_plain_yes_in_its_usual_forms_is_still_consent(lookup, reply):
    agent, opened = lookup
    chat = agent.session("lookup").submit(query="ann").submit(client_id="C-001")
    agent.host.queue_commands(reply, SetField(field="confirm", value=True))

    chat.user(reply)

    assert opened == ["C-001"], f"{reply!r} should be taken as a yes"


def test_a_submission_with_nothing_in_it_is_answered(lookup):
    agent, _ = lookup
    chat = agent.session("lookup").submit(query="ann")
    assert chat.awaiting == "client_id"

    chat.submit()            # Continue with no row chosen

    assert chat.awaiting == "client_id"
    assert "I did not get an answer there. Choose or enter one to continue." in chat.replies

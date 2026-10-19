"""A flow that resumes after another flow ran reads its lookups again.

Found in the send-money example: pay-someone listed three payees, the user said
"they are not in the list, add them first", add-payee saved a fourth, and
pay-someone came back with the list it had memoized before: "Saved Dana Lee as a
payee." followed by a list without her.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Cancel, StartFlow

PAY = """\
---
name: pay
description: Pay someone on the list. Use when the user wants to pay.
allowed-tools: list_people, send
fields:
  person:
    type: string
    pattern: '^[a-z]+$'
    description: Who?
  confirm:
    type: boolean
    description: Send?
actions:
  list_people:
    tool: list_people
    readonly: true
  send:
    tool: send
    requires: confirm
    args: { person: "{{person}}" }
flow:
  - do: list_people
  - pick: "{{list_people.people}}"
    save: [person]
    prompt: Who?
  - ask: confirm
  - when: "confirm"
    do: send
    then_say: "Sent."
---
Pay.
"""

ADD = """\
---
name: add
description: Add a person to the list. Use when the user wants to add someone.
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


@pytest.fixture
def book(tmp_path: Path):
    for name, text in (("pay", PAY), ("add", ADD)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(textwrap.dedent(text), encoding="utf-8")
    people = ["ann", "bob"]
    calls: list[str] = []

    def list_people(args):
        calls.append("list_people")
        return {"people": [{"person": p} for p in people]}

    tools = {
        "list_people": list_people,
        "add_person": lambda args: (calls.append("add_person"), people.append(args["name"]), {"ok": True})[2],
        "send": lambda args: (calls.append("send"), {"ok": True})[1],
    }
    host = TestHost()
    return FlowAgent.from_dir(tmp_path, host=host, tools=tools), host, calls


def _rows(chat) -> list[str]:
    data = (chat.state.get("pending_widget") or {}).get("data") or {}
    return [row["person"] for row in data.get("rows", [])]


def test_the_list_is_read_again_after_a_side_trip_that_changed_it(book):
    agent, host, calls = book
    host.queue_commands("pay someone")  # the model extracts nothing: the flow lists and asks
    chat = agent.session("pay").user("pay someone")
    assert _rows(chat) == ["ann", "bob"]
    host.queue_commands("cy is not there, add them first", StartFlow(flow_id="add"))
    chat.user("cy is not there, add them first")
    assert chat.state["flow_id"] == "add"

    chat.submit(name="cy").submit(confirm=True)

    assert chat.state["flow_id"] == "pay" and chat.awaiting == "person"
    assert _rows(chat) == ["ann", "bob", "cy"], "the person just added is on the list that comes back"
    assert calls.count("list_people") == 2 and calls.count("add_person") == 1


def test_a_cancelled_side_trip_also_comes_back_to_a_fresh_read(book):
    agent, host, calls = book
    host.queue_commands("pay someone")
    chat = agent.session("pay").user("pay someone")
    assert calls.count("list_people") == 1
    host.queue_commands("add someone", StartFlow(flow_id="add"))
    chat.user("add someone")
    host.queue_commands("never mind", Cancel())

    chat.user("never mind")

    assert chat.state["flow_id"] == "pay" and _rows(chat) == ["ann", "bob"]
    assert calls.count("list_people") == 2
    # And it says so: "let me know what you'd like to do next" followed by the
    # earlier flow's question, with nothing in between, read as a non sequitur.
    assert chat.replies[:2] == ["Okay, cancelled that.", "Now, back to where we were."]


def test_a_write_is_never_repeated_by_a_resume(book):
    """Only lookups are forgotten. A gated write that fired before the side
    trip is still at-most-once: the effects log is not touched."""
    agent, host, calls = book
    chat = agent.session("pay").submit(person="ann").submit(confirm=True)
    assert chat.done and calls.count("send") == 1

    again = agent.session("pay").submit(person="bob")
    host.queue_commands("add someone", StartFlow(flow_id="add"))
    again.user("add someone")
    again.submit(name="di").submit(confirm=True)
    again.submit(confirm=True)

    assert calls.count("send") == 2, "one per run, and the side trip did not fire the second run's twice"
    assert calls.count("add_person") == 1

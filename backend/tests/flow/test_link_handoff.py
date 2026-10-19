"""`ctx.link()` hands off to another skill and comes back, once.

A flow replays from the top on every turn. `ctx.link()` used to raise on every
replay, so when the target finished and the runtime popped back to the parent,
the parent reached the same line and handed off again: the user was asked the
target's first question a second time, forever. A link is now remembered by its
position and its target (`links_done` in state), so a finished one is walked
past.

The shape under test is the one people reach for first: a front desk that asks
one yes/no question and sends the user to one of two other skills.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from aigenos_flow import FlowAgent
from aigenos_flow.context import Context, FlowLink
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Cancel, SetField, StartFlow

FRONT_DESK = """\
---
name: front-desk
description: Front desk for order problems. Sends the user to the refund flow or the ticket flow. Use when the user has a problem with an order.
handlers: ./handlers.py
fields:
  wants_refund:
    type: boolean
    description: Do you want a refund? (No opens a support ticket instead.)
  rating:
    type: integer
    min: 1
    max: 5
    description: How did we do, 1 to 5?
---
Route the user to a refund or a ticket, then ask for a rating.
"""

FRONT_DESK_PY = """\
from aigenos_flow import Context, flow


@flow
def front_desk(ctx: Context):
    if ctx.ask("wants_refund"):
        ctx.link("refund-path")
    else:
        ctx.link("ticket-path")
    ctx.ask("rating")
    ctx.say("Thanks. Back at the front desk.")
"""

REFUND = """\
---
name: refund-path
description: Refund an order. Use when the user asks for a refund.
allowed-tools: issue_refund
fields:
  order_id:
    type: string
    pattern: '^[A-Z]{2}[0-9]{4}$'
    description: Which order?
  confirm:
    type: boolean
    description: Refund this order?
actions:
  issue_refund:
    tool: issue_refund
    requires: confirm
    args: { order_id: "{{order_id}}" }
flow:
  - ask: order_id
  - ask: confirm
  - when: "confirm"
    do: issue_refund
    then_say: "Refunded: {{issue_refund.reference}}."
---
Refund an order.
"""

TICKET = """\
---
name: ticket-path
description: Open a support ticket. Use when the user wants to report a problem.
allowed-tools: open_ticket
fields:
  topic:
    type: enum
    values: [delivery, billing, other]
    description: What is it about?
  confirm:
    type: boolean
    description: Open this ticket?
actions:
  open_ticket:
    tool: open_ticket
    requires: confirm
    args: { topic: "{{topic}}" }
flow:
  - ask: topic
  - ask: confirm
  - when: "confirm"
    do: open_ticket
    then_say: "Ticket opened: {{open_ticket.reference}}."
---
Open a ticket.
"""


@pytest.fixture
def desk(tmp_path: Path):
    """(agent, host, fired): three skills on disk and a record of every write."""
    for name, skill_md in (("front-desk", FRONT_DESK), ("refund-path", REFUND), ("ticket-path", TICKET)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (tmp_path / "front-desk" / "handlers.py").write_text(textwrap.dedent(FRONT_DESK_PY), encoding="utf-8")

    fired: list[tuple[str, dict]] = []
    tools = {
        "issue_refund": lambda args: fired.append(("issue_refund", dict(args))) or {"reference": "RF-1"},
        "open_ticket": lambda args: fired.append(("open_ticket", dict(args))) or {"reference": "TK-1"},
    }
    host = TestHost()
    host.checkpointer = MemorySaver()
    return FlowAgent.from_dir(tmp_path, host=host, tools=tools), host, fired


def _stack(chat) -> list[str]:
    return [frame["flow_id"] for frame in chat.state.get("flow_stack") or []]


def test_yes_hands_off_to_the_refund_flow_and_comes_back(desk):
    agent, _, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True)
    assert chat.state["flow_id"] == "refund-path" and _stack(chat) == ["front-desk"]
    assert chat.awaiting == "order_id"

    chat.submit(order_id="AB1234").submit(confirm=True)

    assert fired == [("issue_refund", {"order_id": "AB1234"})]
    # Back in the parent, on the line AFTER the link: not asked for the order again.
    assert chat.state["flow_id"] == "front-desk" and _stack(chat) == []
    assert chat.awaiting == "rating"
    assert any("Refunded: RF-1" in line for line in chat.replies)

    chat.submit(rating=5)
    assert chat.done and len(fired) == 1
    assert any("Back at the front desk" in line for line in chat.replies)


def test_no_hands_off_to_the_ticket_flow(desk):
    agent, _, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=False)
    assert chat.state["flow_id"] == "ticket-path" and chat.awaiting == "topic"

    chat.submit(topic="billing").submit(confirm=True).submit(rating=4)

    assert chat.done
    assert fired == [("open_ticket", {"topic": "billing"})]


def test_a_finished_link_is_not_taken_again_on_later_turns(desk):
    agent, _, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True)
    chat.submit(order_id="AB1234").submit(confirm=True)
    assert chat.state["links_done"] == [{"flow": "refund-path", "completed": True}]

    # Every one of these replays the parent from the top, past the link.
    chat.submit(rating=9)          # out of range: refused, asked again
    assert chat.state["flow_id"] == "front-desk" and chat.awaiting == "rating"
    chat.submit(rating=3)

    assert chat.done and fired == [("issue_refund", {"order_id": "AB1234"})]


def test_the_target_starts_clean_and_the_parent_keeps_its_own_answers(desk):
    agent, _, _ = desk
    chat = agent.session("front-desk").submit(wants_refund=True)
    assert chat.state["fields"] == {}, "the target does not see the parent's fields"
    chat.submit(order_id="AB1234").submit(confirm=True)
    assert chat.state["fields"] == {"wants_refund": True}, "and the parent does not see the target's"


def test_cancelling_the_target_returns_to_the_parent_without_a_write(desk):
    agent, host, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True).submit(order_id="AB1234")
    host.queue_commands("never mind", Cancel())

    chat.user("never mind")

    assert fired == []
    assert chat.state["flow_id"] == "front-desk" and _stack(chat) == []
    assert chat.awaiting == "rating", "the parent carries on after the link; it does not hand off again"
    assert chat.state["links_done"] == [{"flow": "refund-path", "completed": False}], "and it is told why"


def test_saying_no_at_the_targets_last_confirmation_is_not_reported_as_finished(desk):
    """The target ran to its end, but its gated write never fired. A parent told
    "finished" carries on as if it had: send-money went on to "who are you
    paying?" after the user declined to save the payee."""
    agent, _, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True).submit(order_id="AB1234").submit(confirm=False)

    assert fired == []
    assert any("no changes were made" in line for line in chat.replies), "the target says so itself"
    assert chat.state["flow_id"] == "front-desk" and _stack(chat) == []
    assert chat.state["links_done"] == [{"flow": "refund-path", "completed": False}]
    assert "flow_declined" not in chat.state, "the marker is read once and never stored"


def test_a_decline_is_reported_through_the_compiled_graph_too(desk):
    agent, host, fired = desk
    graph = agent.as_langgraph()
    config = {"configurable": {"thread_id": "desk-no"}}
    host.queue_classification("I have a problem with an order", "front-desk")
    host.queue_commands("I have a problem with an order")
    graph.invoke({"pending_user_message": "I have a problem with an order"}, config=config)
    graph.invoke(Command(resume={"wants_refund": True}), config=config)
    graph.invoke(Command(resume={"order_id": "AB1234"}), config=config)

    graph.invoke(Command(resume={"confirm": False}), config=config)

    values = graph.get_state(config).values
    assert fired == []
    assert values["flow_id"] == "front-desk" and values["awaiting_field"] == "rating"
    assert values["links_done"] == [{"flow": "refund-path", "completed": False}]
    assert "flow_declined" not in values


def test_a_decline_in_one_run_does_not_mark_the_next_hand_off(desk):
    """The marker belongs to one completion. Decline the refund, then go round
    again and accept it: the second link is finished, not declined."""
    agent, _, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True).submit(order_id="AB1234").submit(confirm=False)
    chat.submit(rating=3)
    assert chat.done

    again = agent.session("front-desk").submit(wants_refund=True).submit(order_id="AB1234").submit(confirm=True)

    assert fired == [("issue_refund", {"order_id": "AB1234"})]
    assert again.state["links_done"] == [{"flow": "refund-path", "completed": True}]


def test_the_hand_off_survives_the_compiled_graph_and_its_checkpoints(desk):
    """The Studio and `create_agent` run the flow as a LangGraph graph: every key
    the link relies on has to make it through a checkpoint."""
    agent, host, fired = desk
    graph = agent.as_langgraph()
    config = {"configurable": {"thread_id": "desk"}}
    host.queue_classification("I have a problem with an order", "front-desk")
    host.queue_commands("I have a problem with an order")

    graph.invoke({"pending_user_message": "I have a problem with an order"}, config=config)
    assert graph.get_state(config).values["awaiting_field"] == "wants_refund"

    graph.invoke(Command(resume={"wants_refund": True}), config=config)
    values = graph.get_state(config).values
    assert values["flow_id"] == "refund-path" and values["awaiting_field"] == "order_id"

    graph.invoke(Command(resume={"order_id": "AB1234"}), config=config)
    graph.invoke(Command(resume={"confirm": True}), config=config)
    values = graph.get_state(config).values
    assert values["flow_id"] == "front-desk" and values["awaiting_field"] == "rating"
    assert values["links_done"] == [{"flow": "refund-path", "completed": True}]

    graph.invoke(Command(resume={"rating": 5}), config=config)
    assert graph.get_state(config).values["done"] is True
    assert fired == [("issue_refund", {"order_id": "AB1234"})]


def test_link_is_keyed_by_position_and_target():
    def ctx(done: list[str], completed: bool = True) -> Context:
        taken = [{"flow": flow, "completed": completed} for flow in done]
        return Context(state={"links_done": taken}, field_specs={}, emit=lambda _: None)

    done = ctx(["refund-path"])
    assert done.link("refund-path") is True, "already taken and finished: walked past"
    assert ctx(["refund-path"], completed=False).link("refund-path") is False, "taken, but the user cancelled it"
    with pytest.raises(FlowLink) as second:
        done.link("survey")
    assert (second.value.target_flow_id, second.value.index) == ("survey", 1), "the next link is a new one"

    # The branch flipped: a different target at the same position is a new link.
    with pytest.raises(FlowLink) as flipped:
        ctx(["refund-path"]).link("ticket-path")
    assert (flipped.value.target_flow_id, flipped.value.index) == ("ticket-path", 0)

    with pytest.raises(FlowLink) as fresh:
        ctx([]).link("refund-path")
    assert fresh.value.index == 0


def test_a_new_run_of_the_parent_starts_with_no_links_taken():
    """Otherwise the second visit to the front desk in one conversation would
    walk straight past its hand-off."""
    from aigenos_flow.runtime import initial_state, new_flow_updates, reset_for_new_flow

    state = initial_state()
    state["links_done"] = [{"flow": "refund-path", "completed": True}]
    assert new_flow_updates("front-desk", state)["links_done"] == []
    reset_for_new_flow(state, "front-desk")
    assert state["links_done"] == []


def _desk_linking_to(tmp_path: Path, target: str) -> FlowAgent:
    """A front desk whose only step is ctx.link(target), next to one real skill."""
    for name, skill_md in (("front-desk", FRONT_DESK), ("refund-path", REFUND)):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (tmp_path / "front-desk" / "handlers.py").write_text(textwrap.dedent(f"""\
        from aigenos_flow import flow

        @flow
        def front_desk(ctx):
            ctx.ask("wants_refund")
            ctx.link("{target}")
    """), encoding="utf-8")
    return FlowAgent.from_dir(tmp_path, host=TestHost(), tools={"issue_refund": lambda args: {"reference": "RF-1"}})


def test_linking_to_a_skill_that_is_not_loaded_says_so_and_leaves_the_parent_intact(tmp_path: Path):
    chat = _desk_linking_to(tmp_path, "refnd-path").session("front-desk")
    with pytest.raises(ValueError, match=r"ctx\.link\('refnd-path'\), but no such skill is loaded"):
        chat.submit(wants_refund=True)
    # The parent is still the active flow, with nothing left on the stack.
    assert chat.state["flow_id"] == "front-desk" and not chat.state.get("flow_stack")
    assert chat.state["fields"] == {"wants_refund": True}
    assert chat.state["links_done"] == []


def test_a_flow_cannot_link_to_itself(tmp_path: Path):
    chat = _desk_linking_to(tmp_path, "front-desk").session("front-desk")
    with pytest.raises(ValueError, match="cannot hand off to itself"):
        chat.submit(wants_refund=True)
    assert not chat.state.get("flow_stack")


NOTE = """\
---
name: note-{n}
description: Says one line. Use when asked for note {n}.
flow:
  - say: "note {n}"
---
Note.
"""


def _chain(tmp_path: Path) -> FlowAgent:
    """A router that links to three flows, none of which ever pauses."""
    for n in "abc":
        (tmp_path / f"note-{n}").mkdir()
        (tmp_path / f"note-{n}" / "SKILL.md").write_text(NOTE.format(n=n), encoding="utf-8")
    (tmp_path / "router").mkdir()
    (tmp_path / "router" / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: router
        description: Chains three notes. Use when asked to chain notes.
        handlers: ./handlers.py
        fields:
          go:
            type: boolean
            description: Go?
        ---
        Router.
    """), encoding="utf-8")
    (tmp_path / "router" / "handlers.py").write_text(textwrap.dedent("""\
        from aigenos_flow import flow

        @flow
        def router(ctx):
            ctx.ask("go")
            ctx.link("note-a")
            ctx.link("note-b")
            ctx.link("note-c")
            ctx.say("end of chain")
    """), encoding="utf-8")
    host = TestHost()
    host.checkpointer = MemorySaver()
    return FlowAgent.from_dir(tmp_path, host=host)


def test_a_chain_of_hand_offs_that_never_pause_finishes_in_one_turn(tmp_path: Path):
    """Each such link costs the turn two re-entries. With a budget of three the
    chain stopped after the second note, live but silent, until the user spoke."""
    chat = _chain(tmp_path).session("router").submit(go=True)
    assert chat.replies == ["note a", "note b", "note c", "end of chain"]
    assert chat.done


def test_the_same_chain_finishes_in_one_turn_through_the_compiled_graph(tmp_path: Path):
    agent = _chain(tmp_path)
    graph = agent.as_langgraph()
    config = {"configurable": {"thread_id": "chain"}}
    agent.host.queue_classification("chain the notes", "router")
    agent.host.queue_commands("chain the notes")
    graph.invoke({"pending_user_message": "chain the notes"}, config=config)
    graph.invoke(Command(resume={"go": True}), config=config)
    values = graph.get_state(config).values
    said = [m["content"] for m in values["history"] if m["role"] == "assistant"]
    assert said[-4:] == ["note a", "note b", "note c", "end of chain"]
    assert values["done"] is True


# --------------------------------------------------------------------------- the flow that handed off
def test_the_flow_that_handed_off_is_not_offered_as_another_workflow(desk, monkeypatch):
    """Inside the refund flow, "front-desk" is not somewhere else to go: the user
    is in the step it handed off. Offered to the model as a workflow to start,
    "lets send to vivek" was read as "start send-money"."""
    import aigenos_flow.runtime as runtime

    offered: list[list[str]] = []
    real = runtime.interpret_turn

    def spy(**kwargs):
        offered.append(sorted(s.name for s in kwargs.get("digressions") or []))
        return real(**kwargs)

    monkeypatch.setattr(runtime, "interpret_turn", spy)
    agent, host, _ = desk
    chat = agent.session("front-desk").submit(wants_refund=True)
    host.queue_commands("it is order AB1234", SetField(field="order_id", value="AB1234"))

    chat.user("it is order AB1234")

    assert offered[-1] == ["ticket-path"], "the waiting parent is not in the list"


def test_naming_the_flow_that_handed_off_does_not_end_the_errand(desk):
    """The model returned StartFlow(send-money) from inside add-payee. The engine
    "went back" to send-money, counted the hand-off as failed, and the errand
    ended with "No payee was added." in the middle of adding one."""
    agent, host, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True)
    assert chat.state["flow_id"] == "refund-path" and chat.awaiting == "order_id"
    host.queue_commands("refund order AB1234", StartFlow(flow_id="front-desk"),
                        SetField(field="order_id", value="AB1234"))

    chat.user("refund order AB1234")

    assert chat.state["flow_id"] == "refund-path" and _stack(chat) == ["front-desk"], "still in the refund flow"
    assert chat.state["links_done"] == [], "the hand-off was not written off"
    assert chat.awaiting == "confirm", "and the rest of the message was read there"
    assert fired == []


def test_coming_back_from_a_side_trip_lands_in_the_step_that_was_handed_off(desk):
    """front-desk -> refund-path, then a side trip to ticket-path. "Back to the
    front desk" resumes the refund flow the user was in, not its waiting parent."""
    agent, host, fired = desk
    chat = agent.session("front-desk").submit(wants_refund=True).submit(order_id="AB1234")
    host.queue_commands("open a ticket first", StartFlow(flow_id="ticket-path"))
    chat.user("open a ticket first")
    assert chat.state["flow_id"] == "ticket-path" and _stack(chat) == ["front-desk", "refund-path"]
    host.queue_commands("back to the front desk", StartFlow(flow_id="front-desk"))

    chat.user("back to the front desk")

    assert chat.state["flow_id"] == "refund-path" and _stack(chat) == ["front-desk"]
    assert chat.state["fields"].get("order_id") == "AB1234", "with what it had collected"
    assert chat.state["links_done"] == [] and chat.awaiting == "confirm"


def test_a_flow_that_ended_without_doing_anything_is_described_that_way():
    """Asked "did you send to vivek?" after an errand in which nothing was saved
    or sent, the assistant answered "I sent money to someone_new": it had been
    told only that send-money "just FINISHED", with the user's choice attached."""
    from types import SimpleNamespace

    from aigenos_flow.interpret import _render_finished_block

    skill = SimpleNamespace(name="send-money", description="The front door for sending money.", fields={})
    block = _render_finished_block(
        skill, {"pay_to": "someone_new"}, {},
        [{"flow": "add-payee", "completed": False}],
        [{"role": "user", "content": "lets send to vivek"}, {"role": "assistant", "content": "No payee was added."}],
    )

    assert "Ended does not mean it succeeded" in block
    assert "add-payee: NOT completed" in block
    assert "assistant: No payee was added." in block
    assert "ONLY if a result above or a" in block and "say plainly that it was NOT" in block
    assert "FINISHED" not in block

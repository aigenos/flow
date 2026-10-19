"""`link:` — one skill hands the user to another, from the SKILL.md itself.

Chaining skills used to need a Python router (``ctx.link`` in a handlers.py).
It is now a step kind like the others:

    - when: "need == 'refund'"
      link: refund-path
      then_say: "The refund is on its way."     # that skill did its job
      else_say: "No refund was made."           # it did not; this flow stops here

Two things came with it, because a router written in YAML needs them:

* ``when:`` works on ``say:`` too, so a branch can say which way it went.
* a field a gate reads is user-only without the author marking it.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from aigenos_flow import FlowAgent
from aigenos_flow.declarative import ToolRegistry
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Cancel, OutsideFlowDecision, SetField
from aigenos_flow.skills import load_skill, load_skills_dir

DESK = """\
---
name: desk
description: Front desk for order problems. Use when the user has a problem with an order.
fields:
  need:
    type: enum
    values: [refund, ticket]
    description: Do you want a refund, or to open a ticket?
  rating:
    type: integer
    min: 1
    max: 5
    description: How did we do, 1 to 5?
flow:
  - ask: need
  - when: "need == 'refund'"
    say: "A refund, then."
  - when: "need == 'refund'"
    link: refund-path
    then_say: "The refund is on its way."
    else_say: "No refund was made."
  - when: "need == 'ticket'"
    say: "Let's open a ticket."
  - when: "need == 'ticket'"
    link: ticket-path
    else_say: "No ticket was opened."
  - ask: rating
  - say: "Thanks. Back at the front desk."
---
Route the user to a refund or a ticket, then ask for a rating.
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

# A router that links to a router: "did its job" has to travel two levels.
LOBBY = """\
---
name: lobby
description: The lobby. Sends everyone to the front desk. Use when the user walks in.
flow:
  - say: "Welcome."
  - link: desk
    then_say: "All sorted."
    else_say: "Nothing was done at the desk."
---
Send the user to the front desk.
"""


def _write(root: Path, **skills: str) -> Path:
    for name, md in skills.items():
        folder = root / name.replace("_", "-")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(md, encoding="utf-8")
    return root


@pytest.fixture
def desk(tmp_path: Path):
    """(agent, host, fired): a YAML router, its two targets, and a router above it."""
    _write(tmp_path, desk=DESK, refund_path=REFUND, ticket_path=TICKET, lobby=LOBBY)
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


# --------------------------------------------------------------------------- the router is one file
def test_the_router_is_a_skill_file_and_nothing_else(desk, tmp_path):
    agent, _, _ = desk
    assert not (tmp_path / "desk" / "handlers.py").exists()
    skill = agent.registry.get("desk")
    assert skill.links == ["refund-path", "ticket-path"]
    # `need` decides the branch, so only the user may set it. Nobody marked it.
    assert "must_elicit" not in DESK and skill.fields["need"].must_elicit is True
    assert skill.fields["rating"].must_elicit is False


def test_one_branch_hands_off_comes_back_and_carries_on(desk):
    agent, _, fired = desk
    chat = agent.session("desk").submit(need="refund")
    assert chat.replies == ["A refund, then.", "Which order?"]
    assert chat.state["flow_id"] == "refund-path" and _stack(chat) == ["desk"]

    chat.submit(order_id="AB1234").submit(confirm=True)
    assert fired == [("issue_refund", {"order_id": "AB1234"})]
    # Back in the router on the line after the link, with the link's own line said.
    assert chat.replies == ["Refunded: RF-1.", "The refund is on its way.", "How did we do, 1 to 5?"]
    assert chat.state["flow_id"] == "desk" and _stack(chat) == [] and chat.awaiting == "rating"

    chat.submit(rating=5)
    assert chat.replies == ["Thanks. Back at the front desk."] and chat.done and len(fired) == 1


def test_the_other_branch_says_its_own_line_and_takes_its_own_link(desk):
    agent, _, fired = desk
    chat = agent.session("desk").submit(need="ticket")
    assert chat.replies == ["Let's open a ticket.", "What is it about?"]
    assert chat.state["flow_id"] == "ticket-path"

    chat.submit(topic="billing").submit(confirm=True)
    assert fired == [("open_ticket", {"topic": "billing"})]
    # This link has no then_say: nothing extra is said, and nothing from the other branch.
    assert chat.replies == ["Ticket opened: TK-1.", "How did we do, 1 to 5?"]
    chat.submit(rating=4)
    assert chat.replies == ["Thanks. Back at the front desk."] and chat.done


def test_nothing_is_said_twice_however_often_the_router_replays(desk):
    agent, host, _ = desk
    chat = agent.session("desk").submit(need="refund").submit(order_id="AB1234").submit(confirm=True)
    assert chat.awaiting == "rating"
    for text in ("hm", "what was that?"):
        host.queue_commands(text)
        chat.user(text)
        assert chat.replies == ["How did we do, 1 to 5?"], chat.replies
    chat.submit(rating=3)
    assert chat.replies == ["Thanks. Back at the front desk."]


# --------------------------------------------------------------------------- a hand-off that did nothing
def test_no_inside_the_linked_skill_stops_the_router_there(desk):
    """The bug class this default exists for: "No" at "Save this payee?" used to
    carry on to "Who are you paying?"."""
    agent, _, fired = desk
    chat = agent.session("desk").submit(need="refund").submit(order_id="AB1234").submit(confirm=False)

    assert chat.replies == ["Okay — no changes were made.", "No refund was made."]
    assert chat.done and chat.state["flow_id"] == "desk" and not fired
    assert chat.state["fields"].get("rating") is None  # the step after the link never ran
    assert [(e["flow"], e["outcome"]) for e in chat.state["journal"]] == [
        ("refund-path", "declined"), ("desk", "stopped"),
    ]


def test_cancelling_inside_the_linked_skill_stops_the_router_there(desk):
    agent, host, fired = desk
    chat = agent.session("desk").submit(need="ticket").submit(topic="billing")
    host.queue_commands("never mind", Cancel())
    chat.user("never mind")
    assert chat.replies == ["Okay, cancelled that.", "No ticket was opened."]
    assert chat.done and not fired


def _in_the_lobby(agent, host):
    """The lobby says hello and hands straight to the desk, which asks its question."""
    host.queue_commands("hi")
    chat = agent.session("lobby").user("hi")
    assert chat.replies == ["Welcome.", "Do you want a refund, or to open a ticket?"]
    assert chat.state["flow_id"] == "desk" and _stack(chat) == ["lobby"]
    return chat


def test_a_router_above_a_router_hears_that_nothing_was_done(desk):
    agent, host, fired = desk
    chat = _in_the_lobby(agent, host).submit(need="refund")
    assert _stack(chat) == ["lobby", "desk"] and chat.state["flow_id"] == "refund-path"
    chat.submit(order_id="AB1234").submit(confirm=False)
    assert chat.replies == [
        "Okay — no changes were made.", "No refund was made.", "Nothing was done at the desk.",
    ]
    assert chat.done and chat.state["flow_id"] == "lobby" and not fired


def test_a_router_above_a_router_hears_that_it_was_done(desk):
    agent, host, fired = desk
    chat = _in_the_lobby(agent, host).submit(need="refund").submit(order_id="AB1234").submit(confirm=True)
    chat.submit(rating=5)
    assert chat.replies == ["Thanks. Back at the front desk.", "All sorted."]
    assert chat.done and len(fired) == 1


# --------------------------------------------------------------------------- with the rest of the engine
def test_a_cancelled_yaml_errand_can_be_picked_up(desk):
    """`link:` and "continue" together: the parked run holds the router and the
    skill it had handed to."""
    agent, host, fired = desk
    chat = agent.session("desk").submit(need="refund").submit(order_id="AB1234")
    host.queue_commands("never mind", Cancel())
    chat.user("never mind")
    assert chat.done and chat.state["parked"]["root"] == "desk"

    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
    host.queue_commands("continue")
    chat.user("continue")
    assert chat.replies == ["Picking up where you left off.", "Refund this order?"]
    assert chat.state["flow_id"] == "refund-path" and _stack(chat) == ["desk"]

    chat.submit(confirm=True)
    assert fired == [("issue_refund", {"order_id": "AB1234"})]
    # "A refund, then." was said before the cancel and is not said again.
    assert chat.replies == ["Refunded: RF-1.", "The refund is on its way.", "How did we do, 1 to 5?"]


def test_the_model_cannot_pick_the_branch(desk):
    agent, host, _ = desk
    chat = agent.session("desk")
    host.queue_commands("I have a problem with my order", SetField(field="need", value="refund"))
    chat.user("I have a problem with my order")
    assert chat.state["fields"].get("need") is None and chat.state["flow_id"] == "desk"
    assert chat.awaiting == "need"


def test_the_yaml_router_through_the_compiled_graph(desk):
    agent, host, fired = desk
    graph = agent.as_langgraph()
    config = {"configurable": {"thread_id": "yaml-router"}}
    host.queue_classification("my order is wrong", "desk")
    host.queue_commands("my order is wrong")
    graph.invoke({"pending_user_message": "my order is wrong"}, config=config)
    graph.invoke(Command(resume={"need": "refund"}), config=config)
    graph.invoke(Command(resume={"order_id": "AB1234"}), config=config)
    graph.invoke(Command(resume={"confirm": True}), config=config)
    values = graph.get_state(config).values
    said = [m["content"] for m in values["history"] if m["role"] == "assistant"]
    assert said[-3:] == ["Refunded: RF-1.", "The refund is on its way.", "How did we do, 1 to 5?"]
    assert values["flow_id"] == "desk" and values["awaiting_field"] == "rating" and len(fired) == 1


# --------------------------------------------------------------------------- a guarded say keeps its place
STEPS = """\
---
name: steps
description: A flow whose first line is only said while the code is unknown. Use for testing.
fields:
  code:
    type: string
    pattern: '^[0-9]{4}$'
    description: What is the code?
  more:
    type: string
    pattern: '^[a-z]{1,10}$'
    description: Anything more?
flow:
  - when_missing: code
    say: "First, the code."
  - ask: code
  - say: "Thanks."
  - ask: more
  - say: "Done."
---
Test flow.
"""


def test_a_skipped_say_does_not_swallow_the_one_after_it(tmp_path):
    """`say` is "said once" by position. The first line is said, then its guard
    turns false; if it gave its place up, "Thanks." would move into it and be
    taken for a line already said."""
    _write(tmp_path, steps=STEPS)
    agent = FlowAgent.from_dir(tmp_path, host=TestHost())
    chat = agent.session("steps")
    chat.state["pending_user_message"] = None
    host = agent.host
    host.queue_commands("hello")
    chat.user("hello")
    assert chat.replies == ["First, the code.", "What is the code?"]
    chat.submit(code="1234")
    assert chat.replies == ["Thanks.", "Anything more?"]
    chat.submit(more="no")
    assert chat.replies == ["Done."] and chat.done


# --------------------------------------------------------------------------- the loader
def _tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("issue_refund", lambda args: {"reference": "RF-1"})
    registry.register("open_ticket", lambda args: {"reference": "TK-1"})
    return registry


def _load_one(tmp_path: Path, md: str, name: str = "desk"):
    folder = tmp_path / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(md, encoding="utf-8")
    return load_skill(folder)


@pytest.mark.parametrize("old,new,needle", [
    ("    link: refund-path", "    link: Refund Path", "must be the name of another skill"),
    ("    link: refund-path", "    link: desk", "names this skill itself"),
    ("    link: refund-path", "    lnik: refund-path", r"unknown key 'lnik' \(did you mean 'link'\?\)"),
    ('    then_say: "The refund is on its way."', "    else_clear: [need]", "never\n?.*reads 'else_clear'|never reads"),
    ('    else_say: "No refund was made."', '    else_say: "No refund for {{nobody}}."', "undeclared 'nobody'"),
    ("    link: refund-path", "    link: refund-path\n    ask: rating", "carries 2 verbs"),
])
def test_a_malformed_link_is_a_load_error(tmp_path, old, new, needle):
    assert DESK.count(old) == 1
    with pytest.raises(ValueError, match=needle):
        _load_one(tmp_path, DESK.replace(old, new))


def test_a_link_to_a_skill_that_is_not_there_does_not_load(tmp_path):
    _write(tmp_path, desk=DESK.replace("link: refund-path", "link: refund-pth"),
           refund_path=REFUND, ticket_path=TICKET, lobby=LOBBY)
    registry = load_skills_dir(tmp_path, tool_registry=_tools())
    # The router is out, and so is the router that links to it. The targets are fine.
    assert sorted(s.name for s in registry.all()) == ["refund-path", "ticket-path"]
    assert "`link: refund-pth` names a skill that is not a skill in this folder" in registry.errors["desk"]
    assert "(did you mean 'refund-path'?)" in registry.errors["desk"]
    assert "`link: desk` names a skill that did not load" in registry.errors["lobby"]

    with pytest.raises(ValueError, match="`link: refund-pth` names a skill that is not"):
        load_skills_dir(tmp_path, tool_registry=_tools(), strict=True)


def test_a_link_to_an_instruction_only_skill_does_not_load(tmp_path):
    notes = "---\nname: refund-path\ndescription: How we talk about refunds. Use when writing about refunds.\n---\nBe kind.\n"
    _write(tmp_path, desk=DESK, refund_path=notes, ticket_path=TICKET)
    registry = load_skills_dir(tmp_path, tool_registry=_tools())
    assert not registry.has("desk")
    assert "instruction-only skill with no flow to run" in registry.errors["desk"]


def test_skills_that_link_in_a_circle_do_not_load(tmp_path):
    """A hands to B and B hands back to A. The skill file has no loops on
    purpose; this would be one, with the stack growing every lap."""
    back = TICKET.replace("flow:\n  - ask: topic", "flow:\n  - link: desk\n  - ask: topic")
    assert "link: desk" in back
    _write(tmp_path, desk=DESK, refund_path=REFUND, ticket_path=back)
    registry = load_skills_dir(tmp_path, tool_registry=_tools())
    assert sorted(s.name for s in registry.all()) == ["refund-path"]
    assert "lead back to itself (desk -> ticket-path -> desk)" in registry.errors["desk"]
    # With desk gone, ticket-path links to a skill that did not load.
    assert "ticket-path" in registry.errors
    with pytest.raises(ValueError, match="lead back to itself"):
        load_skills_dir(tmp_path, tool_registry=_tools(), strict=True)


# --------------------------------------------------------------------------- the snippet in the developer guide
def test_the_developer_guides_router_is_a_file_that_runs(tmp_path):
    """The guide prints a front door for send-money. Copied out as printed, in
    front of the real add-payee and pay-someone skills, it has to do what the
    page says: one branch through add-payee, both through pay-someone, and a
    "no" in either stops it."""
    import re
    import shutil

    repo = Path(__file__).resolve().parents[3]
    guide = repo / "docs" / "developer-guide.md"
    shipped = repo / "examples" / "studio_agent" / "skills"
    if not guide.exists() or not shipped.exists():
        pytest.skip("repo docs and examples not present (packaged install)")
    block = next(b for b in re.findall(r"```yaml\n(.*?)```", guide.read_text(encoding="utf-8"), re.S)
                 if "do: add-payee" in b)
    assert block.startswith("steps:\n")
    for name in ("add-payee", "pay-someone", "tools"):          # the two real skills and the bank they share
        shutil.copytree(shipped / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "send-money").mkdir()
    (tmp_path / "send-money" / "SKILL.md").write_text(
        "---\nname: send-money\ndescription: The front door for sending money. Use when the user wants to pay someone.\n"
        + block + "---\nRoute the user.\n",
        encoding="utf-8",
    )
    agent = FlowAgent.from_dir(tmp_path, host=TestHost())
    assert agent.registry.get("send-money").links == ["add-payee", "pay-someone"]

    declined = agent.session("send-money").submit(pay_to="someone_new")
    assert declined.replies[0] == "They are not saved yet, so first add them as a payee."
    declined.submit(payee_name="Dana Lee").submit(routing_number="021000021").submit(account_number="4455667788")
    declined.submit(confirm=False)
    assert "No payee was added." in declined.replies and declined.done
    assert declined.state["flow_id"] == "send-money"  # it never reached pay-someone

    saved = agent.session("send-money").submit(pay_to="a_saved_payee")
    assert saved.replies[0] == "Here are your saved payees." and saved.state["flow_id"] == "pay-someone"
    saved.submit(payee_id="P-1001").submit(amount=25).submit(confirm=True)
    assert saved.replies[-1] == "That is everything. Anything else?" and saved.done

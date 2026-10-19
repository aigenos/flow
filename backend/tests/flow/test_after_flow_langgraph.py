"""After a flow finishes, talking about it must not start it again.

Through ``wrap_agent`` / ``create_agent`` the entry node used to run the
cold-start classifier on every message once a flow was done. A finished flow's
own description matches anything said about it — "how much did I just send?" —
so a follow-up question restarted the flow ("How much do you want to send?")
instead of reaching the agent. The entry node now asks the runtime's
outside-flow question first: start something, or talk?
"""
from __future__ import annotations

import warnings
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from aigenos_flow import SetField, wrap_agent
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Cancel, OutsideFlowDecision

SKILLS = Path(__file__).resolve().parents[3] / "examples" / "before_after" / "skills_after"
FIRST = "send $500 to mom"
FOLLOW_UP = "how much did I just send?"
AGAIN = "send another $200 to the gym"


class _S(TypedDict, total=False):
    messages: Annotated[list, add_messages]


def _finished_transfer():
    """A wrapped agent on a thread where one $500 transfer has completed."""
    fired: list[dict] = []
    g = StateGraph(_S)
    g.add_node("chat", lambda s: {"messages": [AIMessage(content="(agent) you sent $500 to mom")]})
    g.add_edge(START, "chat")
    g.add_edge("chat", END)

    host = TestHost()
    host.queue_classification(FIRST, "transfer-funds")
    host.queue_commands(FIRST, SetField(field="amount", value=500), SetField(field="payee", value="mom"))
    # The trap: asked cold, the classifier WOULD send the follow-up to the skill.
    host.queue_classification(FOLLOW_UP, "transfer-funds")
    host.queue_commands(AGAIN, SetField(field="amount", value=200), SetField(field="payee", value="gym"))

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        agent = wrap_agent(g.compile(), skills_dir=SKILLS, host=host,
                           tools={"bank_transfer": lambda args: fired.append(dict(args)) or {"txn": "T"}})
    cfg = {"configurable": {"thread_id": "t"}}
    agent.invoke({"messages": [("user", FIRST)]}, config=cfg)
    agent.invoke(Command(resume={"confirm": True}), config=cfg)
    assert agent.get_state(cfg).values["done"] and len(fired) == 1
    return agent, host, cfg, fired


def test_a_question_about_the_finished_flow_goes_to_the_agent():
    agent, host, cfg, fired = _finished_transfer()
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="reply", reply="unused"))

    out = agent.invoke({"messages": [("user", FOLLOW_UP)]}, config=cfg)
    snap = agent.get_state(cfg)

    assert "(agent)" in out["messages"][-1].content, "the agent must answer, not the flow"
    assert not any(t.interrupts for t in snap.tasks), "the finished flow must not be waiting on a field again"
    assert snap.values["done"] and len(fired) == 1


def test_a_real_new_request_still_starts_the_flow():
    agent, host, cfg, fired = _finished_transfer()
    host.set_schema_override(
        OutsideFlowDecision, OutsideFlowDecision(action="start_skill", skill="transfer-funds")
    )

    agent.invoke({"messages": [("user", AGAIN)]}, config=cfg)
    state = agent.get_state(cfg).values

    assert state["flow_id"] == "transfer-funds" and not state["done"]
    assert state["fields"].get("amount") == 200 and state["awaiting_field"] == "confirm"
    assert len(fired) == 1, "a new run has not fired anything until it is confirmed"

    agent.invoke(Command(resume={"confirm": True}), config=cfg)
    assert [f["amount"] for f in fired] == [500.0, 200.0]



def test_a_cancelled_flow_can_be_picked_up_through_the_wrapped_agent():
    """Cancel a transfer at its confirmation, then "continue": the entry node
    puts the run back with what was entered, and asks for the yes again."""
    fired: list[dict] = []
    g = StateGraph(_S)
    g.add_node("chat", lambda s: {"messages": [AIMessage(content="(agent)")]})
    g.add_edge(START, "chat")
    g.add_edge("chat", END)
    host = TestHost()
    host.queue_classification(FIRST, "transfer-funds")
    host.queue_commands(FIRST, SetField(field="amount", value=500), SetField(field="payee", value="mom"))
    host.queue_commands("never mind", Cancel())
    host.queue_commands("continue")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        agent = wrap_agent(g.compile(), skills_dir=SKILLS, host=host,
                           tools={"bank_transfer": lambda args: fired.append(dict(args)) or {"txn": "T"}})
    cfg = {"configurable": {"thread_id": "resume"}}
    agent.invoke({"messages": [("user", FIRST)]}, config=cfg)
    agent.invoke(Command(resume="never mind"), config=cfg)
    state = agent.get_state(cfg).values
    assert state["flow_id"] is None and state["parked"]["fields"] == {"amount": 500.0, "payee": "mom"}
    assert state["journal"][-1]["outcome"] == "cancelled"

    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
    out = agent.invoke({"messages": [("user", "continue")]}, config=cfg)
    state = agent.get_state(cfg).values
    assert state["flow_id"] == "transfer-funds" and not state["done"]
    assert state["fields"].get("amount") == 500 and state["awaiting_field"] == "confirm"
    assert state["parked"] is None and not fired
    assert any("Picking up where you left off." in str(m.content) for m in out["messages"])

    agent.invoke(Command(resume={"confirm": True}), config=cfg)
    assert [f["amount"] for f in fired] == [500.0]

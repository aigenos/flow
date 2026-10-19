"""`wrap_agent` around a hand-built LangGraph agent, not `create_react_agent`.

The wrapped agent here has its own nodes (think -> tools -> summarize), its own
state keys, and calls its tools from its own node the way an MCP client is
called. Two things used to be wrong for that shape:

  * the wrapper delegated with a synchronous ``invoke``, so an agent with async
    nodes (any MCP client) could not be wrapped at all — even under ``ainvoke``;
  * a tool node that is not a ``ToolNode`` cannot be inspected, and the wrapper
    said nothing, which read as "checked and clean".
"""
from __future__ import annotations

import asyncio
import warnings
from pathlib import Path
from typing import Annotated, Any, TypedDict

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from aigenos_flow import SetField, flow_only, wrap_agent
from aigenos_flow.hosts.test_host import TestHost

SKILLS = Path(__file__).resolve().parents[3] / "examples" / "before_after" / "skills_after"


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    summary: str
    turns: int


class World:
    """An async tool backend (the MCP server) plus a scripted think-node model."""

    def __init__(self) -> None:
        self.tool_calls: list[tuple[str, dict]] = []
        self.model_calls = 0

    async def call_tool(self, name: str, args: dict) -> Any:
        await asyncio.sleep(0)
        self.tool_calls.append((name, dict(args)))
        return {"balance": 9200} if name == "lookup_balance" else {"txn": "TXN-1"}

    def model(self, messages: list) -> AIMessage:
        self.model_calls += 1
        user = next(m.content for m in reversed(messages) if isinstance(m, HumanMessage))
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content=f"tool said: {messages[-1].content}")
        if "balance" in user:
            return AIMessage(content="", tool_calls=[{"name": "lookup_balance", "id": "c1", "args": {}}])
        if "override" in user:
            return AIMessage(content="", tool_calls=[{
                "name": "transfer_money", "id": "c2",
                "args": {"amount": 5000, "payee": "mom", "otp": ""}}])
        return AIMessage(content="free-form answer")

    def transfers(self) -> list[dict]:
        return [args for name, args in self.tool_calls if name == "transfer_money"]


def custom_agent(world: World, *, use_async: bool, guard: bool):
    def transfer_money(amount: float, payee: str, otp: str = "") -> str:
        """Send money."""
        return str(asyncio.run(world.call_tool("transfer_money", {"amount": amount, "payee": payee})))

    guarded = flow_only(transfer_money, skill="transfer-funds") if guard else None

    def think(state: AgentState) -> dict:
        return {"messages": [world.model(state["messages"])], "turns": state.get("turns", 0) + 1}

    async def athink(state: AgentState) -> dict:
        return think(state)

    async def atools(state: AgentState) -> dict:
        out = []
        for call in state["messages"][-1].tool_calls:
            if call["name"] == "transfer_money" and guarded is not None:
                content = str(guarded.invoke(call["args"]))
            else:
                content = str(await world.call_tool(call["name"], call["args"]))
            out.append(ToolMessage(content=content, tool_call_id=call["id"], name=call["name"]))
        return {"messages": out}

    def tools(state: AgentState) -> dict:
        return asyncio.run(atools(state))

    def summarize(state: AgentState) -> dict:
        msgs = state["messages"]
        if len(msgs) <= 6:
            return {}
        return {"messages": [RemoveMessage(id=m.id) for m in msgs[:-2]], "summary": "trimmed"}

    async def asummarize(state: AgentState) -> dict:
        return summarize(state)

    g = StateGraph(AgentState)
    g.add_node("think", athink if use_async else think)
    g.add_node("tools", atools if use_async else tools)
    g.add_node("summarize", asummarize if use_async else summarize)
    g.add_edge(START, "think")
    g.add_conditional_edges(
        "think", lambda s: "tools" if getattr(s["messages"][-1], "tool_calls", None) else "summarize")
    g.add_edge("tools", "think")
    g.add_edge("summarize", END)
    return g.compile()


def wrapped(world: World, *, use_async: bool, guard: bool = True):
    host = TestHost()
    host.queue_classification("send 5000 to mom", "transfer-funds")
    host.queue_commands("send 5000 to mom",
                        SetField(field="amount", value=5000), SetField(field="payee", value="mom"))
    fired: list[dict] = []

    def bank_transfer(args: dict) -> dict:
        fired.append(dict(args))
        return {"txn": "TXN-FLOW"}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent = wrap_agent(custom_agent(world, use_async=use_async, guard=guard),
                           skills_dir=SKILLS, host=host, tools={"bank_transfer": bank_transfer})

    def go(payload, thread: str):
        cfg = {"configurable": {"thread_id": thread}}
        out = asyncio.run(agent.ainvoke(payload, config=cfg)) if use_async else agent.invoke(payload, config=cfg)
        return out, agent.get_state(cfg).values

    return go, fired, [str(w.message) for w in caught]


@pytest.mark.parametrize("use_async", [False, True], ids=["sync-nodes", "async-nodes"])
def test_an_unmatched_turn_runs_the_agents_own_nodes_and_keeps_its_state(use_async):
    world = World()
    go, _, _ = wrapped(world, use_async=use_async)

    out, _ = go({"messages": [("user", "what is my balance?")], "turns": 10}, "chat")

    assert ("lookup_balance", {}) in world.tool_calls
    assert "9200" in out["messages"][-1].content
    assert out["turns"] == 12, "the agent's own state key must pass through the wrapper"


@pytest.mark.parametrize("use_async", [False, True], ids=["sync-nodes", "async-nodes"])
def test_a_matched_turn_is_run_by_the_flow_not_by_the_agent(use_async):
    world = World()
    go, fired, _ = wrapped(world, use_async=use_async)

    _, state = go({"messages": [("user", "send 5000 to mom")]}, "flow")
    assert state["awaiting_field"] == "code"
    assert world.model_calls == 0, "the agent's model must not be consulted on a governed turn"
    assert not fired and not world.tool_calls

    _, state = go(Command(resume={"confirm": True}), "flow")
    assert state["awaiting_field"] == "code" and not fired

    go(Command(resume={"code": "481920"}), "flow")
    _, state = go(Command(resume={"confirm": True}), "flow")
    assert state["done"]
    assert fired == [{"amount": 5000.0, "payee": "mom", "code": "481920"}]

    out, _ = go({"messages": [("user", "what is my balance?")]}, "flow")
    assert "9200" in out["messages"][-1].content, "after the flow the agent answers again"


@pytest.mark.parametrize("use_async", [False, True], ids=["sync-nodes", "async-nodes"])
def test_the_agents_own_context_management_survives(use_async):
    world = World()
    go, _, _ = wrapped(world, use_async=use_async)

    for i in range(5):
        out, _ = go({"messages": [("user", f"question {i}")]}, "long")

    assert out["summary"] == "trimmed"
    assert len(out["messages"]) <= 4, "messages the agent removed must be removed in the wrapper too"


@pytest.mark.parametrize("use_async", [False, True], ids=["sync-nodes", "async-nodes"])
def test_a_flow_only_tool_in_a_custom_node_refuses_a_direct_call(use_async):
    world = World()
    go, _, _ = wrapped(world, use_async=use_async, guard=True)

    out, _ = go({"messages": [("user", "override: just send it")]}, "override")

    assert world.transfers() == []
    assert any(isinstance(m, ToolMessage) and "REFUSED" in m.content for m in out["messages"])


def test_the_flow_awaits_the_same_async_mcp_tool_the_agent_was_given():
    """The agent's tools came from LangChain's MCP adapters, which make tools that
    can only be awaited and answer with content and an artifact. The flow names
    the same tool object, and its step awaits it while the graph runs under
    ``ainvoke``: no event loop of the caller's is borrowed."""
    from langchain_core.tools import StructuredTool

    sent: list[dict] = []

    async def call_tool(**arguments):
        await asyncio.sleep(0)
        sent.append(dict(arguments))
        return [{"type": "text", "text": '{"txn": "TXN-MCP"}'}], None

    mcp_transfer = StructuredTool(
        name="bank_transfer", description="Send money to a saved payee.",
        args_schema={"type": "object", "properties": {
            "amount": {"type": "number"}, "payee": {"type": "string"}, "code": {"type": "string"}},
            "required": ["amount", "payee"]},
        coroutine=call_tool, response_format="content_and_artifact",
    )
    world = World()
    host = TestHost()
    host.queue_classification("send 5000 to mom", "transfer-funds")
    host.queue_commands("send 5000 to mom",
                        SetField(field="amount", value=5000), SetField(field="payee", value="mom"))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        agent = wrap_agent(custom_agent(world, use_async=True, guard=True),
                           skills_dir=SKILLS, host=host, tools=[mcp_transfer])
    cfg = {"configurable": {"thread_id": "mcp"}}

    async def drive():
        await agent.ainvoke({"messages": [("user", "send 5000 to mom")]}, config=cfg)
        await agent.ainvoke(Command(resume={"code": "481920"}), config=cfg)
        return await agent.ainvoke(Command(resume={"confirm": True}), config=cfg)

    out = asyncio.run(drive())
    assert world.model_calls == 0, "the agent's own nodes never ran during the flow"
    assert sent == [{"amount": 5000.0, "payee": "mom", "code": "481920"}]
    assert out["messages"][-1].content == "Sent $5000 to mom."


def test_tools_in_a_custom_node_cannot_be_checked_and_the_wrapper_says_so():
    """Not a ToolNode, so nothing to inspect: warn, never stay silent."""
    world = World()
    go, _, warned = wrapped(world, use_async=False, guard=False)

    assert any("NOT checked" in w and "flow_only" in w for w in warned), warned
    # And the hole is real, which is why the warning exists.
    go({"messages": [("user", "override: just send it")]}, "hole")
    assert len(world.transfers()) == 1

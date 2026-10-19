"""Async tools, the tools LangChain's MCP adapters make, and MCP servers reached by URL.

An agent that already loads its MCP tools with LangChain's adapters hands the
same tool objects to a flow. Those tools can only be awaited, describe their
inputs in JSON Schema, carry the server's ``readOnlyHint`` in ``metadata`` and
answer with content plus a structured artifact. A step has to call them as
they are: typed inputs, the read-only hint honoured, the structured result
bound, and no event loop of the caller's needed.
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from aigenos_flow import FlowAgent, readonly
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.toolbox import describe_tool, invoke_langchain

StructuredTool = pytest.importorskip("langchain_core.tools").StructuredTool

PAY = """---
name: pay-from-account
description: Pay someone from one of the user's accounts.
steps:
  - ask: { account_id: "Which account?" }
  - say: "Balance: {{get_balance.balance}} {{get_balance.currency}}."
  - ask: { amount: "How much?" }
    check: { amount <= get_balance.balance: "That is more than the balance." }
  - confirm: Send this payment?
  - do: send_payment
  - say: "Sent. Reference {{send_payment.reference}}."
---
Pay from an account.
"""


def _adapter_tool(name: str, description: str, schema: dict, answer, *, reads: bool) -> StructuredTool:
    """A tool the way langchain-mcp-adapters builds one from an MCP server's tool."""

    async def call_tool(**arguments):
        await asyncio.sleep(0)
        return answer(arguments)

    return StructuredTool(
        name=name, description=description, args_schema=schema, coroutine=call_tool,
        response_format="content_and_artifact",
        metadata={"readOnlyHint": True, "title": None} if reads else {"title": None},
    )


def _bank(calls: list) -> dict:
    def balance(arguments):
        data = {"account_id": arguments["account_id"], "balance": 1250.5, "currency": "USD"}
        # MCP gives text and, from servers that send it, a structured result.
        return [{"type": "text", "text": json.dumps(data)}], SimpleNamespace(structured_content=data)

    def payment(arguments):
        calls.append(dict(arguments))
        # No structured result: the text is JSON, and that is what gets bound.
        return [{"type": "text", "text": json.dumps({"reference": f"PAY-{len(calls):04d}"})}], None

    return {
        "get_balance": _adapter_tool(
            "get_balance", "The balance of an account.",
            {"type": "object", "properties": {"account_id": {"type": "string"}}, "required": ["account_id"]},
            balance, reads=True,
        ),
        "send_payment": _adapter_tool(
            "send_payment", "Send a payment from an account.",
            {"type": "object", "properties": {"account_id": {"type": "string"}, "amount": {"type": "number"}},
             "required": ["account_id", "amount"]},
            payment, reads=False,
        ),
    }


def _agent(tmp_path: Path, name: str, md: str, tools: dict):
    (tmp_path / name).mkdir()
    (tmp_path / name / "SKILL.md").write_text(md, encoding="utf-8")
    host = TestHost()
    host.queue_commands("hello")
    return FlowAgent.from_dir(tmp_path, host=host, tools=tools)


def test_an_mcp_adapter_tool_says_what_it_takes_and_whether_it_only_reads():
    tools = _bank([])
    lookup = describe_tool("get_balance", tools["get_balance"])
    assert lookup.readonly is True, "the server's readOnlyHint makes it a lookup"
    assert [(p.name, p.type, p.required) for p in lookup.params] == [("account_id", "string", True)]
    write = describe_tool("send_payment", tools["send_payment"])
    assert write.readonly is False, "no hint: it changes something"
    assert [(p.name, p.type) for p in write.params] == [("account_id", "string"), ("amount", "number")]


def test_a_flow_runs_the_tools_an_agent_loaded_with_langchains_mcp_adapters(tmp_path):
    calls: list = []
    agent = _agent(tmp_path, "pay-from-account", PAY, _bank(calls))
    chat = agent.session("pay-from-account").user("hello")
    assert chat.awaiting == "account_id"
    chat.submit(account_id="acc-1")
    assert "Balance: 1250.5 USD." in chat.replies, "the structured result is what the step binds"
    chat.submit(amount=2000)
    assert "That is more than the balance." in chat.replies and chat.awaiting == "amount"
    chat.submit(amount=100)
    assert chat.awaiting == "confirm" and calls == [], "nothing is sent before the yes"
    chat.submit(confirm=True)
    assert chat.replies[-1] == "Sent. Reference PAY-0001." and chat.done
    assert calls == [{"account_id": "acc-1", "amount": 100.0}], "sent once, with exactly what was confirmed"


def test_an_mcp_error_is_raised_never_bound_as_a_value():
    from langchain_core.tools import ToolException

    async def failing(**arguments):
        # What the adapters raise for a result with isError set.
        raise ToolException("account frozen")

    tool = StructuredTool(
        name="send_payment", description="Send a payment.",
        args_schema={"type": "object", "properties": {"amount": {"type": "number"}}},
        coroutine=failing, response_format="content_and_artifact", handle_tool_error=True,
    )
    # handle_tool_error turns it into an error message for an agent; a step raises.
    with pytest.raises(RuntimeError, match="reported an error: account frozen"):
        invoke_langchain(tool, {"amount": 5})


CONVERT = """---
name: convert-money
description: Convert an amount of dollars into another currency.
steps:
  - ask: { amount: "How much, in USD?" }
  - ask: { currency: "Into which currency?" }
    choices: [EUR, GBP]
  - say: "That is {{convert.converted}} {{currency}}."
---
Convert money.
"""


@readonly
async def convert(amount: float, currency: str) -> dict:
    """Convert dollars at today's rate."""
    await asyncio.sleep(0)
    return {"converted": round(amount * {"EUR": 0.5, "GBP": 0.25}[currency], 2)}


def test_an_async_def_tool_runs_even_when_the_caller_is_inside_an_event_loop(tmp_path):
    agent = _agent(tmp_path, "convert-money", CONVERT, {"convert": convert})
    assert describe_tool("convert", convert).readonly is True

    async def inside_a_running_loop():
        # Sync code called from a coroutine, as a graph node driven with
        # ainvoke runs: asyncio.run() is not allowed here.
        chat = agent.session("convert-money").user("hello")
        chat.submit(amount=10).submit(currency="EUR")
        return chat

    chat = asyncio.run(inside_a_running_loop())
    assert chat.replies[-1] == "That is 5 EUR." and chat.done


# --------------------------------------------------------------------------- a remote MCP server
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_mcp_tools_reach_a_remote_server_by_url():
    pytest.importorskip("mcp")
    uvicorn = pytest.importorskip("uvicorn")
    from mcp.types import ToolAnnotations

    from aigenos_flow.mcp_tools import MCPTools

    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as Server
    except ModuleNotFoundError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server

    server = Server("bank")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def get_balance(account_id: str) -> dict:
        """The balance of an account."""
        return {"account_id": account_id, "balance": 1250.5}

    @server.tool()
    def send_payment(account_id: str, amount: float) -> dict:
        """Send a payment from an account."""
        return {"reference": "PAY-1"}

    port = _free_port()
    web = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port,
                                        log_level="warning"))
    thread = threading.Thread(target=web.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not web.started and time.time() < deadline:
        time.sleep(0.05)
    assert web.started, "the test server did not start"
    try:
        with MCPTools(url=f"http://127.0.0.1:{port}/mcp", headers={"X-Test": "1"}) as bank:
            tools = bank.tools()
            assert sorted(tools) == ["get_balance", "send_payment"]
            assert describe_tool("get_balance", tools["get_balance"]).readonly is True
            assert describe_tool("send_payment", tools["send_payment"]).readonly is False
            assert tools["get_balance"]({"account_id": "acc-1"})["balance"] == 1250.5
            assert bank.calls == [("get_balance", {"account_id": "acc-1"})]
    finally:
        web.should_exit = True
        thread.join(timeout=15)


def test_mcp_tools_needs_a_command_or_a_url_not_both():
    pytest.importorskip("mcp")
    from aigenos_flow.mcp_tools import MCPTools

    with pytest.raises(ValueError, match="either the command"):
        MCPTools()
    with pytest.raises(ValueError, match="either the command"):
        MCPTools(["python", "server.py"], url="http://127.0.0.1:1/mcp")
    with pytest.raises(ValueError, match="transport"):
        MCPTools(url="http://127.0.0.1:1/mcp", transport="websocket")

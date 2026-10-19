"""create_agent / wrap_agent — the two front doors.

Solution 1: create_agent(llm, skills_dir) IS the agent — flow-matching
queries run deterministically, everything else is ReAct.

Solution 2: wrap_agent(existing_agent, ...) adds the same determinism in
front of an agent the user already built.

Zero API key: a scripted chat model plays the ReAct LLM; TestHost scripts
the flow-side interpretation/classification. The engine, graph routing,
gates, and widget attachment all run for real.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command

from aigenos_flow import SetField, create_agent, wrap_agent
from aigenos_flow.hosts.test_host import TestHost

SKILL = """\
---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000+ require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:    { type: number, min: 0.01, must_elicit: true }
  recipient: { type: enum, values: [mom, landlord, gym] }
  verification_code: { type: string, required: false, pattern: '^[0-9]{6}$', sensitive: true }
  confirm:   { type: boolean }
compute:
  requires_2fa: "amount >= 1000"
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
flow:
  - ask: amount
  - ask: recipient
  - when: "requires_2fa"
    ask: verification_code
  - say: "Send {{amount}} to {{recipient}}?"
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Collect amount and payee, then confirm.
"""

HOSTILE = "send $5000 to mom"
SMALLTALK = "what is a wire transfer?"


class ScriptedModel(GenericFakeChatModel):
    """Replays scripted replies; bind_tools is a no-op (scripted messages
    already carry any tool calls)."""

    def bind_tools(self, tools, **kwargs):  # noqa: D102
        return self


@pytest.fixture()
def parts(tmp_path: Path):
    (tmp_path / "transfer-funds").mkdir()
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    fired: list[dict] = []

    @tool
    def bank_transfer(amount: float, payee: str, otp: str = "") -> dict:
        """Move money (granted tool for the transfer-funds skill).

        A LangChain tool keeps its natural kwargs signature — the runtime
        maps the typed action args onto it via .invoke().
        """
        fired.append({"amount": amount, "payee": payee, "otp": otp})
        return {"txn": f"TXN-{len(fired)}"}

    host = TestHost()
    host.queue_classification(HOSTILE, "transfer-funds")
    host.queue_classification(SMALLTALK, None)
    host.queue_commands(
        HOSTILE,
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )
    return tmp_path, host, fired, bank_transfer


def drive_flow_and_assert(graph, host, fired):
    cfg = {"configurable": {"thread_id": "t1"}}

    # Hostile query → classifier activates the skill → flow pauses at the gate.
    graph.invoke({"messages": [("user", HOSTILE)]}, config=cfg)
    values = graph.get_state(cfg).values
    assert values["flow_id"] == "transfer-funds"
    assert values["awaiting_field"] == "verification_code"
    assert fired == []

    # a2ui=True: the interrupt payload carries the input widget spec.
    (task,) = [t for t in graph.get_state(cfg).tasks if t.interrupts]
    payload = task.interrupts[0].value
    assert payload["field"] == "verification_code"
    assert isinstance(payload.get("widget"), dict) and payload["widget"]

    # ...and so does the paused assistant message.
    widget_msgs = [
        m for m in values["messages"]
        if isinstance(m, AIMessage) and m.additional_kwargs.get("aigenos_widget")
    ]
    assert widget_msgs, "paused message should carry additional_kwargs['aigenos_widget']"

    # Widget submissions resume the flow — pure Python, no LLM.
    graph.invoke(Command(resume={"verification_code": "481920"}), config=cfg)
    graph.invoke(Command(resume={"confirm": True}), config=cfg)
    values = graph.get_state(cfg).values
    assert values["done"] is True
    assert len(fired) == 1 and fired[0]["otp"] == "481920"

    # After the flow: an unrelated question routes to the wrapped/ReAct agent.
    out = graph.invoke({"messages": [("user", SMALLTALK)]}, config=cfg)
    assert out["messages"][-1].content == "A wire transfer moves money between banks."
    assert len(fired) == 1  # chatting cannot re-fire anything


def test_solution_1_create_agent(parts):
    skills_dir, host, fired, bank_transfer = parts
    llm = ScriptedModel(
        messages=iter([AIMessage(content="A wire transfer moves money between banks.")])
    )
    graph = create_agent(llm, skills_dir, tools=[bank_transfer], host=host)
    drive_flow_and_assert(graph, host, fired)


def test_create_agent_refuses_a_typoed_flow_key_instead_of_failing_open(tmp_path):
    """hn-3: `flowz:` instead of `flow:` used to demote the gated skill to plain
    prompt text and hand `bank_transfer` straight to the model — no warning, no
    error, money moved with otp=''. The typo is a load error now, on the
    documented `create_agent` front door as well as `FlowAgent.from_dir`."""
    (tmp_path / "transfer-funds").mkdir()
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(
        SKILL.replace("\nflow:\n", "\nflowz:\n"), encoding="utf-8"
    )

    @tool
    def bank_transfer(amount: float, payee: str, otp: str = "") -> dict:
        """Move money."""
        return {"txn": "TXN-1"}

    llm = ScriptedModel(messages=iter([AIMessage(content="hi")]))
    with pytest.raises(ValueError, match=r"unknown key 'flowz' \(did you mean 'flow'\?\)"):
        create_agent(llm, tmp_path, tools=[bank_transfer], host=TestHost())


def test_solution_2_wrap_existing_agent(parts):
    skills_dir, host, fired, bank_transfer = parts
    llm = ScriptedModel(
        messages=iter([AIMessage(content="A wire transfer moves money between banks.")])
    )
    existing = create_react_agent(llm, tools=[], prompt="You are my existing agent.")
    graph = wrap_agent(existing, skills_dir=skills_dir, host=host, tools=[bank_transfer])
    drive_flow_and_assert(graph, host, fired)


def test_a2ui_false_skips_widgets(parts):
    skills_dir, host, fired, bank_transfer = parts
    llm = ScriptedModel(messages=iter([AIMessage(content="hi")]))
    graph = create_agent(llm, skills_dir, tools=[bank_transfer], host=host, a2ui=False)
    cfg = {"configurable": {"thread_id": "t1"}}
    graph.invoke({"messages": [("user", HOSTILE)]}, config=cfg)
    (task,) = [t for t in graph.get_state(cfg).tasks if t.interrupts]
    assert "widget" not in task.interrupts[0].value
    values = graph.get_state(cfg).values
    assert not any(
        isinstance(m, AIMessage) and m.additional_kwargs.get("aigenos_widget")
        for m in values["messages"]
    )

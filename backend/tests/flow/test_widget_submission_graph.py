"""End-to-end widget submission through the COMPILED LangGraph.

The companion ``test_widget_submission_bypass.py`` exercises ``run_turn``
directly. That is necessary but not sufficient: it passed even while the
shipped graph (``FlowAgent.as_langgraph``) stringified widget dicts into
``pending_user_message`` and fed them to the LLM — the confirm-loop bug.

This test drives the *actual* compiled graph the backend serves: first turn
asks a boolean ``confirm`` field, then a structured ``{"confirm": "true"}``
widget submission is resumed in via ``Command(resume=...)``. The bypass must
apply it without an interpreter round-trip.

The teeth: ``TestHost(strict=True)`` raises if ``llm_structured`` is called
for an unscripted message. If the bypass regresses and the stringified dict
("confirm: True") reaches ``interpret_turn``, this test fails loudly instead
of hanging in a re-ask loop.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from aigenos_flow.agent import FlowAgent
from aigenos_flow.hosts.test_host import TestHost


def _write_confirm_skill(tmp: Path) -> None:
    skill_dir = tmp / "confirm-demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: confirm-demo
        description: A tiny flow that just asks for a boolean confirm.
        license: MIT
        handlers: ./handlers.py
        triggers:
          - confirm
        fields:
          confirm:
            type: boolean
            description: user confirms (yes / no)
        ---

        # Confirm demo
    """), encoding="utf-8")
    (skill_dir / "handlers.py").write_text(textwrap.dedent("""\
        from aigenos_flow import flow

        @flow
        def confirm_demo(ctx):
            if ctx.ask("confirm"):
                ctx.say("Booked!")
            else:
                ctx.say("Cancelled.")
    """), encoding="utf-8")


def _build_graph(tmp_path: Path):
    _write_confirm_skill(tmp_path)
    host = TestHost()
    # as_langgraph reads the checkpointer off the host; interrupt/resume needs one.
    host.checkpointer = MemorySaver()
    agent = FlowAgent.from_dir(tmp_path, host=host, mode="flex")
    return agent.as_langgraph(), host


def test_widget_submit_through_compiled_graph_bypasses_llm(tmp_path: Path):
    graph, host = _build_graph(tmp_path)
    config = {"configurable": {"thread_id": "t1"}}

    # First turn: route to the flow, interpret an empty turn, then pause on the
    # unfilled `confirm` field. Both LLM calls of this turn are scripted.
    host.queue_classification("start", "confirm-demo")
    host.queue_commands("start")  # no items — flow falls through to ctx.ask

    graph.invoke({"pending_user_message": "start"}, config=config)

    snap = graph.get_state(config)
    assert snap.values.get("awaiting_user") is True
    assert snap.values.get("awaiting_field") == "confirm"

    # Resume with a STRUCTURED widget submission. No message is scripted for the
    # transcript "confirm: True" — if the bypass regresses and that string hits
    # the interpreter, TestHost(strict=True) raises here.
    graph.invoke(Command(resume={"confirm": "true"}), config=config)

    final = graph.get_state(config).values
    assert final["fields"].get("confirm") is True
    assert final.get("done") is True
    assert final.get("pending_field_updates") in (None, {})
    history_text = " ".join(m.get("content", "") for m in final.get("history", []))
    assert "Booked!" in history_text


def test_widget_submit_false_value_routes_without_llm(tmp_path: Path):
    graph, host = _build_graph(tmp_path)
    config = {"configurable": {"thread_id": "t2"}}

    host.queue_classification("start", "confirm-demo")
    host.queue_commands("start")
    graph.invoke({"pending_user_message": "start"}, config=config)

    # "false" must also bypass the interpreter and drive the negative branch.
    graph.invoke(Command(resume={"confirm": "false"}), config=config)

    final = graph.get_state(config).values
    assert final["fields"].get("confirm") is False
    assert final.get("done") is True
    history_text = " ".join(m.get("content", "") for m in final.get("history", []))
    assert "Cancelled." in history_text

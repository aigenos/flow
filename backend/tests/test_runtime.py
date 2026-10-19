"""Smoke test for the workflow runtime — walks a 2-node flow end to end."""
from __future__ import annotations

import asyncio

import pytest

from aigenos_flow.engine import runtime
from aigenos_flow.schemas.widget_spec import WidgetSpec, SpecNode
from aigenos_flow.schemas.workflow import Workflow, WorkflowNode


@pytest.fixture(autouse=True)
def _seed_runtime(monkeypatch: pytest.MonkeyPatch):
    widget = WidgetSpec(
        id="w",
        version="1.0.0",
        spec=SpecNode(type="card", props={"title": "Hello {{name}}"}),
    )
    wf = Workflow(
        id="t",
        start="ask",
        nodes=[
            WorkflowNode(id="ask", type="widget", widget="w", props={"name": "Ada"}, save_to="form", next="end"),
            WorkflowNode(id="end", type="end"),
        ],
    )
    monkeypatch.setattr(runtime, "WIDGETS", {widget.id: widget})
    monkeypatch.setattr(runtime, "WORKFLOWS", {wf.id: wf})
    runtime.SESSIONS.clear()


async def _drain(session, expected_kinds: list[str], submit_after_widget: dict | None = None):
    seen = []
    submitted = False
    async for ev in runtime.run(session):
        seen.append(ev.type)
        if ev.type == "TOOL_CALL_END" and not submitted and submit_after_widget is not None:
            session.resume_queue.put_nowait(submit_after_widget)
            submitted = True
    return seen


async def test_workflow_emits_run_lifecycle_and_widget():
    session = runtime.new_session("t")
    events = await _drain(session, [], submit_after_widget={"answer": "yes"})

    assert events[0] == "RUN_STARTED"
    assert "TOOL_CALL_START" in events
    assert "TOOL_CALL_ARGS" in events
    assert "TOOL_CALL_END" in events
    assert "TOOL_CALL_RESULT" in events
    assert events[-1] == "RUN_FINISHED"
    assert session.state["form"] == {"answer": "yes"}


async def test_unknown_workflow_raises():
    with pytest.raises(KeyError):
        runtime.new_session("does_not_exist")

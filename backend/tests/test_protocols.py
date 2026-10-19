"""Smoke tests for A2UI and MCP-UI adapters."""
from __future__ import annotations

import json
from typing import AsyncIterator

import pytest

from aigenos_flow.agui import events as e
from aigenos_flow.agui.protocols.a2ui import to_a2ui_sse
from aigenos_flow.agui.protocols.mcp_ui import to_mcp_ui_sse


async def _stream(items: list[e.AnyEvent]) -> AsyncIterator[e.AnyEvent]:
    for it in items:
        yield it


async def _collect(agen) -> list[dict]:
    return [chunk async for chunk in agen]


def _ev_names(chunks: list[dict]) -> list[str]:
    return [c["event"] for c in chunks]


async def test_a2ui_translates_run_lifecycle_and_text():
    events = [
        e.RunStarted(thread_id="t1", run_id="r1"),
        e.TextMessageStart(message_id="m1"),
        e.TextMessageContent(message_id="m1", delta="hi"),
        e.TextMessageEnd(message_id="m1"),
        e.RunFinished(thread_id="t1", run_id="r1"),
    ]
    chunks = await _collect(to_a2ui_sse(_stream(events)))
    assert _ev_names(chunks) == [
        "a2ui.run.start", "a2ui.message.start", "a2ui.message.delta", "a2ui.message.end", "a2ui.run.end",
    ]


async def test_a2ui_translates_widget_tool_call_to_ui_resource():
    payload = {"widget_id": "w", "version": "1.0", "spec": {"type": "card"}, "submit_schema": {}}
    events = [
        e.ToolCallStart(tool_call_id="tc1", tool_call_name="render_widget"),
        e.ToolCallArgs(tool_call_id="tc1", delta=json.dumps(payload)),
        e.ToolCallEnd(tool_call_id="tc1"),
    ]
    chunks = await _collect(to_a2ui_sse(_stream(events)))
    assert len(chunks) == 1
    assert chunks[0]["event"] == "a2ui.ui_resource"
    data = json.loads(chunks[0]["data"])
    assert data["widget_id"] == "w"
    assert data["spec"] == {"type": "card"}


async def test_mcp_ui_emits_resource_update_for_widget():
    payload = {"widget_id": "w", "version": "1.0", "spec": {"type": "card"}, "submit_schema": {}}
    events = [
        e.ToolCallStart(tool_call_id="tc1", tool_call_name="render_widget"),
        e.ToolCallArgs(tool_call_id="tc1", delta=json.dumps(payload)),
        e.ToolCallEnd(tool_call_id="tc1"),
    ]
    chunks = await _collect(to_mcp_ui_sse(_stream(events)))
    assert len(chunks) == 1
    assert chunks[0]["event"] == "notifications/resources/updated"
    data = json.loads(chunks[0]["data"])
    assert data["uri"].startswith("ui://widget/w/")
    assert data["mimeType"].endswith("+json")
    inner = json.loads(data["text"])
    assert inner["spec"] == {"type": "card"}


async def test_mcp_ui_translates_text_streaming():
    events = [
        e.TextMessageStart(message_id="m1"),
        e.TextMessageContent(message_id="m1", delta="hello"),
        e.TextMessageEnd(message_id="m1"),
    ]
    chunks = await _collect(to_mcp_ui_sse(_stream(events)))
    assert _ev_names(chunks) == [
        "notifications/message/started",
        "notifications/message/delta",
        "notifications/message/completed",
    ]

"""A2UI protocol adapter.

A2UI groups events into thinner semantic categories than AG-UI: messages,
ui_resources (widgets), state. We translate AG-UI tool calls named
`render_widget` into A2UI `ui_resource` events and stream text deltas as
`message_delta`. The wire spec is still evolving; this is a best-effort,
forward-compatible mapping that downstream A2UI clients can consume.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from ..events import AnyEvent


async def to_a2ui_sse(events: AsyncIterator[AnyEvent]) -> AsyncIterator[dict[str, str]]:
    pending_args: dict[str, str] = {}
    pending_names: dict[str, str] = {}

    async for ev in events:
        t = ev.type

        if t == "RUN_STARTED":
            yield _sse("a2ui.run.start", {"thread_id": ev.thread_id, "run_id": ev.run_id})

        elif t == "RUN_FINISHED":
            yield _sse("a2ui.run.end", {"thread_id": ev.thread_id, "run_id": ev.run_id})

        elif t == "RUN_ERROR":
            yield _sse("a2ui.error", {"message": ev.message, "code": ev.code})

        elif t == "TEXT_MESSAGE_START":
            yield _sse("a2ui.message.start", {"id": ev.message_id, "role": ev.role})

        elif t == "TEXT_MESSAGE_CONTENT":
            yield _sse("a2ui.message.delta", {"id": ev.message_id, "delta": ev.delta})

        elif t == "TEXT_MESSAGE_END":
            yield _sse("a2ui.message.end", {"id": ev.message_id})

        elif t == "TOOL_CALL_START":
            pending_names[ev.tool_call_id] = ev.tool_call_name
            pending_args[ev.tool_call_id] = ""

        elif t == "TOOL_CALL_ARGS":
            pending_args[ev.tool_call_id] = pending_args.get(ev.tool_call_id, "") + ev.delta

        elif t == "TOOL_CALL_END":
            args = pending_args.pop(ev.tool_call_id, "")
            name = pending_names.pop(ev.tool_call_id, "")
            try:
                parsed = json.loads(args) if args else {}
            except json.JSONDecodeError:
                parsed = {"_raw": args}
            if name == "render_widget":
                yield _sse("a2ui.ui_resource", {
                    "id": ev.tool_call_id,
                    "kind": "widget",
                    "widget_id": parsed.get("widget_id"),
                    "version": parsed.get("version"),
                    "spec": parsed.get("spec"),
                    "submit_schema": parsed.get("submit_schema"),
                })
            else:
                yield _sse("a2ui.tool_call", {"id": ev.tool_call_id, "name": name, "args": parsed})

        elif t == "TOOL_CALL_RESULT":
            try:
                content = json.loads(ev.content)
            except json.JSONDecodeError:
                content = ev.content
            yield _sse("a2ui.ui_resource_result", {"id": ev.tool_call_id, "result": content})

        elif t == "STATE_SNAPSHOT":
            yield _sse("a2ui.state", {"snapshot": ev.snapshot})

        elif t == "STATE_DELTA":
            yield _sse("a2ui.state.delta", {"delta": ev.delta})

        elif t == "STEP_STARTED":
            yield _sse("a2ui.step.start", {"name": ev.step_name})

        elif t == "STEP_FINISHED":
            yield _sse("a2ui.step.end", {"name": ev.step_name})


def _sse(event_name: str, data: dict) -> dict[str, str]:
    return {"event": event_name, "data": json.dumps(data)}

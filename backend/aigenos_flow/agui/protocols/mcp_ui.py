"""MCP-UI protocol adapter.

MCP-UI ships UI as `resource` payloads with a `ui://` URI and a mimeType.
For our declarative widget specs we use `application/vnd.aigenos.flow-spec+json`
as the mime type and `text/plain` chunks for streamed messages. Widget
submissions are returned via a tool-result-style envelope. This is a
forward-compatible mapping against the current mcp-apps draft.

Each widget resource carries an ``interaction.resume`` descriptor naming the
real HTTP request that delivers the user's submission, so an MCP host can post
it without reading our docs.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

from ..events import AnyEvent


SPEC_MIME = "application/vnd.aigenos.flow-spec+json"

def resume_descriptor(
    thread_id: str | None, tool_call_id: str, resume_url: str | None
) -> dict[str, Any] | None:
    """How an MCP-UI host returns this widget's submission.

    ``resume_url`` comes from the router that is streaming, so it is correct
    whatever prefix the router was mounted under, and the request body IS the
    submission object with no envelope — that is what ``POST
    <prefix>/{thread_id}/resume`` takes.

    Returns ``None`` when the caller did not supply a URL. Guessing one is how
    the old ``ui-action://submit/<id>`` field came about: a target no code in
    this product served or consumed. A client with no descriptor falls back to
    the resume endpoint its own surface documents.
    """
    if not resume_url:
        return None
    return {
        "method": "POST",
        "url": resume_url,
        "content_type": "application/json",
        # Send the submission as the whole JSON body: {"confirm": "true"}.
        "body_is_submission": True,
        "thread_id": thread_id,
        "tool_call_id": tool_call_id,
    }


async def to_mcp_ui_sse(
    events: AsyncIterator[AnyEvent],
    *,
    resume_url: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    pending_args: dict[str, str] = {}
    pending_names: dict[str, str] = {}
    thread_id: str | None = None

    async for ev in events:
        t = ev.type

        if t == "RUN_STARTED":
            thread_id = ev.thread_id
            yield _sse("notifications/run/started", {"threadId": ev.thread_id, "runId": ev.run_id})

        elif t == "RUN_FINISHED":
            yield _sse("notifications/run/finished", {"threadId": ev.thread_id, "runId": ev.run_id})

        elif t == "RUN_ERROR":
            yield _sse("notifications/error", {"message": ev.message})

        elif t == "TEXT_MESSAGE_START":
            yield _sse("notifications/message/started", {"id": ev.message_id, "role": ev.role})

        elif t == "TEXT_MESSAGE_CONTENT":
            yield _sse("notifications/message/delta", {
                "id": ev.message_id,
                "contents": [{"type": "text", "text": ev.delta}],
            })

        elif t == "TEXT_MESSAGE_END":
            yield _sse("notifications/message/completed", {"id": ev.message_id})

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
                widget_id = parsed.get("widget_id", "unknown")
                interaction: dict[str, Any] = {"tool_call_id": ev.tool_call_id}
                resume = resume_descriptor(thread_id, ev.tool_call_id, resume_url)
                if resume is not None:
                    interaction["resume"] = resume
                yield _sse("notifications/resources/updated", {
                    "uri": f"ui://widget/{widget_id}/{ev.tool_call_id}",
                    "name": widget_id,
                    "mimeType": SPEC_MIME,
                    "text": json.dumps({
                        "widget_id": widget_id,
                        "version": parsed.get("version"),
                        "spec": parsed.get("spec"),
                        "submit_schema": parsed.get("submit_schema"),
                        "interaction": interaction,
                    }),
                })
            else:
                yield _sse("notifications/tools/called", {
                    "id": ev.tool_call_id,
                    "name": name,
                    "arguments": parsed,
                })

        elif t == "TOOL_CALL_RESULT":
            try:
                content = json.loads(ev.content)
            except json.JSONDecodeError:
                content = ev.content
            yield _sse("notifications/tools/result", {
                "id": ev.tool_call_id,
                "content": [{"type": "text", "text": json.dumps(content)}],
            })

        elif t == "STATE_SNAPSHOT":
            yield _sse("notifications/state/snapshot", {"state": ev.snapshot})

        elif t == "STATE_DELTA":
            yield _sse("notifications/state/delta", {"delta": ev.delta})

        elif t == "STEP_STARTED":
            yield _sse("notifications/step/started", {"name": ev.step_name})

        elif t == "STEP_FINISHED":
            yield _sse("notifications/step/finished", {"name": ev.step_name})


def _sse(event_name: str, data: dict) -> dict[str, str]:
    return {"event": event_name, "data": json.dumps(data)}

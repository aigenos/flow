"""Workflow runtime.

Drives a YAML workflow node-by-node, emitting AG-UI events. Pauses when it hits
a widget node and resumes when the client posts a submission. Session state is
held in-memory (skip persistence per Phase-2-deferred scope).
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ..agui import events as e
from ..schemas.widget_spec import SpecNode, WidgetSpec
from ..schemas.workflow import Workflow, WorkflowNode
from .bindings import resolve
from .loader import load_widgets, load_workflows
from .safe_eval import safe_eval

_log = logging.getLogger(__name__)


WIDGETS: dict[str, WidgetSpec] = {}
WORKFLOWS: dict[str, Workflow] = {}


def reload_assets() -> None:
    global WIDGETS, WORKFLOWS
    WIDGETS = load_widgets()
    WORKFLOWS = load_workflows()


reload_assets()


@dataclass
class Session:
    thread_id: str
    workflow_id: str
    state: dict[str, Any] = field(default_factory=dict)
    current_node: str | None = None
    pending_tool_call_id: str | None = None
    pending_widget_id: str | None = None
    resume_queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    finished: bool = False


SESSIONS: dict[str, Session] = {}


def new_session(workflow_id: str) -> Session:
    wf = WORKFLOWS.get(workflow_id)
    if not wf:
        raise KeyError(f"Unknown workflow: {workflow_id}")
    s = Session(thread_id=uuid.uuid4().hex, workflow_id=workflow_id, current_node=wf.start)
    SESSIONS[s.thread_id] = s
    return s


def get_session(thread_id: str) -> Session:
    s = SESSIONS.get(thread_id)
    if not s:
        raise KeyError(f"Unknown session: {thread_id}")
    return s


def _resolve_spec(node: SpecNode, state: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": node.type,
        "props": resolve(node.props, state),
        "children": [_resolve_spec(c, state) for c in node.children],
    }


async def _emit_text(message: str) -> AsyncIterator[e.AnyEvent]:
    mid = e.new_message_id()
    yield e.TextMessageStart(message_id=mid)
    yield e.TextMessageContent(message_id=mid, delta=message)
    yield e.TextMessageEnd(message_id=mid)


async def _emit_widget(session: Session, widget_id: str, props: dict[str, Any]) -> AsyncIterator[e.AnyEvent]:
    widget = WIDGETS.get(widget_id)
    if not widget:
        yield e.RunError(message=f"Unknown widget: {widget_id}")
        return

    merged_state = {**session.state, **props}
    resolved_spec = _resolve_spec(widget.spec, merged_state)

    payload = {
        "widget_id": widget.id,
        "version": widget.version,
        "spec": resolved_spec,
        "submit_schema": widget.submit_schema,
    }

    tcid = e.new_tool_call_id()
    session.pending_tool_call_id = tcid
    session.pending_widget_id = widget.id

    yield e.ToolCallStart(tool_call_id=tcid, tool_call_name="render_widget")
    yield e.ToolCallArgs(tool_call_id=tcid, delta=json.dumps(payload))
    yield e.ToolCallEnd(tool_call_id=tcid)


def _pick_next_branch(node: WorkflowNode, state: dict[str, Any]) -> str | None:
    for branch in node.branches:
        cond = branch.get("if")
        if cond is None:
            return branch.get("next")
        try:
            if safe_eval(cond, state):
                return branch.get("next")
        except Exception:
            _log.warning("branch condition %r failed to evaluate; skipping", cond)
            continue
    return None


async def run(session: Session) -> AsyncIterator[e.AnyEvent]:
    """Drive the workflow until completion or until pausing on a widget."""
    wf = WORKFLOWS[session.workflow_id]
    nodes = wf.node_map()
    run_id = e.new_run_id()
    yield e.RunStarted(thread_id=session.thread_id, run_id=run_id)

    try:
        while session.current_node and not session.finished:
            node = nodes.get(session.current_node)
            if not node:
                yield e.RunError(message=f"Unknown node: {session.current_node}")
                break

            yield e.StepStarted(step_name=node.id)

            if node.type == "message":
                text = resolve(node.text or "", session.state)
                async for ev in _emit_text(text):
                    yield ev
                session.current_node = node.next

            elif node.type == "widget":
                resolved_props = resolve(node.props, session.state)
                async for ev in _emit_widget(session, node.widget or "", resolved_props):
                    yield ev
                yield e.StepFinished(step_name=node.id)
                # pause — wait for client submission
                submission = await session.resume_queue.get()
                if node.save_to:
                    session.state[node.save_to] = submission
                yield e.ToolCallResult(
                    tool_call_id=session.pending_tool_call_id or "",
                    message_id=e.new_message_id(),
                    content=json.dumps(submission),
                )
                yield e.StateDelta(delta=[{"op": "replace", "path": "/", "value": session.state}])
                session.pending_tool_call_id = None
                session.pending_widget_id = None
                session.current_node = node.next
                continue

            elif node.type == "tool_call":
                # Stub tool runner — real impl would dispatch to host tools.
                result = {"ok": True, "tool": node.tool, "args": resolve(node.args, session.state)}
                if node.save_to:
                    session.state[node.save_to] = result
                session.current_node = node.next

            elif node.type == "branch":
                session.current_node = _pick_next_branch(node, session.state)

            elif node.type == "end":
                session.finished = True
                break

            yield e.StepFinished(step_name=node.id)

    except Exception as exc:  # noqa: BLE001
        yield e.RunError(message=str(exc))

    yield e.RunFinished(thread_id=session.thread_id, run_id=run_id)


def submit(thread_id: str, payload: dict[str, Any]) -> None:
    session = get_session(thread_id)
    session.resume_queue.put_nowait(payload)

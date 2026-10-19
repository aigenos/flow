"""LangGraph integration — the bridge between your existing ReAct/Deep agents
and the aigenos/flow runtime.

This module exposes:

* ``render_widget``: a LangChain tool you add to your agent's tool list.
  When the LLM calls it, the graph interrupts and surfaces a widget to the
  client. The user's submission becomes the tool's return value.

* ``make_chat_router(agent)``: a FastAPI router that wires AG-UI / A2UI /
  MCP-UI streaming + submission resume onto your existing app. Mount it under
  any prefix and it gives you a complete chat surface.

Usage::

    from langgraph.prebuilt import create_react_agent
    from langgraph.checkpoint.memory import MemorySaver
    from aigenos_flow.integrations.langgraph import render_widget, make_chat_router

    agent = create_react_agent(
        model="claude-sonnet-5-5",
        tools=[render_widget, *your_other_tools],
        checkpointer=MemorySaver(),  # required for interrupt/resume
    )

    app.include_router(make_chat_router(agent), prefix="/chat")

The router also serves an agent built by ``create_agent`` / ``wrap_agent``:
when such an agent's flow pauses to collect a field, the widget resolved for
that field is streamed as a ``render_widget`` tool call, exactly as
``make_flow_router`` does. That is what makes "the widget rides on the paused
assistant message, so your AG-UI/A2UI client renders it from the stream" true
for both routers.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict
from sse_starlette.sse import EventSourceResponse

from ..agui import events as e
from ..agui.protocols import SUPPORTED, emit
from ..engine import runtime as af_runtime
from ..engine.bindings import resolve

log = logging.getLogger("aigenos_flow.chat_router")


# ---------- The tool ----------

@tool
def render_widget(widget_id: str, props: dict | None = None) -> dict:
    """Render an interactive UI widget to the user and wait for their response.

    Use this whenever you need structured input from the user (a form, a
    confirmation, a selection) or want to present structured output (a card,
    a chart, a table).

    You choose WHICH widget and its props — never the markup. The widget's
    spec lives in the catalog on the server; call ``widget_catalog_prompt()``
    to list the registered ids. Passing a spec object instead of an id is an
    error.

    Args:
        widget_id: The id of a registered widget. Check the widget catalog
            in the system context for available ids and their expected props.
        props: Values for any {{bindings}} the widget declares (e.g. dynamic
            titles, prefilled values, options).

    Returns:
        The user's submission payload as a dict. For forms, this is keyed by
        the form's field names. For confirmations, the button's payload.
    """
    widget = af_runtime.WIDGETS.get(widget_id)
    if not widget:
        return {"_error": f"unknown widget '{widget_id}'. Known: {list(af_runtime.WIDGETS)}"}

    # Resolve {{bindings}} so the spec sent to the client is fully concrete.
    state = {**(props or {})}
    resolved_spec = resolve(widget.spec.model_dump(exclude_none=True), state)

    # Pause the graph. Whatever the client posts to /resume becomes this
    # function's return value.
    submission = interrupt({
        "kind": "render_widget",
        "widget_id": widget.id,
        "version": widget.version,
        "spec": resolved_spec,
        "submit_schema": widget.submit_schema,
        "props": state,
    })
    return submission


def widget_catalog_prompt() -> str:
    """A prompt fragment listing currently-registered widgets.

    Append this to your agent's system prompt so the LLM knows what widget_ids
    are valid and what shape their submissions take.
    """
    if not af_runtime.WIDGETS:
        return "No widgets registered."
    lines = ["You have the following UI widgets available via render_widget(widget_id, props):", ""]
    for w in af_runtime.WIDGETS.values():
        lines.append(f"- {w.id} (v{w.version}): {w.description or '(no description)'}")
        if w.bindings:
            lines.append(f"    expects props: {w.bindings}")
        if w.submit_schema:
            lines.append(f"    submits: {json.dumps(w.submit_schema)}")
    return "\n".join(lines)


# ---------- Wire-payload normalisation ----------
#
# Three producers hand us a widget and they do NOT agree on shape:
#
#   render_widget's interrupt  -> {kind, widget_id, version, spec, submit_schema}
#   a flow pause (wait_node)   -> {"widget": <WidgetSpec dump: id/version/spec/…>}
#   ctx.show / autowidget      -> a bare SpecNode dict ({type, props, children})
#
# The client contract is ONE shape — WidgetPayload in frontend/src/agui/types.ts
# — so everything funnels through here. Getting this wrong is invisible on the
# Python side and renders nothing in the browser, which is exactly what
# happened before: the stream gated the widget path on `kind == "render_widget"`
# and a flow pause has no `kind`.

_WIRE_KEYS = ("widget_id", "version", "spec", "submit_schema")


def to_widget_payload(widget: Any) -> dict[str, Any] | None:
    """Coerce any of the three producer shapes into the client's WidgetPayload.

    Returns ``None`` when ``widget`` carries nothing renderable.
    """
    if not isinstance(widget, dict) or not widget:
        return None

    spec = widget.get("spec")
    if spec is None:
        # A bare SpecNode (ctx.show / autowidget.spec_for) — the node IS the spec.
        if not widget.get("type"):
            return None
        return {
            "widget_id": str(widget.get("id") or "autowidget"),
            "version": str(widget.get("version") or "1.0.0"),
            "spec": widget,
            "submit_schema": None,
        }
    if not isinstance(spec, dict):
        return None
    return {
        # WidgetSpec dumps call it `id`; the wire calls it `widget_id`.
        "widget_id": str(widget.get("widget_id") or widget.get("id") or ""),
        "version": str(widget.get("version") or "1.0.0"),
        "spec": spec,
        "submit_schema": widget.get("submit_schema"),
    }


def widget_from_interrupt(payload: Any) -> dict[str, Any] | None:
    """The widget an interrupt payload is asking the client to render.

    Handles both interrupt producers: ``render_widget``'s own payload, and the
    flow pause payload that ``create_agent`` / ``wrap_agent`` emit (which has
    no ``kind`` key at all — the bug this function exists to close).
    """
    if not isinstance(payload, dict):
        return None
    if payload.get("kind") == "render_widget" or ("widget_id" in payload and "spec" in payload):
        return to_widget_payload(payload)
    return to_widget_payload(payload.get("widget"))


def _widget_sig(payload: dict[str, Any]) -> str:
    """A stable key for "this exact widget was already streamed this run".

    A flow pause hands us the same widget twice — once on the assistant
    message's ``aigenos_widget``, once in the interrupt payload — and two
    skeletons for one question is a broken transcript.
    """
    return json.dumps(payload, sort_keys=True, default=str)


# ---------- The router ----------

class ChatRequest(BaseModel):
    # A body-shape drift must fail loudly. Before this, `skill` (which
    # useFlowChat sends on every opening turn) was silently dropped and the
    # pin did nothing.
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = None
    message: str | None = None
    resume_payload: dict[str, Any] | None = None
    # Pin the run to one skill instead of letting the classifier choose.
    # Honoured only when the wrapped graph actually has a flow to pin (i.e.
    # it came from create_agent / wrap_agent); otherwise the request is
    # refused rather than accepted and ignored.
    skill: str | None = None


_TRACKER_MAX = 512


def _graph_has_flow(agent: Any) -> bool:
    """Does this compiled graph carry aigenos/flow's state keys?

    ``make_chat_router`` accepts any LangGraph graph, so ``skill=`` can only be
    honoured for one built by ``create_agent`` / ``wrap_agent``.
    """
    for attr in ("builder", "graph"):
        builder = getattr(agent, attr, None)
        schema = getattr(builder, "schema", None) or getattr(builder, "state_schema", None)
        keys = getattr(schema, "__annotations__", None)
        if keys and "flow_id" in keys:
            return True
    try:
        props = agent.get_input_jsonschema().get("properties", {})  # type: ignore[union-attr]
        return "flow_id" in props
    except Exception:  # noqa: BLE001
        return False


def make_chat_router(agent, *, default_protocol: str = "ag-ui") -> APIRouter:
    """Build a FastAPI router that streams AG-UI / A2UI / MCP-UI events from a
    LangGraph agent, with /resume to deliver widget submissions.

    The router is stateless as far as conversation state goes — it relies on the
    agent's checkpointer. Pass any LangGraph compiled graph (e.g. from
    create_react_agent, create_agent or wrap_agent).
    """
    router = APIRouter()
    # thread_id -> the tool_call_id of the widget that thread is waiting on.
    # Purely a UI correlation id so the resume turn can emit TOOL_CALL_RESULT
    # against the right widget; losing it on a restart costs a resolved-tick,
    # never correctness. Bounded so a long-lived process cannot grow it.
    widget_waits: "OrderedDict[str, str]" = OrderedDict()
    supports_skill_pin = _graph_has_flow(agent)

    @router.post("/")
    @router.post("")
    async def chat(
        req: ChatRequest,
        base_path: str = Depends(request_path),
        protocol: str = Query(default_protocol),
    ):
        if protocol not in SUPPORTED:
            raise HTTPException(400, f"unsupported protocol: {protocol}")
        if req.message is None and req.resume_payload is None:
            raise HTTPException(400, "either `message` or `resume_payload` is required")

        thread_id = req.thread_id or uuid.uuid4().hex
        config = {"configurable": {"thread_id": thread_id}}

        if req.resume_payload is not None:
            graph_input: Any = Command(resume=dict(req.resume_payload))
        else:
            graph_input = {"messages": [{"role": "user", "content": req.message}]}
            if req.skill:
                if not supports_skill_pin:
                    raise HTTPException(
                        400,
                        "`skill` is not supported for this agent: the wrapped graph has "
                        "no flow state to pin (it was not built by create_agent / "
                        "wrap_agent). Use make_flow_router, or an agent from "
                        "create_agent(), to enter a skill directly.",
                    )
                # Same seeding Scenario.run and make_flow_router use — enter the
                # flow directly and skip first-turn classification. A FRESH
                # flow_run_id comes with it, or the run's idempotency key would
                # be None and its side effect unguarded.
                graph_input.update(_new_run_updates(agent, config, req.skill))

        async def gen():
            async for sse_event in emit(
                protocol,
                _agui_event_stream(
                    agent,
                    graph_input,
                    config,
                    thread_id,
                    widget_waits=widget_waits,
                    resume_payload=req.resume_payload,
                ),
                resume_url=resume_url_for(base_path, thread_id),
            ):
                yield sse_event

        return EventSourceResponse(gen())

    @router.post("/{thread_id}/resume")
    async def resume(
        thread_id: str,
        payload: dict[str, Any],
        base_path: str = Depends(request_path),
        protocol: str = Query(default_protocol),
    ):
        """Convenience alias for resuming a thread without echoing thread_id in the body.

        ``useFlowChat`` re-POSTs the base endpoint with ``{thread_id,
        resume_payload}`` instead; both are equivalent server-side.
        """
        return await chat(
            ChatRequest(thread_id=thread_id, resume_payload=payload),
            base_path,
            protocol=protocol,
        )

    return router


async def request_path(request: Request) -> str:
    """The path the client POSTed to.

    A FastAPI dependency rather than a ``request: Request`` route parameter so
    it also works from ``integrations/flow.py``, which keeps fastapi out of its
    module-level imports (the annotation there would not resolve, and
    ``from __future__ import annotations`` makes that silent).
    """
    return request.url.path or "/"


def resume_url_for(base_path: str | None, thread_id: str) -> str:
    """The absolute path a client POSTs a widget submission to.

    Derived from the request the client just made, so it is correct whatever
    prefix the router was mounted under. MCP-UI embeds it in each widget
    resource (see agui/protocols/mcp_ui.resume_descriptor).
    """
    suffix = f"/{thread_id}/resume"
    if not base_path:
        # No request in scope (an internal call) — a relative path still
        # resolves against the endpoint the client POSTed to.
        return suffix.lstrip("/")
    base = base_path.rstrip("/")
    # Already the resume alias: don't append it twice.
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def _new_run_updates(agent: Any, config: dict, skill: str) -> dict[str, Any]:
    """State updates that start a fresh, pinned flow run on this thread."""
    from ..runtime import new_flow_updates

    state: dict[str, Any] = {}
    try:
        snap = agent.get_state(config)
        values = getattr(snap, "values", None)
        if isinstance(values, dict):
            state = values
    except Exception:  # noqa: BLE001
        pass
    updates = new_flow_updates(skill, state)
    # `messages` is set by the caller; never let the reset clobber it.
    updates.pop("messages", None)
    return updates


async def _agui_event_stream(
    agent,
    graph_input,
    config,
    thread_id,
    *,
    widget_waits: "OrderedDict[str, str] | None" = None,
    resume_payload: dict[str, Any] | None = None,
) -> AsyncIterator[e.AnyEvent]:
    """Translate LangGraph's astream output into AG-UI internal events.

    Emits RUN_STARTED → (TOOL_CALL_RESULT for the widget just submitted) →
    (TEXT_MESSAGE_* | TOOL_CALL_*) → STATE_SNAPSHOT → RUN_FINISHED.

    Assistant text is read from EVERY node's update, not from a node called
    "agent": ``create_agent``'s graph names its nodes entry/react/flow/wait,
    and keying off "agent" dropped the flow's own prompts on the floor.
    """
    import asyncio

    run_id = e.new_run_id()
    yield e.RunStarted(thread_id=thread_id, run_id=run_id)

    # The resume turn settles the widget the previous turn streamed. Without
    # this a client can never tell an accepted widget from an abandoned one.
    if resume_payload is not None and widget_waits is not None:
        prev_tcid = widget_waits.pop(thread_id, None)
        if prev_tcid:
            yield e.ToolCallResult(
                tool_call_id=prev_tcid,
                message_id=e.new_message_id(),
                content=json.dumps(resume_payload, default=str),
            )

    emitted_widgets: set[str] = set()
    seen_message_ids: set[str] = set()
    emitted_text: list[str] = []
    paused_on: dict[str, Any] | None = None
    errored = False

    def _widget_events(payload: dict[str, Any]):
        sig = _widget_sig(payload)
        if sig in emitted_widgets:
            return
        emitted_widgets.add(sig)
        tcid = e.new_tool_call_id()
        if widget_waits is not None and payload.get("submit_schema"):
            widget_waits[thread_id] = tcid
            while len(widget_waits) > _TRACKER_MAX:
                widget_waits.popitem(last=False)
        yield e.ToolCallStart(tool_call_id=tcid, tool_call_name="render_widget")
        yield e.ToolCallArgs(tool_call_id=tcid, delta=json.dumps(payload, default=str))
        yield e.ToolCallEnd(tool_call_id=tcid)

    try:
        async for chunk in agent.astream(graph_input, config=config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue

            # --- Assistant text + widgets from EVERY node's update ---
            for node, update in chunk.items():
                if node == "__interrupt__" or not isinstance(update, dict):
                    continue
                for msg in update.get("messages") or []:
                    if not isinstance(msg, AIMessage):
                        continue
                    mid = getattr(msg, "id", None)
                    if mid and mid in seen_message_ids:
                        continue
                    if mid:
                        seen_message_ids.add(mid)
                    content = msg.content
                    if isinstance(content, str) and content.strip():
                        emitted_id = e.new_message_id()
                        emitted_text.append(content)
                        yield e.TextMessageStart(message_id=emitted_id)
                        yield e.TextMessageContent(message_id=emitted_id, delta=content)
                        yield e.TextMessageEnd(message_id=emitted_id)
                    # A flow pause (and every `show:` step) hangs its widget
                    # spec on the assistant message. That is the documented
                    # contract — honour it here so the spec reaches the stream.
                    kw = getattr(msg, "additional_kwargs", None) or {}
                    for shown in kw.get("aigenos_widgets") or []:
                        payload = to_widget_payload(shown)
                        if payload:
                            for ev in _widget_events(payload):
                                yield ev
                    payload = to_widget_payload(kw.get("aigenos_widget"))
                    if payload:
                        for ev in _widget_events(payload):
                            yield ev

            # --- Interrupt → the widget the client must render to continue ---
            interrupts = chunk.get("__interrupt__")
            if interrupts:
                raw = interrupts[0].value
                paused_on = raw if isinstance(raw, dict) else {"prompt": str(raw)}
                payload = widget_from_interrupt(raw)
                if payload:
                    for ev in _widget_events(payload):
                        yield ev
                elif isinstance(raw, dict):
                    log.info(
                        "[chat] interrupt on thread=%s carried no widget (keys=%s)",
                        thread_id, sorted(raw),
                    )
                # A pause with a prompt the nodes never emitted (a bare
                # render_widget interrupt, or a graph that keeps its question
                # out of `messages`) still owes the user that text. Compared by
                # CONTENT, not by message id: a hand-built AIMessage may have no
                # id, and "no ids seen" would then re-emit the prompt twice.
                prompt = paused_on.get("prompt")
                if isinstance(prompt, str) and prompt.strip() and prompt not in emitted_text:
                    mid = e.new_message_id()
                    yield e.TextMessageStart(message_id=mid)
                    yield e.TextMessageContent(message_id=mid, delta=prompt)
                    yield e.TextMessageEnd(message_id=mid)
                break

    except Exception as exc:  # noqa: BLE001
        errored = True
        log.exception("[chat] stream failed for thread=%s", thread_id)
        yield e.RunError(message=str(exc))

    if not errored:
        # RUN_FINISHED alone cannot tell "the flow is waiting for you" from
        # "the flow is over" — one snapshot before it can.
        snapshot = await asyncio.to_thread(
            _terminal_snapshot, agent, config, thread_id, paused_on
        )
        yield e.StateSnapshot(snapshot=snapshot)

    yield e.RunFinished(thread_id=thread_id, run_id=run_id)


def _terminal_snapshot(
    agent: Any, config: dict, thread_id: str, paused_on: dict[str, Any] | None
) -> dict[str, Any]:
    """What the client needs to know when the stream closes."""
    snapshot: dict[str, Any] = {
        "thread_id": thread_id,
        "status": "awaiting_input" if paused_on else "finished",
        "awaiting_field": (paused_on or {}).get("field"),
        "flow": (paused_on or {}).get("flow_id"),
        "done": not paused_on,
    }
    try:
        values = getattr(agent.get_state(config), "values", None)
        if isinstance(values, dict):
            snapshot["flow"] = values.get("flow_id", snapshot["flow"])
            snapshot["awaiting_field"] = values.get(
                "awaiting_field", snapshot["awaiting_field"]
            )
            if "done" in values and not paused_on:
                snapshot["done"] = bool(values.get("done"))
    except Exception:  # noqa: BLE001
        pass
    return snapshot


# Deprecated alias — the router was renamed in 0.3.1; remove in 0.4.
make_chatform_router = make_chat_router

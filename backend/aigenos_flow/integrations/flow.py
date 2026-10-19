"""Flow engine ↔ aigenos-flow bridge.

Connects the aigenos flow deterministic-flow engine to the widget rendering
layer. When a aigenos flow skill pauses to collect fields, this bridge
auto-generates a widget spec from FieldSpec metadata and surfaces it via
AG-UI / A2UI / MCP-UI — so the user fills a form instead of answering
free-text questions one by one.

Usage::

    from aigenos_flow.integrations.flow import make_flow_router

    app.include_router(
        make_flow_router(skills_dir="examples/studio_agent/skills"),
        prefix="/chat",
    )

Or lower-level — convert FieldSpecs to a widget spec in your own code::

    from aigenos_flow.integrations.flow import fields_to_widget

    spec = fields_to_widget(
        skill_name="book-meeting-room",
        field_specs={"room": room_spec, "date": date_spec},
        pending_fields=["room", "date"],
    )
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from pydantic import BaseModel

from ..agui import events as e
from ..agui.protocols import SUPPORTED, emit
from ..schemas.widget_spec import SpecNode, WidgetSpec

# NOTE: nothing at module level may import FastAPI (directly or through
# `.langgraph`). `create_agent` / `wrap_agent` import this module to build the
# widget for a paused field, and they must work on a bare
# `pip install aigenos-flow` — the server pieces live in the [server] extra.

log = logging.getLogger("aigenos_flow.flow")

# The Studio Gallery's templates, as paths under the repo's ``examples/``: skills
# written as steps, each with its tools in its own scripts.py, so it installs as
# one folder and cannot be refused for a missing dependency.
# tests/test_gallery.py installs every entry into an empty skills dir, and
# tests/test_gallery_templates.py runs each one as a conversation.
GALLERY_EXAMPLES = (
    "gallery/expense-report",
    "gallery/subscription-help",
    "gallery/table-booking",
)


# ---------------------------------------------------------------------------
# FieldSpec → WidgetSpec conversion
# ---------------------------------------------------------------------------

def _field_to_input(name: str, spec: Any, *, skill_name: str | None = None) -> SpecNode:
    """Convert a single aigenos flow FieldSpec into a widget primitive node.

    ``skill_name`` rides along as a ``skill`` prop because the renderer's
    inline validation posts ``{field, value, skill}`` to /flow/validate-field,
    and that endpoint cannot resolve a FieldSpec without the skill: without it
    the field is unknown, the answer is an unconditional ``{"valid": true}``,
    and a ``-5`` in a ``min: 0.01`` field shows a green tick.
    """
    ftype = getattr(spec, "type", "string")
    desc = getattr(spec, "description", None) or name.replace("_", " ").title()
    required = getattr(spec, "required", True)
    common: dict[str, Any] = {"name": name, "label": desc, "required": required}
    if skill_name:
        common["skill"] = skill_name

    if ftype == "enum":
        values = getattr(spec, "values", []) or []
        from ..fields import option_label

        options = [{"label": option_label(v), "value": v} for v in values]
        return SpecNode(type="select", props={**common, "options": options})

    if ftype == "boolean":
        return SpecNode(
            type="select",
            props={
                **common,
                "options": [
                    {"label": "Yes", "value": "true"},
                    {"label": "No", "value": "false"},
                ],
            },
        )

    input_type = "text"
    extra_props: dict[str, Any] = {}

    if ftype in ("number", "integer"):
        input_type = "number"
        if getattr(spec, "min", None) is not None:
            extra_props["min"] = spec.min
        if getattr(spec, "max", None) is not None:
            extra_props["max"] = spec.max
        if ftype == "integer":
            extra_props["step"] = 1
        else:
            # Without this the browser assumes step 1 COUNTED FROM `min`: with
            # `min: 0.01` the only values it accepts are 0.01, 1.01, 2.01 ...,
            # so 2600 and 49.99 are both "invalid" and the form will not submit.
            extra_props["step"] = "any"
    elif ftype == "string":
        if getattr(spec, "sensitive", False):
            # A secret is masked on screen, kept out of autofill, and marked so
            # a voice conversation refuses to take it by ear: `sensitive: true`
            # changes what the user sees, not only the audit log.
            input_type = "password"
            extra_props["sensitive"] = True
            extra_props["autoComplete"] = "one-time-code"
        elif "date" in name.lower():
            input_type = "date"
        elif "time" in name.lower():
            input_type = "time"
        pattern = getattr(spec, "pattern", None)
        if pattern:
            # The same shape the server enforces, so the browser can say so first.
            extra_props["pattern"] = pattern.lstrip("^").rstrip("$")
            if pattern.lstrip("^").startswith("[0-9]"):
                extra_props["inputMode"] = "numeric"

    return SpecNode(
        type="input",
        props={**common, "type": input_type, **extra_props},
    )


def fields_to_widget(
    *,
    skill_name: str,
    field_specs: dict[str, Any],
    pending_fields: list[str] | None = None,
    title: str | None = None,
) -> WidgetSpec:
    """Build a WidgetSpec from aigenos flow FieldSpec metadata.

    If ``pending_fields`` is given, only those fields appear in the form.
    Otherwise all fields are included.
    """
    names = pending_fields or list(field_specs.keys())
    children = [
        _field_to_input(n, field_specs[n], skill_name=skill_name)
        for n in names
        if n in field_specs
    ]

    form_node = SpecNode(
        type="form",
        props={"submitLabel": "Continue"},
        children=children,
    )
    card_title: str | None = title or skill_name.replace("_", " ").title()
    # A one-question widget printed that question twice: as the card title and
    # again as the field's own label directly beneath it. The label is the one
    # the input needs (it is the accessible name), so the title gives way.
    if len(children) == 1:
        only_label = str((children[0].props or {}).get("label") or "")
        if only_label.strip().casefold() == str(card_title).strip().casefold():
            card_title = None
    card_node = SpecNode(
        type="card",
        props={"title": card_title} if card_title else {},
        children=[form_node],
    )

    submit_properties = {}
    submit_required = []
    for n in names:
        if n not in field_specs:
            continue
        spec = field_specs[n]
        ftype = getattr(spec, "type", "string")
        if ftype == "enum":
            submit_properties[n] = {"type": "string", "enum": getattr(spec, "values", [])}
        elif ftype == "boolean":
            submit_properties[n] = {"type": "string", "enum": ["true", "false"]}
        elif ftype in ("number", "integer"):
            submit_properties[n] = {"type": ftype}
        else:
            submit_properties[n] = {"type": "string"}
        if getattr(spec, "required", True):
            submit_required.append(n)

    return WidgetSpec(
        id=f"flow_{skill_name}",
        version="1.0.0",
        description=f"Auto-generated form for {skill_name}",
        submit_schema={
            "type": "object",
            "properties": submit_properties,
            "required": submit_required,
        },
        spec=card_node,
    )


def resolve_widget_for_field(
    *,
    skill: Any,
    field_name: str,
    state_fields: dict[str, Any] | None = None,
    title: str | None = None,
    pending_widget: dict[str, Any] | None = None,
) -> WidgetSpec:
    """Pick a widget for a pending field.

    Resolution order: the widget the runtime already built for this pause
    (``state['pending_widget']`` — what a ``pick:`` or ``widget:`` step
    produces), then ``field.widget``, then ``skill.widget``, then
    auto-generated from the FieldSpec. Bad widget ids fall back to
    auto-generation with a log warning.

    Passing ``pending_widget`` matters: without it a ``pick:`` step's
    selectable grid never reaches the browser and the user is shown a plain
    text input for the first field the grid would have filled.
    """
    # Local import to avoid an engine -> backend top-level dep.
    from ..engine import bindings as _bindings
    from ..engine.loader import load_widgets

    state_fields = state_fields or {}

    if pending_widget:
        built = _widget_from_pending(pending_widget, skill=skill, state_fields=state_fields)
        if built is not None:
            return built

    candidate_ids: list[str] = []
    field_specs = getattr(skill, "fields", {}) or {}
    field_spec = field_specs.get(field_name)
    if field_spec is not None and getattr(field_spec, "widget", None):
        candidate_ids.append(field_spec.widget)
    if getattr(skill, "widget", None):
        candidate_ids.append(skill.widget)

    if candidate_ids:
        try:
            catalog = load_widgets()
        except Exception as exc:  # noqa: BLE001
            log.warning("[flow] widget catalog load failed: %s", exc)
            catalog = {}
        for wid in candidate_ids:
            if wid not in catalog:
                log.warning(
                    "[flow] widget %r (skill=%s field=%s) not in catalog, falling back",
                    wid, getattr(skill, "name", "?"), field_name,
                )
                continue
            chosen = catalog[wid]
            # Resolve {{field}} bindings against collected fields.
            try:
                resolved_spec_dict = _bindings.resolve(
                    chosen.spec.model_dump(exclude_none=True),
                    {"fields": state_fields, **state_fields},
                )
                resolved_spec = SpecNode.model_validate(resolved_spec_dict)
            except Exception as exc:  # noqa: BLE001
                log.warning("[flow] binding resolution failed for %r: %s", wid, exc)
                resolved_spec = chosen.spec
            return WidgetSpec(
                id=chosen.id,
                version=chosen.version,
                description=chosen.description,
                bindings=chosen.bindings,
                submit_schema=chosen.submit_schema,
                spec=resolved_spec,
            )

    return fields_to_widget(
        skill_name=skill.name,
        field_specs=field_specs,
        pending_fields=[field_name],
        title=title,
    )


def _widget_from_pending(
    pending: dict[str, Any], *, skill: Any, state_fields: dict[str, Any]
) -> WidgetSpec | None:
    """Turn ``state['pending_widget']`` into a WidgetSpec.

    The runtime stores ``{widget, data, fields, submit_schema, spec}``:
    ``spec`` is inline for an auto-generated ``pick:`` widget, and ``None`` for
    a catalog widget named by ``widget:`` (which we resolve here, binding both
    the step's ``data`` and the collected fields).
    """
    from ..engine import bindings as _bindings
    from ..engine.loader import load_widgets

    widget_id = str(pending.get("widget") or "")
    submit_schema = pending.get("submit_schema")
    data = pending.get("data") or {}
    inline = pending.get("spec")

    if isinstance(inline, dict) and inline:
        try:
            return WidgetSpec(
                id=widget_id or f"flow_{getattr(skill, 'name', 'skill')}",
                version="1.0.0",
                description=f"Auto-generated widget for {getattr(skill, 'name', '?')}",
                submit_schema=submit_schema,
                spec=SpecNode.model_validate(inline),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("[flow] inline pending widget spec invalid: %s", exc)
            return None

    if not widget_id:
        return None
    try:
        catalog = load_widgets()
    except Exception as exc:  # noqa: BLE001
        log.warning("[flow] widget catalog load failed: %s", exc)
        return None
    chosen = catalog.get(widget_id)
    if chosen is None:
        log.warning(
            "[flow] pending widget %r (skill=%s) not in catalog, falling back",
            widget_id, getattr(skill, "name", "?"),
        )
        return None
    env = {"fields": state_fields, **state_fields, **data}
    try:
        resolved = SpecNode.model_validate(
            _bindings.resolve(chosen.spec.model_dump(exclude_none=True), env)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("[flow] binding resolution failed for %r: %s", widget_id, exc)
        resolved = chosen.spec
    return WidgetSpec(
        id=chosen.id,
        version=chosen.version,
        description=chosen.description,
        bindings=chosen.bindings,
        submit_schema=submit_schema or chosen.submit_schema,
        spec=resolved,
    )


def confirmation_widget(*, skill_name: str, summary: str) -> WidgetSpec:
    """Build a confirmation card widget for flow completion."""
    return WidgetSpec(
        id=f"flow_{skill_name}_confirm",
        version="1.0.0",
        description=f"Confirmation for {skill_name}",
        spec=SpecNode(
            type="card",
            props={"title": "Confirm"},
            children=[
                # `value` is the text primitive's prop. Writing `content` left
                # the user confirming a $5000 transfer with an empty <p> where
                # the summary should be.
                SpecNode(type="text", props={"value": summary}),
                SpecNode(
                    type="form",
                    props={"submitLabel": "Confirm"},
                    children=[
                        SpecNode(
                            type="select",
                            props={
                                "name": "confirm",
                                "label": "Proceed?",
                                "options": [
                                    {"label": "Yes, confirm", "value": "true"},
                                    {"label": "No, cancel", "value": "false"},
                                ],
                                "required": True,
                            },
                        )
                    ],
                ),
            ],
        ),
        submit_schema={
            "type": "object",
            "properties": {"confirm": {"type": "string", "enum": ["true", "false"]}},
            "required": ["confirm"],
        },
    )


# ---------------------------------------------------------------------------
# FastAPI router — runs aigenos flow with widget rendering
# ---------------------------------------------------------------------------

class FlowChatRequest(BaseModel):
    thread_id: str | None = None
    message: str | None = None
    resume_payload: dict[str, Any] | None = None
    # Pin the run to one skill instead of letting the classifier choose. The
    # Canvas's "Test in Flow" uses this so you land in the flow you were just
    # editing rather than hoping the model routes there.
    skill: str | None = None


class SkillGenRequest(BaseModel):
    """Body for /flow/preview-skill and /flow/create-skill.

    Defined at module level (not inside make_flow_router) because
    FastAPI's body-vs-query inference only treats Pydantic models as JSON
    bodies when they're importable at module scope; closure-defined models
    fall through to query-parameter inference and produce a confusing 422.
    """

    description: str
    overwrite: bool = False
    # The one skill an overwrite may replace. The model picks the name again on
    # every request, so without this a retry could land on a different skill.
    replace: str | None = None


class SkillUpdateRequest(BaseModel):
    """Body for PUT /flow/skills/{name} — overwrite SKILL.md and/or handlers.py.

    Either or both fields may be omitted; only present keys are written. The
    skill is re-validated and re-registered on every update so the next chat
    turn picks up the changes without a server restart.
    """

    skill_md: str | None = None
    handlers_py: str | None = None


class SkillCreateRequest(BaseModel):
    """Body for POST /flow/skills — a new skill, written by hand or on the canvas."""

    name: str
    skill_md: str


class SkillEditRequest(BaseModel):
    """Body for POST /flow/skills/edit — a SKILL.md and a sentence saying what to change."""

    skill_md: str
    instruction: str
    name: str | None = None


class SkillCheckRequest(BaseModel):
    """Body for POST /flow/skills/check — a SKILL.md to load without saving.

    ``name`` is the skill being edited, when there is one: its folder supplies
    any Python the file refers to.
    """

    skill_md: str
    name: str | None = None


class ToolFileRequest(BaseModel):
    """Body for PUT /flow/tools/files — one Python file of the tool library.

    ``path`` is ``tools/<name>.py`` (or ``tools.py``), relative to the skills
    folder. Nothing outside those two places can be written.
    """

    path: str
    source: str


class ApprovalDecision(BaseModel):
    """Body for POST /flow/approvals/{id} — the other person's answer."""

    approved: bool
    by: str = ""
    note: str = ""


class ToolRunRequest(BaseModel):
    """Body for POST /flow/tools/{name}/run — try a tool from the Tools page.

    A tool that changes something is not run unless ``allow_write`` is true:
    the page asks first, because this is the real function.
    """

    inputs: dict[str, Any] = {}
    allow_write: bool = False


class FieldValidationRequest(BaseModel):
    field: str
    value: Any = None
    skill: str | None = None


class ScenariosRequest(BaseModel):
    """Body for PUT /flow/skills/{name}/scenarios — the whole list, replaced.

    Kept as loose dicts rather than a typed model: a scenario's `emits` are
    interpretation commands whose shapes belong to interpret.py, and they are
    validated when the scenario actually runs. Rejecting an in-progress
    scenario at save time would stop you writing one incrementally.
    """

    scenarios: list[dict[str, Any]] = []


def _build_checkpointer() -> Any:
    """Build a LangGraph checkpointer from FG_TRACKER_STORE_URL.

    Selection logic — env var prefix → backend:

      redis://...        → langgraph.checkpoint.redis.RedisSaver
      postgresql://...   → langgraph.checkpoint.postgres.PostgresSaver
      sqlite:///path     → langgraph.checkpoint.sqlite.SqliteSaver
      mongodb://...      → langgraph.checkpoint.mongodb.MongoDBSaver (community)
      <empty / unset>    → langgraph.checkpoint.memory.MemorySaver

    Each backend has its own extra-install requirement, e.g.::

        pip install langgraph-checkpoint-redis
        pip install langgraph-checkpoint-postgres

    On import failure we log a clear warning and fall back to MemorySaver
    so the server still boots — better degraded than dead.
    """
    import os

    url = os.getenv("FG_TRACKER_STORE_URL", "").strip()

    try:
        if url.startswith("redis://") or url.startswith("rediss://"):
            from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import-not-found]
            log.info("[flow] tracker store: redis @ %s", _redact(url))
            return RedisSaver.from_conn_string(url)

        if url.startswith("postgresql://") or url.startswith("postgres://"):
            from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore[import-not-found]
            log.info("[flow] tracker store: postgres @ %s", _redact(url))
            return PostgresSaver.from_conn_string(url)

        if url.startswith("sqlite:///"):
            from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore[import-not-found]
            path = url.removeprefix("sqlite:///")
            log.info("[flow] tracker store: sqlite @ %s", path)
            return SqliteSaver.from_conn_string(path)

        if url.startswith("mongodb://") or url.startswith("mongodb+srv://"):
            from langgraph.checkpoint.mongodb import MongoDBSaver  # type: ignore[import-not-found]
            log.info("[flow] tracker store: mongodb @ %s", _redact(url))
            return MongoDBSaver.from_conn_string(url)

    except ImportError as exc:
        log.warning(
            "[flow] FG_TRACKER_STORE_URL=%s requires an extra: %s. "
            "Falling back to in-memory MemorySaver — conversations will not "
            "survive restarts.",
            _redact(url),
            exc,
        )

    # Default / fallback
    from langgraph.checkpoint.memory import MemorySaver
    log.info("[flow] tracker store: in-memory (set FG_TRACKER_STORE_URL to persist)")
    return MemorySaver()


def _redact(url: str) -> str:
    """Hide credentials in a connection URL for logs."""
    # Crude but enough — replace `user:pass@` with `***@`
    import re
    return re.sub(r"://[^@/]+@", "://***@", url)


def make_flow_router(
    *,
    skills_dir: str | Path,
    llm: Any = None,
    mode: str = "flex",
    default_protocol: str = "ag-ui",
) -> Any:
    """Build a FastAPI router that serves aigenos flow skills with widget rendering.

    When the flow pauses for field collection, the router auto-generates a
    widget from FieldSpec metadata and streams it to the client. The user
    fills the form; their submission maps back to aigenos flow fields.

    Args:
        skills_dir: Path to the skills directory (contains SKILL.md folders).
        llm: A LangChain BaseChatModel. If None, auto-detects from env vars.
        mode: "flex" or "strict" interpretation mode.
        default_protocol: Wire protocol for SSE events.
    """
    from fastapi import APIRouter, Depends, HTTPException, Query
    from sse_starlette.sse import EventSourceResponse

    from .langgraph import request_path, resume_url_for
    from .write_access import require_write_access

    router = APIRouter()
    # On every route that writes a skill or returns its source — see
    # write_access.py for why the router enforces this itself.
    managed = [Depends(require_write_access)]
    # thread_id -> tool_call_id of the widget that thread is waiting on. A UI
    # correlation id only (losing it costs a resolved-tick, never a gate), kept
    # in insertion order so the oldest entry is the one dropped when it fills.
    widget_waits: dict[str, str] = {}

    # Build managed connector tools before any skill is loaded. The manager's
    # registry is swapped only after a complete candidate skill registry has
    # validated, so operation edits cannot poison a running agent.
    from aigenos_flow.connectors import ConnectorManager

    connector_manager = ConnectorManager()

    def _with_local_tools(base: Any) -> Any:
        """Managed connector tools + optional local tools.

        ``<skills_dir>/tools.py`` may define plain callables (REST clients, MCP
        wrappers, mocks) so declarative skills are runnable from the UI while
        prototyping, before the tools graduate to managed connectors. Same
        trust boundary as a skill's handlers.py: operator code, operator
        server. Re-imported on every registry build, so edits hot-reload with
        the skills. Export an explicit ``TOOLS = {name: fn}`` dict, or every
        public module-level callable is registered under its own name.
        Connector-managed names win on collision.
        """
        # The engine finds the library itself now (``<skills_dir>/tools.py`` and
        # ``<skills_dir>/tools/*.py``, see toolbox.load_library), for every entry
        # point and not only this server. Loading it here as well would import
        # each file twice.
        return base

    # Built on first request OR at startup (see startup hook below). Held in
    # closure state so the same agent + graph + host are reused across requests.
    _state: dict[str, Any] = {}

    def _swap_registry(agent: Any, fresh: Any) -> None:
        """Put a freshly loaded registry behind the running agent.

        The compiled graph holds ``agent.registry`` itself, so its contents are
        replaced rather than the object. The tool library goes with the skills:
        each reload re-imports the library files, and a tool list left pointing
        at the previous import would describe functions that no longer run.
        """
        agent.registry._skills = fresh._skills  # noqa: SLF001
        if getattr(fresh, "tools", None) is not None:
            agent.registry.tools = fresh.tools
        if hasattr(fresh, "errors"):
            agent.registry.errors = fresh.errors

    def _live_tools(agent: Any) -> Any:
        """Everything a skill here can name: connectors, library files, built-ins."""
        return getattr(agent.registry, "tools", None) or _with_local_tools(connector_manager.registry)

    def _get_agent():
        if "agent" not in _state:
            import time
            t0 = time.time()
            log.info("[flow] initializing agent (this can take a few minutes on first run while Python compiles bytecode)...")

            log.info("[flow]   importing aigenos flow...")
            from aigenos_flow import FlowAgent
            from aigenos_flow.hosts.langgraph_host import LangGraphHost
            log.info("[flow]   aigenos flow imported (%.1fs)", time.time() - t0)

            log.info("[flow]   building LLM...")
            t1 = time.time()
            resolved_llm = llm or _auto_llm()
            log.info("[flow]   LLM ready (%.1fs)", time.time() - t1)

            checkpointer = _build_checkpointer()
            host = LangGraphHost(llm=resolved_llm, checkpointer=checkpointer)

            log.info("[flow]   loading skills from %s ...", skills_dir)
            t2 = time.time()
            agent = FlowAgent.from_dir(
                skills_dir,
                host=host,
                mode=mode,
                tools=_with_local_tools(connector_manager.registry),
                # A server may legitimately start with nothing authored yet —
                # the Studio's first act is to create a skill. A folder whose
                # skills all FAILED to load still raises.
                allow_empty=True,
            )
            graph = agent.as_langgraph()
            log.info("[flow]   %d skills loaded (%.1fs)", len(agent.registry.all()), time.time() - t2)

            _state["agent"] = agent
            _state["graph"] = graph
            _state["host"] = host
            connector_manager.sync_dependencies(skills_dir)
            log.info("[flow] agent ready (total %.1fs)", time.time() - t0)
        return _state["agent"], _state["graph"], _state["host"]

    # Eager warmup at server startup — the slow langchain_ollama import (cold
    # .pyc compilation + Windows Defender scan) blocks for ~3 min on Windows.
    # If we don't pre-warm, the first request blocks the async event loop and
    # appears to "hang" because connections time out. Running it during the
    # startup event makes uvicorn delay "Application startup complete" until
    # everything is ready, which makes the slowness visible and predictable.
    @router.on_event("startup")
    async def _warmup_agent() -> None:
        import asyncio
        try:
            log.info("[flow] warming up agent at startup...")
            await asyncio.to_thread(_get_agent)
        except Exception:  # noqa: BLE001
            log.exception("[flow] startup warmup failed — endpoints will return 500 until fixed")

    @router.post("/")
    @router.post("")
    async def chat(
        req: FlowChatRequest,
        base_path: str = Depends(request_path),
        protocol: str = Query(default_protocol),
    ):
        import asyncio
        if protocol not in SUPPORTED:
            raise HTTPException(400, f"unsupported protocol: {protocol}")

        # to_thread keeps the event loop free if init still happening
        agent, graph, host = await asyncio.to_thread(_get_agent)
        thread_id = req.thread_id or uuid.uuid4().hex
        config = {"configurable": {"thread_id": thread_id}}

        if req.resume_payload is not None:
            from langgraph.types import Command
            # Pass the structured payload directly — wait_node detects dict
            # vs str and routes it to the LLM-free `pending_field_updates`
            # path, so widget submissions can't be lost to interpreter quirks.
            # Field names only. A submitted widget can carry a `sensitive` value
            # (a 2FA or approval code), and those never reach a log in clear.
            log.info("[flow] resume thread=%s fields=%s", thread_id, sorted(map(str, req.resume_payload)))
            graph_input: Any = Command(resume=dict(req.resume_payload))
        else:
            if not req.message:
                raise HTTPException(400, "message is required")
            log.info(
                "[flow] start  thread=%s skill=%s message=%r",
                thread_id, req.skill or "-", req.message,
            )
            # Only set pending_user_message — the engine appends to history
            # itself inside run_turn. Setting history here would overwrite
            # prior turns (FlowState has no reducer for the history key).
            graph_input = {"pending_user_message": req.message}
            if req.skill:
                if req.skill not in agent.registry:
                    raise HTTPException(404, f"skill {req.skill!r} not found")
                # Seeding flow_id is exactly what Scenario.run does: enter this
                # flow directly and skip first-turn classification.
                graph_input["flow_id"] = req.skill

        async def gen():
            try:
                async for sse_event in emit(
                    protocol,
                    _flow_event_stream(
                        graph,
                        graph_input,
                        config,
                        thread_id,
                        agent,
                        host=host,
                        widget_waits=widget_waits,
                        resume_payload=req.resume_payload,
                    ),
                    resume_url=resume_url_for(base_path, thread_id),
                ):
                    yield sse_event
            except Exception:
                log.exception("[flow] stream failed for thread=%s", thread_id)
                raise

        return EventSourceResponse(gen())

    @router.post("/{thread_id}/resume")
    async def resume(
        thread_id: str,
        payload: dict[str, Any],
        base_path: str = Depends(request_path),
        protocol: str = Query(default_protocol),
    ):
        return await chat(
            FlowChatRequest(thread_id=thread_id, resume_payload=payload),
            base_path,
            protocol=protocol,
        )

    @router.get("/llm-info")
    async def llm_info():
        """Report which LLM provider + model is currently active.

        Used by the frontend to render a "Powered by <provider>" badge so
        viewers of demo recordings can see whether they're watching Ollama
        (free, slower) or a hosted API (paid, fast). Honest signaling — which
        means reading the model OBJECT IN USE, not guessing from whatever API
        keys the process environment happens to hold. `make_flow_router(
        llm=ChatAnthropic(...))` on a box that also has OPENAI_API_KEY set used
        to report "openai"/"gpt-4o", and the env-sniffing branch also told any
        unauthenticated caller which provider keys the server has.
        """
        if llm is not None:
            return describe_llm(llm)
        # llm=None: the router auto-detects at first use, so before that the
        # only honest answer is what auto-detection WILL pick.
        if "host" in _state:
            return describe_llm(getattr(_state["host"], "llm", None))
        return auto_llm_info()

    @router.get("/skills")
    async def list_skills():
        import asyncio
        agent, _, _ = await asyncio.to_thread(_get_agent)
        skills = []
        for s in agent.registry.all():
            skills.append({
                "name": s.name,
                "description": s.description,
                "workflow": getattr(s, "workflow", True),
                "widget": getattr(s, "widget", None),
                "metadata": getattr(s, "metadata", {}) or {},
                # Written as `steps:`: each field's description is the question
                # the user is asked. In the long form it is guidance for the model.
                "short_form": bool(getattr(s, "short_form", False)),
                "fields": {
                    name: {
                        "type": spec.type,
                        "description": spec.description,
                        "required": spec.required,
                        "values": spec.values,
                        "widget": getattr(spec, "widget", None),
                        # Whose yes it is, when it is not the user's (`by: manager`).
                        "asked_of": getattr(spec, "asked_of", None),
                    }
                    for name, spec in (s.fields or {}).items()
                },
            })
        return {"skills": skills}

    @router.get("/tools")
    async def list_library_tools(skill: str | None = None):
        """The tool library: every function a skill in this agent can name.

        Each entry says what the tool takes (name, type, whether it is required)
        and whether it changes anything. That last flag comes from the tool, not
        from any skill: one that changes something cannot run without a confirm.
        """
        import asyncio
        from aigenos_flow.toolbox import list_tools

        agent, _, _ = await asyncio.to_thread(_get_agent)
        registry = getattr(agent.registry, "tools", None)
        tools = list_tools(registry) if registry is not None else []
        used: dict[str, list[str]] = {}
        for loaded in agent.registry.all():
            for tool_name in sorted(_names(getattr(loaded, "allowed_tools", None))):
                used.setdefault(tool_name, []).append(loaded.name)
        for tool in tools:
            tool["used_by"] = sorted(used.get(tool["name"], []))
        # `?skill=x` adds the tools that skill carries itself (its scripts.py):
        # only it can name them, so they are not part of the shared list.
        if skill and agent.registry.has(skill):
            listed = {t["name"] for t in tools}
            for own in getattr(agent.registry.get(skill), "own_tools", None) or []:
                if own["name"] not in listed:
                    tools.append({**own, "used_by": [skill], "private_to": skill})
        return {"tools": tools}

    def _names(line: str | None) -> set[str]:
        return {part for part in (line or "").replace(",", " ").split() if part}

    # ------------------------------------------------------------------
    # The tool library: the Python a skill names instead of containing
    # ------------------------------------------------------------------

    def _library_file(path: str) -> Path:
        """``tools.py`` or ``tools/<name>.py`` under the skills folder, and nothing else."""
        import re as _re
        from fastapi import HTTPException

        clean = (path or "").strip().replace("\\", "/")
        if clean != "tools.py" and not _re.fullmatch(r"tools/[A-Za-z_][A-Za-z0-9_]*\.py", clean):
            raise HTTPException(
                400,
                "a tool file is `tools/<name>.py` (letters, digits and underscores), or `tools.py`",
            )
        return Path(skills_dir) / clean

    def _library_listing() -> list[dict[str, Any]]:
        from aigenos_flow.toolbox import library_files

        root = Path(skills_dir)
        listed = [p for p in library_files(root)]
        helpers = sorted((root / "tools").glob("_*.py")) if (root / "tools").is_dir() else []
        out = []
        for file in [*listed, *helpers]:
            out.append({
                "path": file.relative_to(root).as_posix(),
                "source": file.read_text(encoding="utf-8"),
                # A file whose name starts with "_" holds helpers, not tools.
                "helper": file.name.startswith("_"),
            })
        return out

    @router.get("/tools/files", dependencies=managed)
    async def get_tool_files():
        """The library's Python files, with the tools each one defines."""
        import asyncio

        agent, _, _ = await asyncio.to_thread(_get_agent)
        files = _library_listing()
        registry = getattr(agent.registry, "tools", None)
        by_file: dict[str, list[str]] = {}
        if registry is not None:
            from aigenos_flow.toolbox import list_tools

            for tool in list_tools(registry):
                by_file.setdefault(tool.get("source") or "", []).append(tool["name"])
        for entry in files:
            entry["tools"] = sorted(by_file.get(entry["path"], []))
        return {"folder": "tools", "files": files}

    async def _change_library(target: Path, source: str | None, what: str) -> dict[str, Any]:
        """Write (or remove) one library file, then reload everything against it.

        Refused, and put back, when the file does not import or when a skill
        that loaded before no longer does: a tool is shared, so a change to one
        is checked against every skill that names it before anything goes live.
        """
        import asyncio
        from fastapi import HTTPException
        from aigenos_flow.skills import load_skills_dir
        from aigenos_flow.store import get_store

        agent, _, _ = await asyncio.to_thread(_get_agent)
        loaded_before = {s.name for s in agent.registry.all()}
        previous = target.read_text(encoding="utf-8") if target.exists() else None

        def restore() -> None:
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(previous, encoding="utf-8")

        target.parent.mkdir(parents=True, exist_ok=True)
        if source is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(source, encoding="utf-8")
        try:
            fresh = await asyncio.to_thread(
                load_skills_dir, Path(skills_dir),
                tool_registry=_with_local_tools(connector_manager.registry),
            )
        except Exception as exc:  # noqa: BLE001 - the file did not import
            restore()
            raise HTTPException(422, f"{what} rejected: {exc}")
        broken = sorted(loaded_before - {s.name for s in fresh.all()})
        if broken:
            # Errors are filed under the skill's folder, which is its name
            # unless someone renamed one of the two.
            errors = getattr(fresh, "errors", {}) or {}
            reasons = "; ".join(f"{folder}: {why}" for folder, why in sorted(errors.items()))
            restore()
            raise HTTPException(
                422,
                f"{what} rejected: it would break {', '.join(broken)}. {reasons}",
            )
        _swap_registry(agent, fresh)
        connector_manager.sync_dependencies(skills_dir)
        rel = target.relative_to(Path(skills_dir)).as_posix()
        if source is None:
            get_store().log("tool", rel, "deleted")
        else:
            get_store().record("tool", rel, {"source": source}, action="saved")
        log.info("[flow] tool library changed (%s %s); %d skills reloaded", what, rel, len(fresh))
        return {"path": rel, "files": _library_listing()}

    @router.put("/tools/files", dependencies=managed)
    async def put_tool_file(req: ToolFileRequest):
        """Save one Python file of the tool library and reload the skills.

        Every public function in it becomes a tool the steps can name. Mark the
        ones that only look things up with ``@readonly``; any other is a write,
        and no skill can run it without a ``confirm:`` above it.
        """
        from fastapi import HTTPException

        target = _library_file(req.path)
        try:
            compile(req.source, target.name, "exec")
        except SyntaxError as exc:
            where = f"line {exc.lineno}" + (f", column {exc.offset}" if exc.offset else "")
            raise HTTPException(422, f"{target.name} is not valid Python ({where}): {exc.msg}")
        return await _change_library(target, req.source, "saving the tool file")

    @router.delete("/tools/files", dependencies=managed)
    async def delete_tool_file(path: str):
        from fastapi import HTTPException

        target = _library_file(path)
        if not target.exists():
            raise HTTPException(404, f"{path} not found")
        return await _change_library(target, None, "removing the tool file")

    @router.post("/tools/{name}/run", dependencies=managed)
    async def run_tool(name: str, req: ToolRunRequest):
        """Call one tool with the given inputs, the way a flow would.

        For trying a function while writing it. It is the real function: one
        that changes something is refused unless ``allow_write`` is set.
        """
        import asyncio
        import uuid
        from fastapi import HTTPException
        from fastapi.encoders import jsonable_encoder
        from aigenos_flow.fields import FieldSpec, FieldValidationError, coerce_and_validate
        from aigenos_flow.toolbox import call_tool, describe_tool

        agent, _, _ = await asyncio.to_thread(_get_agent)
        registry = getattr(agent.registry, "tools", None)
        if registry is None or not registry.has(name):
            raise HTTPException(404, f"no tool named {name!r} in the library")
        info = describe_tool(name, registry.raw(name), registry.descriptor(name))
        if info.style == "builtin":
            raise HTTPException(409, f"`{name}` is built in and runs inside a flow; there is nothing to try here")
        if not info.readonly and not req.allow_write:
            raise HTTPException(
                409,
                f"`{name}` changes something (it is not marked @readonly). Running it here runs it for real.",
            )

        values: dict[str, Any] = {}
        if info.style == "named" or info.params:
            for param in info.params:
                given = req.inputs.get(param.name)
                if given is None or given == "":
                    if param.required:
                        return {"ok": False, "error": f"`{param.name}` is needed"}
                    continue
                if param.type in ("number", "integer", "boolean", "enum", "string"):
                    spec = FieldSpec(type=param.type, values=param.choices if param.type == "enum" else None)
                    try:
                        given = coerce_and_validate(spec, given)
                    except FieldValidationError as exc:
                        return {"ok": False, "error": f"`{param.name}`: {exc.reason}"}
                values[param.name] = given
        else:
            values = dict(req.inputs)
        context = {"skill_name": None, "action_name": "tried-in-studio", "flow_run_id": uuid.uuid4().hex}
        context["idempotency_key"] = context["flow_run_id"]
        try:
            result = await asyncio.to_thread(call_tool, info, registry.raw(name), registry.get(name), values, context)
        except Exception as exc:  # noqa: BLE001 - shown to the person writing the tool
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            shown = jsonable_encoder(result)
        except Exception:  # noqa: BLE001
            shown = repr(result)
        return {"ok": True, "result": shown, "changed_something": not info.readonly}

    # ------------------------------------------------------------------
    # A yes from someone else (`confirm:` with `by:`)
    # ------------------------------------------------------------------

    def _approval_store() -> Any:
        from aigenos_flow.approvals import store_for

        return store_for(_state.get("host"))

    @router.get("/approvals", dependencies=managed)
    async def list_approvals(status: str | None = None):
        """Requests filed by flows that are waiting on someone else's yes.

        Each says who is asked, the question, which tool a yes will run and
        with which inputs, and what the user has answered so far.
        """
        return {"approvals": [r.as_dict() for r in _approval_store().all(status)]}

    @router.get("/approvals/{request_id}/status")
    async def approval_status(request_id: str):
        """Only whether it has been answered: what the waiting chat looks in on.
        It carries no detail, so it needs no special access."""
        from fastapi import HTTPException

        found = _approval_store().get(request_id)
        if found is None:
            raise HTTPException(404, "no such request")
        return {"id": found.id, "status": found.status}

    @router.post("/approvals/{request_id}", dependencies=managed)
    async def decide_approval(request_id: str, req: ApprovalDecision):
        """Answer a request. The flow that filed it carries on at its next turn
        (the chat that is waiting looks in every few seconds and takes it).

        This is the only way such a yes reaches a flow: a value for it sent
        through the conversation, by the user or by the model, is refused.
        """
        from fastapi import HTTPException

        try:
            found = _approval_store().decide(request_id, req.approved, by=req.by, note=req.note)
        except KeyError:
            raise HTTPException(404, "no such request")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        log.info("[flow] approval %s %s by %s", found.id, found.status, found.decided_by or found.who)
        return found.as_dict()

    # ------------------------------------------------------------------
    # Check a skill without saving it
    # ------------------------------------------------------------------

    def _check_md(agent: Any, tools: Any, skill_md: str, name: str | None) -> dict[str, Any]:
        """Load a SKILL.md text exactly as a save would, in a scratch folder, and
        say what the loader said. Writes nothing to the skills folder."""
        import re as _re
        import shutil
        import tempfile
        from aigenos_flow.skills import load_skill, parse_skill_md

        try:
            front, _body = parse_skill_md(skill_md)
        except Exception as exc:  # noqa: BLE001 - not a SKILL.md yet
            return {"ok": False, "error": str(exc), "step": None}
        folder_name = str(getattr(front, "name", "") or name or "draft")
        if not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", folder_name):
            return {"ok": False, "step": None,
                    "error": "a skill name is lowercase words joined by hyphens, like `book-a-table`"}
        with tempfile.TemporaryDirectory(prefix="flow-check-") as tmp:
            # In a folder of its own name: the loader checks the two agree.
            target = Path(tmp) / folder_name
            target.mkdir()
            if name:
                try:
                    existing = _skill_folder(name)
                except Exception:  # noqa: BLE001 - an odd name is just "no folder"
                    existing = None
                if existing is not None and existing.is_dir():
                    for extra in ("handlers.py", "scripts.py"):
                        if (existing / extra).is_file():
                            shutil.copy2(existing / extra, target / extra)
                    if (existing / "scripts").is_dir():
                        shutil.copytree(existing / "scripts", target / "scripts")
            (target / "SKILL.md").write_text(skill_md, encoding="utf-8")
            try:
                skill = load_skill(target, tool_registry=tools)
            except Exception as exc:  # noqa: BLE001 - this IS the answer
                text = str(exc)
                found = _re.search(r"\bstep (\d+(?:\.\d+)*)", text)
                shown = _re.sub(r"^skill '[^']*': ", "", text)
                return {"ok": False, "error": shown, "step": found.group(1) if found else None}

        known = {s.name for s in agent.registry.all()} | {skill.name}
        missing = [t for t in (skill.links or []) if t not in known]
        if missing:
            return {
                "ok": False, "step": None,
                "error": f"`do: {missing[0]}` names a skill that does not exist yet. "
                         f"The skills here are: {', '.join(sorted(known - {skill.name})) or 'none'}.",
            }
        writes = sorted(_names(skill.allowed_tools) - _names(skill.readonly_tools))
        return {
            "ok": True,
            "name": skill.name,
            "short_form": bool(getattr(skill, "short_form", False)),
            "fields": {
                name: {
                    "type": spec.type, "values": spec.values, "question": spec.description,
                    "secret": bool(spec.sensitive), "optional": not spec.required,
                    # Only the user can give this one: what the model
                    # proposes for it is dropped.
                    "user_only": bool(spec.must_elicit or spec.sensitive)
                    or name in (skill.confirmation_fields or [])
                    or name in (skill.picked_fields or []),
                    "confirmation": name in (skill.confirmation_fields or []),
                }
                for name, spec in (skill.fields or {}).items()
            },
            "tools": sorted(_names(skill.allowed_tools)),
            "writes": writes,
            "links": list(skill.links or []),
        }

    @router.post("/skills/check", dependencies=managed)
    async def check_skill(req: SkillCheckRequest):
        """Load a SKILL.md text exactly as a save would, and write nothing.

        The canvas calls this while a flow is being edited, so a mistake is
        named on the step it is in before anyone presses Save. The answer is
        the loader's own: ``{"ok": true, ...}`` with what each question turned
        out to be, or ``{"ok": false, "error", "step"}``.
        """
        import asyncio

        agent, _, _ = await asyncio.to_thread(_get_agent)
        return await asyncio.to_thread(_check_md, agent, _live_tools(agent), req.skill_md, req.name)

    @router.post("/skills/edit", dependencies=managed)
    async def edit_skill(req: SkillEditRequest):
        """Change a skill's steps from a sentence, and say whether the result loads.

        The model writes the change; the loader decides whether it is valid.
        A proposal the loader refuses is sent back to the model once, with the
        loader's sentence. Nothing is written: the answer is a proposal
        (``skill_md``), what changed in plain words (``changed``), and the
        loader's verdict (``ok``, ``check``). ``cannot`` is set instead when
        the model says the change needs a tool or a skill that is not there.
        """
        import asyncio
        from fastapi import HTTPException
        from aigenos_flow.skill_editor import propose
        from aigenos_flow.toolbox import list_tools

        if not req.instruction.strip():
            raise HTTPException(400, "say what to change")
        agent, _, host = await asyncio.to_thread(_get_agent)
        tools = _live_tools(agent)
        described = list_tools(tools) if tools is not None else []
        if req.name and agent.registry.has(req.name):
            listed = {t["name"] for t in described}
            described += [
                own for own in (getattr(agent.registry.get(req.name), "own_tools", None) or [])
                if own["name"] not in listed
            ]
        try:
            llm = getattr(host, "llm", None) or _auto_llm()
            return await asyncio.to_thread(
                propose, req.skill_md, req.instruction, llm,
                check=lambda md: _check_md(agent, tools, md, req.name),
                tools=described,
                skills=sorted(sk.name for sk in agent.registry.all() if sk.name != req.name),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        except Exception as exc:  # noqa: BLE001 - the model, not the file
            log.exception("[flow] skills/edit failed")
            raise HTTPException(502, f"the model did not answer: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # In-place skill editing: read / write / delete / hot-reload
    # ------------------------------------------------------------------

    def _skill_folder(name: str) -> Path:
        """Resolve a skill's folder under skills_dir, with traversal protection."""
        from fastapi import HTTPException

        # Defense: name comes from URL path; refuse anything that escapes the
        # configured skills_dir via .. or absolute paths.
        if "/" in name or "\\" in name or ".." in name or name.startswith("."):
            raise HTTPException(400, f"invalid skill name: {name!r}")
        folder = (Path(skills_dir) / name).resolve()
        root = Path(skills_dir).resolve()
        if root not in folder.parents and folder != root:
            raise HTTPException(400, "skill path escapes skills_dir")
        return folder

    @router.get("/skills/{name}/files", dependencies=managed)
    async def get_skill_files(name: str):
        """Return the raw SKILL.md and handlers.py contents for in-UI editing."""
        from fastapi import HTTPException

        folder = _skill_folder(name)
        if not folder.exists():
            raise HTTPException(404, f"skill {name!r} not found")
        skill_md = folder / "SKILL.md"
        handlers = folder / "handlers.py"
        return {
            "name": name,
            "skill_md": skill_md.read_text(encoding="utf-8") if skill_md.exists() else "",
            "handlers_py": handlers.read_text(encoding="utf-8") if handlers.exists() else "",
        }

    @router.get("/skills/{name}/outline")
    async def get_skill_outline(name: str):
        """Canvas view of ANY skill's flow: declarative steps verbatim
        (editable), or a read-only AST outline of an imperative @flow."""
        from fastapi import HTTPException
        from aigenos_flow.outline import outline_folder

        folder = _skill_folder(name)
        if not folder.exists():
            raise HTTPException(404, f"skill {name!r} not found")
        return outline_folder(folder)

    @router.get("/skills/{name}/procedure")
    async def get_skill_procedure(name: str):
        """The skill read back as a procedure: its steps in plain words, every
        way through it, and what must be true before anything changes.

        Generated from the skill file and the tool library. No model is
        involved, so what is read here is what runs.
        """
        import asyncio
        from fastapi import HTTPException
        from aigenos_flow.procedure import for_skill

        agent, _, _ = await asyncio.to_thread(_get_agent)
        if not agent.registry.has(name):
            raise HTTPException(404, f"skill {name!r} not found")
        return await asyncio.to_thread(for_skill, agent.registry, name)

    def _skill_snapshot(folder: Path) -> dict[str, str]:
        """Full on-disk state of the editable skill files, for the revision store.

        Includes scenarios.json so restoring a revision brings back the tests
        that were passing at the time, not just the flow.
        """
        snap: dict[str, str] = {}
        for fname in ("SKILL.md", "handlers.py", "scripts.py", "scenarios.json"):
            p = folder / fname
            if p.exists():
                snap[fname] = p.read_text(encoding="utf-8")
        return snap

    async def _apply_skill_update(
        name: str,
        files: dict[str, str | None],
        label: str | None = None,
        action: str = "saved",
        create: bool = False,
    ) -> dict[str, Any]:
        """Write files, re-validate the WHOLE registry, hot-swap, record a revision.

        Only non-None files are written. On any validation failure the prior
        on-disk state is restored so the live registry never holds a
        half-broken skill. ``create=True`` lets a restore revive a deleted
        skill folder.
        """
        import asyncio
        import shutil
        from fastapi import HTTPException
        from aigenos_flow.skills import load_skills_dir
        from aigenos_flow.store import get_store

        folder = _skill_folder(name)
        created = not folder.exists()
        if created and not create:
            raise HTTPException(404, f"skill {name!r} not found")
        folder.mkdir(parents=True, exist_ok=True)

        # Snapshot for rollback on validation failure
        previous = {
            fname: (folder / fname).read_text(encoding="utf-8")
            if (folder / fname).exists() else None
            for fname in files
        }
        try:
            for fname, content in files.items():
                if content is not None:
                    (folder / fname).write_text(content, encoding="utf-8")

            # Validate the COMPLETE registry using the same connector catalog
            # the runtime uses. This catches a change that breaks another skill
            # before anything live is swapped.
            new_registry = await asyncio.to_thread(
                load_skills_dir,
                Path(skills_dir),
                tool_registry=_with_local_tools(connector_manager.registry),
            )
            if name not in new_registry:
                # The loader skipped it and filed the reason. That reason is the
                # answer; asking the registry for the skill would only say that
                # it is not there.
                reason = (getattr(new_registry, "errors", {}) or {}).get(folder.name, "")
                raise ValueError(reason.split(": ", 1)[-1] if reason else f"skill {name!r} did not load")
            skill = new_registry.get(name)

            agent, _, _ = await asyncio.to_thread(_get_agent)
            _swap_registry(agent, new_registry)
            connector_manager.sync_dependencies(skills_dir)
            log.info("[flow] hot-reloaded skill %r after edit", name)
        except Exception as exc:
            # Roll back to prior on-disk state so we don't poison the registry
            if created:
                shutil.rmtree(folder, ignore_errors=True)
            else:
                for fname, content in previous.items():
                    if files.get(fname) is None:
                        continue
                    target = folder / fname
                    if content is not None:
                        target.write_text(content, encoding="utf-8")
                    else:
                        target.unlink(missing_ok=True)
            log.exception("[flow] skill update failed for %r, rolled back", name)
            raise HTTPException(422, f"skill update rejected: {exc}")

        version = get_store().record(
            "skill", name, _skill_snapshot(folder), label=label, action=action,
        )
        return {
            "name": skill.name,
            "description": skill.description,
            "fields": list(skill.fields.keys()),
            "triggers": skill.triggers,
            "version": version,
        }

    @router.put("/skills/{name}", dependencies=managed)
    async def update_skill(name: str, req: SkillUpdateRequest):
        """Overwrite SKILL.md and/or handlers.py, re-validate, hot-reload.

        Either or both fields may be omitted; only provided files are written.
        Every successful save also records a revision in the workspace store,
        so the Studio's History panel can restore any prior version.
        """
        return await _apply_skill_update(
            name, {"SKILL.md": req.skill_md, "handlers.py": req.handlers_py},
        )

    @router.post("/skills", dependencies=managed)
    async def new_skill(req: SkillCreateRequest):
        """Create a skill from a SKILL.md, validated like any save.

        For a skill started on the canvas or typed by hand. (Describing one in
        a sentence is ``/create-skill``.) Refused when a skill of that name is
        already there: this never overwrites.
        """
        import re as _re
        from fastapi import HTTPException

        name = (req.name or "").strip()
        if not _re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            raise HTTPException(
                400, "a skill name is lowercase words joined by hyphens, like `book-a-table`"
            )
        if _skill_folder(name).exists():
            raise HTTPException(409, f"A skill named {name!r} already exists.")
        return await _apply_skill_update(
            name, {"SKILL.md": req.skill_md}, label="created", action="created", create=True,
        )

    @router.get("/skills/{name}/revisions")
    async def skill_revisions(name: str):
        from aigenos_flow.store import get_store

        return {"revisions": get_store().history("skill", name)}

    @router.post("/skills/{name}/restore/{version}", dependencies=managed)
    async def restore_skill(name: str, version: int):
        """Materialize a stored revision back to disk, re-validated like any save."""
        from fastapi import HTTPException
        from aigenos_flow.store import get_store

        rev = get_store().get("skill", name, version)
        if rev is None:
            raise HTTPException(404, "Unknown revision")
        body = rev["body"]
        return await _apply_skill_update(
            name,
            {f: body.get(f) for f in ("SKILL.md", "handlers.py", "scripts.py", "scenarios.json")},
            label=f"restored from v{version}",
            action="restored",
            create=True,
        )

    # ------------------------------------------------------------------
    # Scenarios — deterministic replay tests stored beside SKILL.md
    # ------------------------------------------------------------------

    @router.get("/skills/{name}/scenarios")
    async def get_scenarios(name: str):
        from fastapi import HTTPException
        from aigenos_flow import scenarios as scen

        folder = _skill_folder(name)
        if not folder.exists():
            raise HTTPException(404, f"skill {name!r} not found")
        return {"scenarios": scen.load(folder)}

    @router.put("/skills/{name}/scenarios", dependencies=managed)
    async def put_scenarios(name: str, req: ScenariosRequest):
        """Persist a skill's scenarios and snapshot them with the skill.

        Scenarios are not executable code, so they skip the registry
        revalidation a SKILL.md save goes through — but they still land in the
        revision store so a restore brings the tests back with the flow.
        """
        from fastapi import HTTPException
        from aigenos_flow import scenarios as scen
        from aigenos_flow.store import get_store

        folder = _skill_folder(name)
        if not folder.exists():
            raise HTTPException(404, f"skill {name!r} not found")
        scen.save(folder, req.scenarios)
        version = get_store().record(
            "skill", name, _skill_snapshot(folder), label="scenarios", action="saved",
        )
        return {"saved": len(req.scenarios), "version": version}

    @router.post("/skills/{name}/scenarios/run", dependencies=managed)
    async def run_scenarios(name: str):
        """Replay every scenario against a TestHost — no model, no live agent."""
        import asyncio
        from fastapi import HTTPException
        from aigenos_flow import scenarios as scen

        folder = _skill_folder(name)
        if not folder.exists():
            raise HTTPException(404, f"skill {name!r} not found")
        return await asyncio.to_thread(
            scen.run_all,
            Path(skills_dir),
            name,
            tools=_with_local_tools(connector_manager.registry),
            mode=mode,
        )

    @router.get("/skills/{name}/guarantees")
    async def skill_guarantees(name: str):
        """What the fail-closed loader proved about this skill on its last save."""
        from fastapi import HTTPException
        from aigenos_flow import guarantees

        folder = _skill_folder(name)
        md = folder / "SKILL.md"
        if not md.exists():
            raise HTTPException(404, f"skill {name!r} not found")
        text = md.read_text(encoding="utf-8")
        front = guarantees.frontmatter_of(text)
        if isinstance(front.get("steps"), list):
            # Written as steps: report on what the loader accepted (the tools
            # that change something, what each waits for), not on long-form
            # keys this file does not have.
            import asyncio
            from aigenos_flow import procedure

            agent, _, _ = await asyncio.to_thread(_get_agent)
            if agent.registry.has(name):
                page = await asyncio.to_thread(procedure.for_skill, agent.registry, name)
                return guarantees.analyze_steps(front, agent.registry.get(name), page)
        return guarantees.analyze(front)

    # ------------------------------------------------------------------
    # Gallery — example skills shipped in the repo, as starting points
    # ------------------------------------------------------------------

    def _gallery_templates() -> dict[str, Path]:
        """The example skills a checkout ships, by name.

        Found by walking up from this file to a folder holding ``examples/``,
        so a pip install (no repo around it) has none. The live skills dir is
        the destination, never a source: installing a skill over itself is
        meaningless.
        """
        live = Path(skills_dir).resolve()
        for parent in Path(__file__).resolve().parents:
            examples = parent / "examples"
            if not examples.is_dir():
                continue
            found = {}
            for rel in GALLERY_EXAMPLES:
                folder = (examples / rel).resolve()
                if (folder / "SKILL.md").exists() and folder.parent != live:
                    found[folder.name] = folder
            if found:
                return found
        return {}

    def _template_holds(folder: Path, fm: dict[str, Any], text: str, agent: Any) -> int:
        """How many of the Verify tab's guarantees hold for a template, before it is installed."""
        from aigenos_flow import guarantees, procedure
        from aigenos_flow.skills import load_skill, parse_skill_md
        from aigenos_flow.toolbox import list_tools

        if not isinstance(fm.get("steps"), list):
            return guarantees.analyze_markdown(text).get("holds", 0)
        try:
            library = _live_tools(agent)
            loaded = load_skill(folder, tool_registry=library)
            front, _ = parse_skill_md(text)
            tools = {t["name"]: t for t in (list_tools(library) if library is not None else [])}
            for own in loaded.own_tools or []:
                tools.setdefault(own["name"], own)
            page = procedure.describe(front, loaded, tools, set())
            return guarantees.analyze_steps(fm, loaded, page).get("holds", 0)
        except Exception:  # noqa: BLE001 - a template that does not load here would be refused on install too
            return 0

    @router.get("/gallery")
    async def gallery():
        from aigenos_flow import guarantees
        from aigenos_flow.outline import outline_folder

        templates = _gallery_templates()
        if not templates:
            return {"templates": []}

        import asyncio

        agent, _, _ = await asyncio.to_thread(_get_agent)
        installed = {s.name for s in agent.registry.all()}

        out = []
        for _, child in sorted(templates.items()):
            md = child / "SKILL.md"
            text = md.read_text(encoding="utf-8")
            outline = outline_folder(child)
            # Read the description off the parsed frontmatter — a line scan
            # returns nothing for the `description: >-` block scalars most of
            # these skills use.
            fm = guarantees.frontmatter_of(text)
            out.append({
                "name": child.name,
                "description": str(fm.get("description") or "").strip(),
                "mode": outline.get("mode"),
                "steps": len(outline.get("steps") or []),
                "guarantees": _template_holds(child, fm, text, agent),
                "installed": child.name in installed,
            })
        return {"templates": out}

    @router.post("/gallery/{name}/install", dependencies=managed)
    async def install_template(name: str):
        """Copy a gallery skill into the live skills dir, validated like any save."""
        from fastapi import HTTPException

        templates = _gallery_templates()
        if not templates:
            raise HTTPException(404, "no gallery available")
        # `name` is looked up in a fixed table, never joined onto a path.
        if "/" in name or "\\" in name or ".." in name or name.startswith("."):
            raise HTTPException(400, f"invalid template name: {name!r}")
        src = templates.get(name)
        if src is None:
            raise HTTPException(404, f"template {name!r} not found")

        target = _skill_folder(name)
        if target.exists():
            raise HTTPException(409, f"skill {name!r} already exists — delete it first")

        files = {
            fname: (src / fname).read_text(encoding="utf-8")
            for fname in ("SKILL.md", "handlers.py", "scripts.py", "scenarios.json")
            if (src / fname).exists()
        }
        return await _apply_skill_update(
            name, files, label="installed from gallery", action="created", create=True,
        )

    @router.delete("/skills/{name}", dependencies=managed)
    async def delete_skill(name: str):
        """Remove a skill from disk AND from the live registry."""
        import asyncio
        import shutil
        from fastapi import HTTPException

        folder = _skill_folder(name)
        if not folder.exists():
            raise HTTPException(404, f"skill {name!r} not found")

        agent, _, _ = await asyncio.to_thread(_get_agent)
        if name in agent.registry:
            agent.registry._skills.pop(name, None)  # noqa: SLF001

        await asyncio.to_thread(shutil.rmtree, folder)
        connector_manager.sync_dependencies(skills_dir)
        from aigenos_flow.store import get_store

        get_store().log("skill", name, "deleted")
        log.info("[flow] deleted skill %r", name)
        return {"deleted": name}

    @router.post("/demo/reset-bookings", dependencies=managed)
    async def reset_demo_bookings():
        """Reset the fake meeting-room bookings to the seeded state.

        Lets a demo or a test start from a known state. Silently no-ops if
        the studio agent's shared module isn't on the
        path (i.e. when running with a different skills_dir).
        """
        try:
            import sys as _sys
            shared_path = Path(skills_dir).parent / "shared"
            if shared_path.exists() and str(shared_path.parent) not in _sys.path:
                _sys.path.insert(0, str(shared_path.parent))
            from shared.rooms import reset_bookings  # type: ignore
            reset_bookings()
            return {"reset": True}
        except Exception as e:
            return {"reset": False, "reason": str(e)}

    @router.post("/reload", dependencies=managed)
    async def reload_skills():
        """Rescan ``skills_dir`` and rebuild the registry from disk.

        Useful when a user edits files directly in their IDE rather than via
        the in-UI editor. The next chat turn picks up the changes.
        """
        import asyncio
        from aigenos_flow.skills import load_skills_dir

        agent, _, _ = await asyncio.to_thread(_get_agent)
        new_registry = await asyncio.to_thread(
            load_skills_dir,
            Path(skills_dir),
            tool_registry=_with_local_tools(connector_manager.registry),
        )
        # Swap atomically — the agent holds a reference, so we patch its dict
        _swap_registry(agent, new_registry)
        connector_manager.sync_dependencies(skills_dir)
        log.info("[flow] reloaded %d skills from disk", len(new_registry))
        return {"reloaded": [s.name for s in new_registry.all()]}

    # ------------------------------------------------------------------
    # skill-creator: generate a SKILL.md + handlers.py from a description
    # ------------------------------------------------------------------

    @router.post("/create-skill", dependencies=managed)
    async def create_skill(req: SkillGenRequest):
        """Generate a new skill from a natural-language description.

        The LLM produces a structured spec; the engine renders SKILL.md +
        handlers.py, writes them under ``skills_dir/<name>/``, and hot-
        registers the new skill into the live registry. The next chat turn
        can immediately route to it — no server restart required.
        """
        import asyncio
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        from aigenos_flow.skill_generator import create_skill_in_dir, generate_skill_files

        if not req.description.strip():
            raise HTTPException(400, "description is required")

        agent, _, host = await asyncio.to_thread(_get_agent)

        files: dict[str, Any] = {}
        try:
            # First do a dry run so the user sees the generated files; the
            # actual write happens inside create_skill_in_dir below.
            llm = getattr(host, "llm", None) or _auto_llm()
            files = await asyncio.to_thread(
                generate_skill_files, req.description, llm
            )
            overwrite = req.overwrite and req.replace in (None, files["name"])
            skill = await asyncio.to_thread(
                create_skill_in_dir,
                description=req.description,
                llm=llm,
                skills_dir=Path(skills_dir),
                registry=agent.registry,
                overwrite=overwrite,
                # The live library (connectors, tools/ files, built-ins): a
                # generated skill names a tool from it.
                tool_registry=getattr(agent.registry, "tools", None)
                or _with_local_tools(connector_manager.registry),
                generated_files=files,
            )
            connector_manager.sync_dependencies(skills_dir)
        except FileExistsError:
            # Name the skill, not the folder: the path is the server's business.
            name = files.get("name", "")
            return JSONResponse(
                status_code=409,
                content={"detail": f"A skill named {name!r} already exists.", "name": name},
            )
        except ValueError as exc:
            raise HTTPException(422, f"skill generation failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("[flow] create-skill failed")
            raise HTTPException(500, f"unexpected error: {exc}")

        from aigenos_flow.store import get_store

        get_store().record(
            "skill", skill.name, _skill_snapshot(_skill_folder(skill.name)),
            label="created", action="created",
        )
        return {
            "name": skill.name,
            "description": skill.description,
            "skill_md": files["SKILL.md"],
            "handlers_py": files.get("handlers.py", ""),
            "fields": list(skill.fields.keys()),
            "triggers": skill.triggers,
        }

    @router.post("/preview-skill", dependencies=managed)
    async def preview_skill(req: SkillGenRequest):
        """Generate skill files for preview WITHOUT writing to disk or registering.

        Useful for a "review before save" UX in the frontend.
        """
        import asyncio
        from fastapi import HTTPException
        from aigenos_flow.skill_generator import generate_skill_files

        if not req.description.strip():
            raise HTTPException(400, "description is required")

        _, _, host = await asyncio.to_thread(_get_agent)
        try:
            llm = getattr(host, "llm", None) or _auto_llm()
            files = await asyncio.to_thread(
                generate_skill_files, req.description, llm
            )
        except ValueError as exc:
            raise HTTPException(422, f"skill generation failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            log.exception("[flow] preview-skill failed")
            raise HTTPException(500, f"unexpected error: {exc}")
        return files

    @router.post("/validate-field")
    async def validate_field(req: FieldValidationRequest):
        from aigenos_flow.fields import coerce_and_validate, FieldValidationError
        import asyncio
        agent, _, _ = await asyncio.to_thread(_get_agent)
        skill = None
        if req.skill and agent.registry.has(req.skill):
            skill = agent.registry.get(req.skill)
        if skill and req.field in skill.fields:
            spec = skill.fields[req.field]
            try:
                coerced = coerce_and_validate(spec, req.value)
            except FieldValidationError as e:
                return {"valid": False, "error": e.reason}
            # A rule of the flow that reads only this answer (`check: amount > 0`)
            # is known now, not only on submit: the tick must not disagree with
            # what the flow says one moment later.
            from aigenos_flow.skills import answer_problem

            problem = answer_problem(skill, req.field, req.value)
            if problem:
                return {"valid": False, "error": problem}
            return {"valid": True, "coerced": coerced}
        return {"valid": True, "coerced": req.value}

    # Connector management is mounted last to keep the existing chat/skill
    # route ordering stable. Every endpoint carries its own admin dependency.
    from aigenos_flow.connectors.api import make_connector_router
    from aigenos_flow.skills import load_skills_dir

    def _validate_connector_candidate(tool_registry: Any) -> Any:
        return load_skills_dir(
            Path(skills_dir), tool_registry=_with_local_tools(tool_registry)
        )

    def _apply_connector_candidate(candidate_skills: Any, tool_registry: Any) -> None:
        del tool_registry  # manager has already atomically installed it
        if "agent" in _state:
            # The compiled graph captured this registry object, so mutate its
            # contents instead of replacing agent.registry.
            _swap_registry(_state["agent"], candidate_skills)

    router.include_router(
        make_connector_router(
            connector_manager,
            validate_candidate=_validate_connector_candidate,
            apply_candidate=_apply_connector_candidate,
            skills_dir=str(skills_dir),
        )
    )

    return router


def _map_submission_to_fields(payload: dict[str, Any], agent: Any) -> str:
    """Convert a widget form submission into a natural-language string the
    aigenos flow interpreter can parse.

    E.g. ``{"room": "redwood", "date": "2026-05-25"}`` becomes
    ``"room: redwood, date: 2026-05-25"``.

    We return a string rather than injecting fields directly so the full
    interpretation pipeline runs — validation, type coercion, and field-
    history tracking all happen in the normal code path.
    """
    parts = [f"{k}: {v}" for k, v in payload.items()]
    return ", ".join(parts)


async def _flow_event_stream(
    graph: Any,
    graph_input: Any,
    config: dict,
    thread_id: str,
    agent: Any,
    *,
    host: Any = None,
    widget_waits: dict[str, str] | None = None,
    resume_payload: dict[str, Any] | None = None,
) -> AsyncIterator[e.AnyEvent]:
    """Stream AG-UI events from a aigenos flow LangGraph execution.

    RUN_STARTED → (TOOL_CALL_RESULT for the widget just submitted) →
    (TEXT_MESSAGE_* | TOOL_CALL_* per `show:`/pause widget) → STATE_SNAPSHOT →
    RUN_FINISHED.

    The two events after the text are what make the wire readable by a client:
    TOOL_CALL_RESULT settles the widget the user just filled in, and the
    terminal STATE_SNAPSHOT says whether the flow is waiting or finished. Before
    them, a run that completed the transfer and a run that declined it looked
    the same on the wire (``RUN_STARTED`` then ``RUN_FINISHED``) and the money
    had already moved.
    """
    import asyncio

    run_id = e.new_run_id()
    yield e.RunStarted(thread_id=thread_id, run_id=run_id)
    log.info("[flow] stream started run_id=%s", run_id)

    if resume_payload is not None and widget_waits is not None:
        prev_tcid = widget_waits.pop(thread_id, None)
        if prev_tcid:
            yield e.ToolCallResult(
                tool_call_id=prev_tcid,
                message_id=e.new_message_id(),
                content=json.dumps(resume_payload, default=str),
            )

    paused_on: dict[str, Any] | None = None
    errored = False
    drain_widgets = getattr(host, "drain_pending_widgets", None) if host else None

    def _emit_widget(payload: dict[str, Any], *, interactive: bool):
        tcid = e.new_tool_call_id()
        if interactive and widget_waits is not None:
            widget_waits[thread_id] = tcid
            while len(widget_waits) > _WIDGET_WAITS_MAX:
                widget_waits.pop(next(iter(widget_waits)))
        yield e.ToolCallStart(tool_call_id=tcid, tool_call_name="render_widget")
        yield e.ToolCallArgs(tool_call_id=tcid, delta=json.dumps(payload, default=str))
        yield e.ToolCallEnd(tool_call_id=tcid)

    def _shown_widgets() -> list[dict[str, Any]]:
        """`show:` / tool-result widgets the host collected this turn.

        These are bare SpecNode dicts from autowidget; the wire wants a
        WidgetPayload. Draining them here is what makes `show:` visible at all
        through this router — nothing else reads the host's widget queue.
        """
        if not callable(drain_widgets):
            return []
        out = []
        for spec in drain_widgets() or []:
            if isinstance(spec, dict) and spec.get("type"):
                out.append({
                    "widget_id": "autowidget",
                    "version": "1.0.0",
                    "spec": spec,
                    "submit_schema": None,
                })
        return out

    try:
        async for chunk in graph.astream(graph_input, config=config, stream_mode="updates"):
            log.info("[flow] chunk keys=%s", list(chunk.keys()))
            # --- Interrupt → the widget the client must render to continue ---
            if "__interrupt__" in chunk:
                interrupt_val = chunk["__interrupt__"][0].value
                log.info("[flow] interrupt value type=%s value=%r", type(interrupt_val).__name__, interrupt_val)
                if isinstance(interrupt_val, dict):
                    paused_on = interrupt_val
                    field_name = interrupt_val.get("field")
                    flow_id = interrupt_val.get("flow_id")
                    # NOTE: do NOT re-emit interrupt_val["messages"] here — the
                    # preceding turn chunk's `pending_assistant_messages` already
                    # streamed the same prompts. Emitting both produces duplicates
                    # in the UI.
                    log.info("[flow] interrupt field=%r flow_id=%r", field_name, flow_id)

                    # Resolve widget: the runtime's own pending_widget (a pick:
                    # grid or a catalog widget:) > per-field `widget:` >
                    # skill-level `widget:` > auto-gen from the FieldSpec.
                    if field_name and flow_id:
                        try:
                            skill = agent.registry.get(flow_id)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("[flow]   cannot find skill %r: %s", flow_id, exc)
                            skill = None
                        if skill and skill.fields:
                            # Pull collected fields from checkpoint for binding resolution.
                            collected_fields: dict[str, Any] = {}
                            pending_widget: dict[str, Any] | None = None
                            try:
                                snapshot = await asyncio.to_thread(
                                    graph.get_state, config
                                )
                                values = getattr(snapshot, "values", {}) or {}
                                if isinstance(values, dict):
                                    collected_fields = dict(values.get("fields", {}) or {})
                                    pw = values.get("pending_widget")
                                    if isinstance(pw, dict):
                                        pending_widget = pw
                            except Exception as exc:  # noqa: BLE001
                                log.warning(
                                    "[flow]   could not read state for binding resolution: %s",
                                    exc,
                                )
                            widget = resolve_widget_for_field(
                                skill=skill,
                                field_name=field_name,
                                state_fields=collected_fields,
                                title=interrupt_val.get("prompt"),
                                pending_widget=pending_widget,
                            )
                            # Match the WidgetPayload shape exactly so the
                            # existing useFlowChat hook + <Widget> renderer
                            # work without modification.
                            widget_payload = {
                                "widget_id": widget.id,
                                "version": widget.version,
                                "spec": widget.spec.model_dump(exclude_none=True),
                                "submit_schema": widget.submit_schema,
                            }
                            log.info("[flow]   emit widget skill=%s field=%s", skill.name, field_name)
                            for ev in _emit_widget(widget_payload, interactive=True):
                                yield ev
                        else:
                            log.warning("[flow]   skill=%r has no fields, no widget emitted", skill.name if skill else flow_id)
                    else:
                        log.warning("[flow]   missing field=%r or flow_id=%r — no widget emitted (prompt was already streamed in turn chunk)", field_name, flow_id)
                else:
                    paused_on = {"prompt": str(interrupt_val)}

                yield e.StateSnapshot(
                    snapshot=_flow_snapshot(thread_id, paused_on, values=None)
                )
                yield e.RunFinished(thread_id=thread_id, run_id=run_id)
                return

            # --- Turn node output → assistant text + `show:` widgets ---
            turn_chunk = chunk.get("turn")
            if turn_chunk:
                pending_msgs = turn_chunk.get("pending_assistant_messages", []) or []
                log.info("[flow] turn chunk pending_messages=%d done=%s", len(pending_msgs), turn_chunk.get("done"))
                for msg_text in pending_msgs:
                    if isinstance(msg_text, str) and msg_text.strip():
                        log.info("[flow]   emit turn text: %r", msg_text[:80])
                        mid = e.new_message_id()
                        yield e.TextMessageStart(message_id=mid)
                        yield e.TextMessageContent(message_id=mid, delta=msg_text)
                        yield e.TextMessageEnd(message_id=mid)
                for shown in _shown_widgets():
                    for ev in _emit_widget(shown, interactive=False):
                        yield ev
                continue

    except Exception as exc:
        errored = True
        log.exception("[flow] stream failed for thread=%s", thread_id)
        yield e.RunError(message=str(exc))

    if not errored:
        values: dict[str, Any] | None = None
        try:
            snap = await asyncio.to_thread(graph.get_state, config)
            got = getattr(snap, "values", None)
            if isinstance(got, dict):
                values = got
        except Exception:  # noqa: BLE001
            pass
        yield e.StateSnapshot(snapshot=_flow_snapshot(thread_id, paused_on, values=values))

    yield e.RunFinished(thread_id=thread_id, run_id=run_id)


_WIDGET_WAITS_MAX = 512


def _flow_snapshot(
    thread_id: str,
    paused_on: dict[str, Any] | None,
    *,
    values: dict[str, Any] | None,
) -> dict[str, Any]:
    """The terminal state a client needs to tell paused from finished."""
    values = values or {}
    return {
        "thread_id": thread_id,
        "status": "awaiting_input" if paused_on else "finished",
        "awaiting_field": (paused_on or {}).get("field") or values.get("awaiting_field"),
        "flow": (paused_on or {}).get("flow_id") or values.get("flow_id"),
        "done": False if paused_on else bool(values.get("done", True)),
    }


def load_env_files() -> None:
    """Best-effort ``.env`` loading with NO extra dependency.

    The auto-detect below reads real environment variables, but most users put
    their key in a ``.env`` at the repo root — and then get silently routed to
    Ollama because nothing loaded it. Walk CWD upward, parse simple KEY=VALUE
    lines, and ``setdefault`` into the environment (a real env var always
    wins; values are never logged).
    """
    import os
    from pathlib import Path

    seen: set[Path] = set()
    for base in [Path.cwd(), *Path.cwd().parents[:4]]:
        p = (base / ".env").resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip("'\"")
                if k and v:
                    os.environ.setdefault(k, v)
        except OSError:
            continue


def _fg_model(default: str, *, hosted: bool) -> str:
    """FG_MODEL override, guarded against a stale cross-provider value.

    A leftover ``FG_MODEL=qwen3:8b`` from an Ollama session must not be sent
    to a hosted provider as a model id (instant 404). Ollama tags contain a
    ``:``; hosted ids don't — if the override obviously belongs to the other
    world, fall back to the provider default.
    """
    import os

    override = os.getenv("FG_MODEL")
    if not override:
        return default
    looks_local = ":" in override
    if hosted and looks_local:
        return default
    if not hosted and not looks_local:
        return default
    return override


#: LangChain chat-model class name -> the provider label the UI badges use.
_PROVIDER_BY_CLASS = {
    "ChatAnthropic": ("anthropic", True),
    "AnthropicChat": ("anthropic", True),
    "ChatOpenAI": ("openai", True),
    "AzureChatOpenAI": ("openai", True),
    "ChatGoogleGenerativeAI": ("gemini", True),
    "ChatVertexAI": ("gemini", True),
    "ChatOllama": ("ollama", False),
    "OllamaLLM": ("ollama", False),
    "Ollama": ("ollama", False),
    "ChatBedrock": ("bedrock", True),
    "ChatMistralAI": ("mistral", True),
    "ChatGroq": ("groq", True),
}


def describe_llm(llm: Any) -> dict[str, Any]:
    """Provider + model of the model object actually in use.

    A model object the caller supplied is the ground truth; env vars are not.
    An unrecognised class reports ``provider: "custom"`` with its class name,
    which is the honest answer for a hand-rolled or fake model.
    """
    if llm is None:
        return {"provider": "none", "model": None, "hosted": False, "detected": False}
    cls = type(llm).__name__
    provider, hosted = _PROVIDER_BY_CLASS.get(cls, ("custom", False))
    model = (
        getattr(llm, "model", None)
        or getattr(llm, "model_name", None)
        or getattr(llm, "model_id", None)
        or getattr(llm, "deployment_name", None)
    )
    return {
        "provider": provider,
        "model": str(model) if model else None,
        "hosted": hosted,
        "detected": False,
        "class": cls,
    }


def auto_llm_info() -> dict[str, Any]:
    """What ``_auto_llm()`` WOULD pick, without importing a provider package.

    Only reached when the caller passed no ``llm=`` and the agent has not been
    built yet. ``detected: True`` marks it as a prediction, not an observation.
    """
    import os

    load_env_files()
    if os.getenv("ANTHROPIC_API_KEY"):
        return {"provider": "anthropic", "model": _fg_model("claude-sonnet-5-5", hosted=True),
                "hosted": True, "detected": True}
    if os.getenv("OPENAI_API_KEY"):
        return {"provider": "openai", "model": _fg_model("gpt-4o", hosted=True),
                "hosted": True, "detected": True}
    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        return {"provider": "gemini", "model": _fg_model("gemini-3.8-flash", hosted=True),
                "hosted": True, "detected": True}
    return {"provider": "ollama", "model": _fg_model("llama3.1:8b", hosted=False),
            "hosted": False, "detected": True}


def _auto_llm() -> Any:
    """Auto-detect an LLM: Anthropic > OpenAI > Gemini > local Ollama."""
    import os

    load_env_files()

    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=_fg_model("claude-sonnet-5-5", hosted=True),
            temperature=0,
        )

    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        base_url = os.getenv("OPENAI_BASE_URL")
        kwargs = {
            # A custom base URL is an OpenAI-compatible server whose model ids
            # may look like Ollama tags, so only the hosted API gets the guard.
            "model": os.getenv("FG_MODEL", "gpt-4o") if base_url else _fg_model("gpt-4o", hosted=True),
            "temperature": 0,
        }
        if base_url:
            kwargs["base_url"] = base_url
        return ChatOpenAI(**kwargs)

    if os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        os.environ["GOOGLE_API_KEY"] = api_key  # type: ignore[assignment]
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=_fg_model("gemini-3.8-flash", hosted=True),
            temperature=0,
        )

    from langchain_ollama import ChatOllama
    return ChatOllama(
        model=_fg_model("llama3.1:8b", hosted=False),
        temperature=0,
    )

"""FastAPI entrypoint — AG-UI streaming + CRUD + multi-protocol output.

Mounts three routers:
  - YAML-workflow CRUD + run/stream/submit endpoints (default surface)
  - /flow: deterministic flow engine with auto-generated widgets
    (mounted if the `aigenos flow` package is installed and FG_SKILLS_DIR is set)

SECURITY — read this before you expose the port
-----------------------------------------------
Part of this surface writes Python to disk and imports it in-process
(``PUT /flow/skills/{name}``, ``POST /flow/create-skill``, ``POST /flow/reload``,
``POST /flow/gallery/{name}/install``, ``POST /flow/skills/{name}/restore/{v}``).
That is remote code execution by design — it is how the Studio edits a skill —
so it is **reachable from your own machine and nowhere else** unless you say
otherwise:

* ``FG_SKILL_WRITES`` unset (the default) → ``local``: allowed from loopback
  clients only, and only when the request carries no browser ``Origin`` or an
  allowed one. This is what makes the Studio work on a dev box out of the box.
  A public bind is refused while this mode is in force (see ``check_bind``).
* ``FG_SKILL_WRITES=token`` (implied when ``FG_ADMIN_TOKEN`` is set) → writes
  require ``Authorization: Bearer <token>`` or ``X-Admin-Token: <token>``. This
  is the mode for a server anyone else can reach.
* ``FG_SKILL_WRITES=off`` → every call on that surface answers 403 and nothing
  can change server-side code. Use this to serve chat publicly with no
  authoring surface at all.
* ``FG_SKILL_WRITES=open`` → no check at all. Logged as a warning on every
  startup and on every request. Do not use this on a reachable interface.

CORS defaults to the local dev origins (``FG_CORS_ORIGINS`` to change it), never
``*``, so a random web page in the operator's browser cannot reach the surface.
``python -m aigenos_flow.main`` binds 127.0.0.1 and refuses a public bind while
writes are ungated.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import re
from pathlib import Path
from typing import Any

# Make aigenos flow warmup + per-turn logs visible in the uvicorn console
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import __version__
from .agui.protocols import SUPPORTED, emit
from .ai import generator
from .ai.llm import describe_import_error
from .engine import loader, runtime
from .schemas.widget_spec import WidgetSpec
from .schemas.workflow import Workflow
from .store import get_store

log = logging.getLogger("aigenos_flow.main")


# ---------------------------------------------------------------------------
# Identifier hygiene — every path segment that becomes a filename or a store
# key is checked HERE, at the boundary, not in whatever writer it reaches.
# ---------------------------------------------------------------------------

# Must START and END with an alphanumeric: a trailing dot or space is stripped
# by Windows path normalization (``greet.`` and ``greet`` are the same file), and
# a leading dot hides the file. 1-64 characters.
_SAFE_REF = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$")
_RESERVED_BASENAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CLOCK$"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def check_ref(kind: str, value: str) -> str:
    """Reject anything that could escape a directory or alias another file.

    The pattern is a strict allowlist anchored at both ends, so ``..``, path
    separators, URL-encoded separators (already decoded by the time we see
    them), NUL bytes, leading dots, trailing dots/spaces and over-long names
    cannot get through. Windows device names are refused too: ``CON.json`` is
    not a file you want a request to create.
    """
    if not _SAFE_REF.fullmatch(value or ""):
        raise HTTPException(
            400,
            f"invalid {kind} {value[:64]!r}: must start and end with a letter or digit "
            "and contain only letters, digits, '.', '_' or '-' (max 64 characters)",
        )
    if value in {".", ".."} or ".." in value:
        raise HTTPException(400, f"invalid {kind} {value!r}")
    if value.split(".")[0].upper() in _RESERVED_BASENAMES:
        raise HTTPException(400, f"invalid {kind} {value!r}: reserved device name")
    return value


# ---------------------------------------------------------------------------
# Security posture
# ---------------------------------------------------------------------------

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# The policy itself lives in write_guard so the flow router can enforce it too
# (an app that mounts make_flow_router itself never runs this module).
from .write_guard import (  # noqa: E402
    cors_origins,
    refusal as _write_refusal,
    skill_writes_mode,
)


def is_managed_write(method: str, path: str) -> bool:
    """Is this request on the surface that writes code/files or leaks source?

    Deliberately includes ``GET /flow/skills/{name}/files`` (it returns the raw
    handlers.py source) and the revision store (its bodies are that same
    source), and deliberately EXCLUDES ``/flow/connectors/**``, which carries
    its own admin dependency.
    """
    if path.startswith("/flow/connectors"):
        return False
    writing = method.upper() in _WRITE_METHODS
    if path in {"/flow/reload", "/flow/create-skill", "/flow/preview-skill",
                "/flow/demo/reset-bookings", "/widgets/reload", "/ai/generate-widget"}:
        return True
    if path.startswith("/flow/skills"):
        return writing or path.endswith("/files")
    if path.startswith("/flow/gallery/") and path.endswith("/install"):
        return True
    if path.startswith("/store/revisions"):
        return True
    if writing and (path.startswith("/widgets") or path.startswith("/workflows")):
        return True
    return False


def _deny(detail: str, status: int) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def authorize_managed_write(request: Request) -> JSONResponse | None:
    """``None`` when the request may proceed, else the response to return."""
    refused = _write_refusal(
        request.method,
        request.url.path,
        request.client.host if request.client else None,
        request.headers,
    )
    return None if refused is None else _deny(refused[1], refused[0])


app = FastAPI(
    title="aigenos/flow",
    version=__version__,
    summary="Deterministic conversational flows with auto-generated widgets.",
)


def _managed_name(path: str) -> str | None:
    """The skill/template name a ``/flow`` management path addresses, if any."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "flow" and parts[1] in {"skills", "gallery"}:
        return parts[2]
    return None


@app.middleware("http")
async def _guard_managed_surface(request: Request, call_next):
    """Gate the code-writing surface, and check its names, before any route.

    The name check is here rather than only inside the router because a name
    from the URL becomes a directory under the skills dir. Windows normalizes a
    trailing space or dot away, so without this ``/flow/skills/greet%20/files``
    would read the ``greet`` skill while recording the revision under a
    different key.
    """
    path = request.url.path
    name = _managed_name(path)
    if name is not None:
        try:
            check_ref("skill name", name)
        except HTTPException as exc:
            return _deny(str(exc.detail), exc.status_code)
    if is_managed_write(request.method, path):
        refusal = authorize_managed_write(request)
        if refusal is not None:
            return refusal
    return await call_next(request)


@app.exception_handler(ImportError)
async def _missing_llm_provider(request: Request, exc: ImportError):
    """A missing optional provider is a 503 naming the extra, never a bare 500."""
    described = describe_import_error(exc)
    if described is None:
        raise exc
    log.error("%s %s: %s", request.method, request.url.path, described)
    return JSONResponse(
        {
            "detail": str(described),
            "provider": described.provider,
            "extra": described.extra,
            "install": f'pip install "aigenos-flow[{described.extra}]"',
        },
        status_code=503,
    )


# CORS is added last so it is the OUTERMOST middleware: it answers preflights
# before the write guard runs, and it decorates the guard's 403 too.
_cors = cors_origins()
if "*" in _cors:
    log.warning(
        "FG_CORS_ORIGINS=* — any web page in a browser that can reach this server may "
        "call it. The skill-management surface is code-executing; keep FG_SKILL_WRITES "
        "gated."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _announce_posture() -> None:
    mode = skill_writes_mode()
    log.info(
        "aigenos/flow %s — CORS origins: %s | skill-management writes: %s",
        __version__, ", ".join(cors_origins()), mode,
    )
    if mode == "open":
        log.warning(
            "==================================================================\n"
            "  FG_SKILL_WRITES=open: PUT /flow/skills/{name}, POST /flow/create-skill\n"
            "  and POST /flow/reload accept UNAUTHENTICATED requests. Anyone who can\n"
            "  reach this port can write Python that this process then imports and\n"
            "  runs. Only do this on a loopback-only development machine.\n"
            "=================================================================="
        )
    elif mode == "local":
        log.info(
            "skill-management surface: loopback only (the default). The Studio can "
            "author skills from this machine; nothing off-box can. These endpoints "
            "execute operator Python, so any process on this machine can too — set "
            "FG_SKILL_WRITES=off to close them, or FG_SKILL_WRITES=token with "
            "FG_ADMIN_TOKEN before exposing this port."
        )


# ---------- Optional: mount aigenos flow engine router ----------
# When FG_SKILLS_DIR is set (or a default skills dir exists in the monorepo),
# expose the deterministic aigenos flow engine at /flow. The frontend Flow
# page consumes this endpoint.
def _resolve_skills_dir() -> Path | None:
    env_dir = os.getenv("FG_SKILLS_DIR")
    if env_dir:
        p = Path(env_dir)
        return p if p.exists() else None

    # Walk up to find examples/studio_agent/skills relative to this file
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "examples" / "studio_agent" / "skills"
        if candidate.exists():
            return candidate

    # A `pip install` has no monorepo to walk, so fall back to a writable
    # skills folder under the data dir and CREATE it. Returning None here used
    # to leave /flow unmounted with nothing logged: every chat request and
    # every Studio call answered 404, which reads as "my request is wrong"
    # rather than "the server mounted nothing". An empty folder is a working
    # server the Studio can author its first skill into.
    from .engine.loader import DATA_DIR

    fallback = DATA_DIR / "skills"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error(
            "/flow is NOT mounted — no skills directory and %s is not writable (%s). "
            "Set FG_SKILLS_DIR=/path/to/skills (it may be empty) and restart.",
            fallback, exc,
        )
        return None
    log.info(
        "no FG_SKILLS_DIR set — serving skills from %s (set FG_SKILLS_DIR to use "
        "your own folder)", fallback,
    )
    return fallback


_skills_dir = _resolve_skills_dir()
if _skills_dir is not None:
    try:
        from .integrations.flow import make_flow_router

        app.include_router(
            make_flow_router(
                skills_dir=_skills_dir,
                mode=os.getenv("FG_MODE", "flex"),
            ),
            prefix="/flow",
        )
    except ImportError as exc:
        # No silent downgrade: an unmounted /flow means every chat route 404s,
        # which reads as "the server is fine, my request is wrong". Say what is
        # missing. If the operator pointed FG_SKILLS_DIR at a folder on purpose,
        # refuse to start half-built.
        described = describe_import_error(exc)
        detail = (
            str(described) if described
            else f"{exc} (install: pip install 'aigenos-flow[server]')"
        )
        log.error("/flow is NOT mounted — %s", detail)
        if os.getenv("FG_SKILLS_DIR"):
            raise RuntimeError(f"cannot serve FG_SKILLS_DIR={_skills_dir}: {detail}") from exc
elif os.getenv("FG_SKILLS_DIR"):
    raise RuntimeError(
        f"FG_SKILLS_DIR={os.getenv('FG_SKILLS_DIR')!r} does not exist — /flow would be "
        "silently unmounted and every chat request would answer 404."
    )


class StartRequest(BaseModel):
    workflow_id: str


class StartResponse(BaseModel):
    thread_id: str


class SubmitRequest(BaseModel):
    thread_id: str
    payload: dict[str, Any]


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "protocols": list(SUPPORTED)}


# ---------- Workflow listing / CRUD ----------

@app.get("/workflows")
def list_workflows() -> dict[str, Any]:
    return {
        "workflows": [
            {"id": wf.id, "description": wf.description}
            for wf in runtime.WORKFLOWS.values()
        ]
    }


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> dict[str, Any]:
    check_ref("workflow id", workflow_id)
    wf = runtime.WORKFLOWS.get(workflow_id)
    if not wf:
        raise HTTPException(404, "Unknown workflow")
    return wf.model_dump(exclude_none=True)


@app.put("/workflows/{workflow_id}")
def upsert_workflow(workflow_id: str, body: Workflow) -> dict[str, Any]:
    check_ref("workflow id", workflow_id)
    if body.id != workflow_id:
        raise HTTPException(400, "id mismatch")
    loader.save_workflow(body)
    runtime.reload_assets()
    version = get_store().record("workflow", workflow_id, body.model_dump(exclude_none=True))
    return {"status": "saved", "version": version}


@app.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str) -> dict[str, str]:
    check_ref("workflow id", workflow_id)
    if not loader.delete_workflow(workflow_id):
        raise HTTPException(404, "Unknown workflow")
    runtime.reload_assets()
    get_store().log("workflow", workflow_id, "deleted")
    return {"status": "deleted"}


# ---------- Widget listing / CRUD ----------

@app.get("/widgets")
def list_widgets() -> dict[str, Any]:
    return {
        "widgets": [
            {"id": w.id, "version": w.version, "description": w.description}
            for w in runtime.WIDGETS.values()
        ]
    }


@app.get("/widgets/{widget_id}")
def get_widget(widget_id: str) -> dict[str, Any]:
    check_ref("widget id", widget_id)
    w = runtime.WIDGETS.get(widget_id)
    if not w:
        raise HTTPException(404, "Unknown widget")
    return w.model_dump(exclude_none=True)


@app.put("/widgets/{widget_id}")
def upsert_widget(widget_id: str, body: WidgetSpec) -> dict[str, Any]:
    check_ref("widget id", widget_id)
    if body.id != widget_id:
        raise HTTPException(400, "id mismatch")
    loader.save_widget(body)
    runtime.reload_assets()
    version = get_store().record("widget", widget_id, body.model_dump(exclude_none=True))
    return {"status": "saved", "version": version}


@app.delete("/widgets/{widget_id}")
def delete_widget(widget_id: str) -> dict[str, str]:
    check_ref("widget id", widget_id)
    if not loader.delete_widget(widget_id):
        raise HTTPException(404, "Unknown widget")
    runtime.reload_assets()
    get_store().log("widget", widget_id, "deleted")
    return {"status": "deleted"}


@app.post("/widgets/{widget_id}/restore/{version}")
def restore_widget(widget_id: str, version: int) -> dict[str, Any]:
    """Materialize a stored revision back to disk (and record it as a new one)."""
    check_ref("widget id", widget_id)
    rev = get_store().get("widget", widget_id, version)
    if rev is None:
        raise HTTPException(404, "Unknown revision")
    spec = WidgetSpec.model_validate(rev["body"])
    if spec.id != widget_id:
        raise HTTPException(400, "id mismatch in stored revision")
    loader.save_widget(spec)
    runtime.reload_assets()
    new_version = get_store().record(
        "widget", widget_id, rev["body"],
        label=f"restored from v{version}", action="restored",
    )
    return {"status": "restored", "version": new_version}


# ---------- Workspace store: history + activity ----------

@app.get("/store/revisions/{kind}/{ref}")
def revision_history(kind: str, ref: str) -> dict[str, Any]:
    if kind not in ("skill", "widget", "workflow"):
        raise HTTPException(400, "kind must be skill | widget | workflow")
    check_ref("revision ref", ref)
    return {"revisions": get_store().history(kind, ref)}


@app.get("/store/revisions/{kind}/{ref}/{version}")
def revision_body(kind: str, ref: str, version: int) -> dict[str, Any]:
    if kind not in ("skill", "widget", "workflow"):
        raise HTTPException(400, "kind must be skill | widget | workflow")
    check_ref("revision ref", ref)
    rev = get_store().get(kind, ref, version)
    if rev is None:
        raise HTTPException(404, "Unknown revision")
    return rev


@app.get("/store/activity")
def store_activity(limit: int = Query(default=30, le=200)) -> dict[str, Any]:
    return {"events": get_store().activity(limit)}


@app.post("/widgets/reload")
def reload_assets() -> dict[str, str]:
    runtime.reload_assets()
    return {"status": "reloaded"}


# ---------- Run lifecycle ----------

@app.post("/runs", response_model=StartResponse)
def start_run(req: StartRequest) -> StartResponse:
    try:
        session = runtime.new_session(req.workflow_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return StartResponse(thread_id=session.thread_id)


@app.get("/runs/{thread_id}/stream")
async def stream_run(
    thread_id: str,
    protocol: str = Query("ag-ui", description=f"One of: {', '.join(SUPPORTED)}"),
) -> EventSourceResponse:
    try:
        session = runtime.get_session(thread_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    if protocol not in SUPPORTED:
        raise HTTPException(400, f"Unsupported protocol: {protocol}")

    async def event_gen():
        async for sse in emit(protocol, runtime.run(session)):
            yield sse

    return EventSourceResponse(event_gen())


# ---------- AI generation ----------

class GenerateWidgetRequest(BaseModel):
    prompt: str
    suggested_id: str | None = None


@app.get("/ai/providers")
def ai_providers() -> dict[str, Any]:
    info = generator.available_providers()
    try:
        info["active"] = generator.select_provider()
    except generator.ProviderUnavailable:
        info["active"] = None
    return info


@app.post("/ai/generate-widget")
def generate_widget(req: GenerateWidgetRequest) -> dict[str, Any]:
    if not req.prompt.strip():
        raise HTTPException(400, "prompt is required")
    try:
        return generator.generate_widget(req.prompt, req.suggested_id)
    except generator.ProviderUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"generation failed: {exc}") from exc


@app.post("/runs/{thread_id}/submit")
def submit(thread_id: str, req: SubmitRequest) -> dict[str, str]:
    try:
        runtime.submit(thread_id, req.payload)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"status": "accepted"}


# ---------------------------------------------------------------------------
# `python -m aigenos_flow.main` / `aigenos-flow-server`
# ---------------------------------------------------------------------------

_PUBLIC_BINDS = frozenset({"0.0.0.0", "::", "*"})


def check_bind(host: str, *, allow_public: bool | None = None) -> None:
    """Refuse a public bind while the code-writing surface is open.

    ``FG_SKILL_WRITES=off`` makes a public bind merely a choice, so it is
    allowed with a warning — that is the mode for serving chat publicly with no
    authoring surface. ``local`` (the default) or ``open`` plus a public
    interface is the exact shape of the reported RCE, so it is refused unless
    ``FG_ALLOW_PUBLIC_BIND=1`` says the operator means it.
    """
    if host not in _PUBLIC_BINDS and not _is_public_hostname(host):
        return
    if allow_public is None:
        allow_public = (os.getenv("FG_ALLOW_PUBLIC_BIND") or "").strip().lower() in {
            "1", "true", "yes", "on",
        }
    mode = skill_writes_mode()
    if mode in {"local", "open"} and not allow_public:
        raise SystemExit(
            f"refusing to bind {host}: FG_SKILL_WRITES={mode} leaves endpoints that write "
            "and import Python reachable from the network. Set FG_SKILL_WRITES=off to "
            "serve chat with no authoring surface, or FG_SKILL_WRITES=token with "
            "FG_ADMIN_TOKEN to keep it behind a secret; or bind 127.0.0.1; or set "
            "FG_ALLOW_PUBLIC_BIND=1 if you have your own authenticating proxy in front."
        )
    log.warning(
        "binding %s — this server is reachable off-box. skill-management writes: %s, "
        "CORS: %s", host, mode, ", ".join(cors_origins()),
    )


def _is_public_hostname(host: str) -> bool:
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host not in {"localhost", ""}


def main(argv: list[str] | None = None) -> None:
    """Run the server. Binds loopback unless told otherwise."""
    import argparse

    parser = argparse.ArgumentParser(prog="aigenos-flow-server")
    parser.add_argument(
        "--host",
        default=os.getenv("FG_HOST", "127.0.0.1"),
        help="interface to bind (default 127.0.0.1 — loopback only)",
    )
    parser.add_argument("--port", type=int, default=int(os.getenv("FG_PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    check_bind(args.host)

    import uvicorn

    uvicorn.run(
        "aigenos_flow.main:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":  # pragma: no cover
    main()

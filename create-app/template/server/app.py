"""__APP_NAME__ — deterministic agentic backend.

A thin, stateless FastAPI server over an aigenos/flow agent. Each request
carries the full conversation `state`; the server runs exactly one turn and
hands the updated state back. That keeps the engine deterministic and the
server trivial to scale — no sessions, no SSE buffering, no hidden memory.

Endpoints:
  POST /api/chat          {message, state?}  -> {messages, widget, awaiting, done, state}
  POST /api/submit        {fields,  state}   -> same shape (widget submission, no LLM)
  GET  /api/health        {ok, skills, provider, model, llm_error, skill_errors}
  GET  /api/flow/outline  the real steps of every skill in server/skills/
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.hosts.langgraph_host import LangGraphHost
from aigenos_flow.outline import outline_folder

SKILLS_DIR = Path(__file__).parent / "skills"

DEFAULT_MODELS = {
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5-20251001",
    "ollama": "qwen3:8b",
}


# --------------------------------------------------------------------------- #
# LLM auto-detection. Drop ONE key in .env and the right provider wires up.
# --------------------------------------------------------------------------- #
def select_provider() -> tuple[str, str]:
    """(provider, model) from the environment.

    FG_MODEL is the override for whichever provider was selected; the Ollama
    model name lives in its own FG_OLLAMA_MODEL so a container/compose file can
    set it without hijacking a hosted provider. (Handing "qwen3:8b" to Gemini
    because compose exported FG_MODEL unconditionally was a real bug.)
    """
    provider = os.environ.get("FG_PROVIDER") or ""
    if provider and provider not in DEFAULT_MODELS:
        # Fail loudly: a typo'd FG_PROVIDER used to fall through to Ollama and
        # look like "no key found", which sends you hunting the wrong bug.
        raise RuntimeError(
            f"FG_PROVIDER={provider!r} is not one of "
            f"{', '.join(sorted(DEFAULT_MODELS))} — fix it in .env"
        )

    def has(*names):
        return any(os.environ.get(n) for n in names)

    if not provider:
        if has("GOOGLE_API_KEY", "GEMINI_API_KEY"):
            provider = "gemini"
        elif has("OPENAI_API_KEY"):
            provider = "openai"
        elif has("ANTHROPIC_API_KEY"):
            provider = "anthropic"
        else:
            provider = "ollama"  # local fallback — no key needed

    model = os.environ.get("FG_MODEL") or ""
    if not model and provider == "ollama":
        model = os.environ.get("FG_OLLAMA_MODEL") or ""
    return provider, model or DEFAULT_MODELS.get(provider, DEFAULT_MODELS["ollama"])


def build_llm():
    provider, model = select_provider()

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
        return ChatGoogleGenerativeAI(model=model, temperature=0)
    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0)
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=0, max_tokens=2048)
    from langchain_ollama import ChatOllama

    return ChatOllama(model=model, temperature=0)


def ollama_host() -> str:
    host = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if "://" not in host:  # the ollama CLI accepts a bare host:port
        host = "http://" + host
    return host.rstrip("/")


def ollama_problem(model: str, timeout: float = 1.5) -> str | None:
    """Why a local Ollama cannot serve ``model`` right now, or None if it can.

    A ChatOllama object constructs happily against a dead endpoint — nothing
    touches the network until the first generate call. So "the server booted"
    said nothing about whether chat would work, and the UI's "add a key" banner
    never appeared. One cheap GET turns that into an honest answer.
    """
    host = ollama_host()
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=timeout) as resp:
            tags = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return (
            f"no API key found and no local model server: Ollama is not "
            f"reachable at {host} ({type(exc).__name__}). Add one key to .env "
            f"(GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY), or start "
            f"Ollama and run: ollama pull {model}"
        )
    installed = [m.get("name", "") for m in (tags.get("models") or [])]
    if not installed:
        return (
            f"Ollama is running at {host} but has no models installed. "
            f"Run: ollama pull {model}"
        )
    stem = lambda n: n.split(":", 1)[0]  # noqa: E731 — qwen3:8b vs qwen3:latest
    if model not in installed and stem(model) not in {stem(n) for n in installed}:
        return (
            f"Ollama at {host} does not have model {model!r} "
            f"(installed: {', '.join(installed[:5])}). "
            f"Run: ollama pull {model} — or set FG_OLLAMA_MODEL to one you have."
        )
    return None


# --------------------------------------------------------------------------- #
# Build the agent once at startup.
# --------------------------------------------------------------------------- #
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

# Lazily build the agent so the server ALWAYS boots — even with no LLM key set.
# Chat then degrades to a friendly "add a key" message instead of a crash.
_AGENT = None
_HOST = None
_BUILD_ERROR: str | None = None


def get_agent():
    """Build once, and be honest about failure.

    The check is REACHABILITY, not construction: every provider client builds
    fine with a missing or bogus key, so "did the object construct" is not a
    usable health signal. For the keyless local path we ask Ollama whether it
    actually has the model. /api/health reports the verdict, so the UI banner
    appears exactly when chat will not work.
    """
    global _AGENT, _HOST, _BUILD_ERROR
    if _AGENT is not None:
        return _AGENT, _HOST
    try:
        provider, model = select_provider()
        if provider == "ollama":
            problem = ollama_problem(model)
            if problem:
                raise RuntimeError(problem)
        _HOST = LangGraphHost(build_llm())
        _AGENT = FlowAgent.from_dir(SKILLS_DIR, host=_HOST, mode="flex")
        _BUILD_ERROR = None
    except Exception as exc:  # missing provider lib, no key, dead endpoint, …
        _AGENT = None
        _HOST = None
        _BUILD_ERROR = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
    return _AGENT, _HOST


def _provider_model_safe() -> tuple[str | None, str | None]:
    """select_provider() for reporting paths that must never raise."""
    try:
        return select_provider()
    except Exception:
        return None, None


def skill_load_errors() -> dict[str, str]:
    """{skill folder: why it did not load}.

    The loader is deliberately lenient — one bad skill must not take the folder
    down — but "skipped" must never look like "fine". A misspelled key in
    SKILL.md is a load error, and this is where you see it.
    """
    agent = _AGENT
    if agent is None:
        return {}
    return dict(getattr(agent.registry, "errors", {}) or {})


def _no_llm_response(state: dict[str, Any] | None, detail: str | None = None) -> dict[str, Any]:
    return {
        "messages": [
            "⚠ Chat needs a working model. "
            + (detail or _BUILD_ERROR or "no provider detected")
            + " (A new key in .env needs a server restart; starting Ollama "
            "only needs a page reload.)"
        ],
        "widget": None, "awaiting": None, "done": False,
        "flow_id": None, "fields": {}, "state": state or initial_state(),
    }


def _turn_failed(state: dict[str, Any] | None, exc: Exception) -> dict[str, Any]:
    """A live provider that rejects the call (bad key, wrong model name, rate
    limit) must read as a message, not as an HTTP 500 the UI shows as '⚠ 500'."""
    provider, model = _provider_model_safe()
    return _no_llm_response(
        state,
        f"The {provider} call for model {model!r} failed: "
        f"{type(exc).__name__}: {exc}. Fix the key or model name in .env, "
        "then restart the server.",
    )


app = FastAPI(title="__APP_NAME__")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# Turn a pending field's FieldSpec into a typed widget the UI renders. This is
# the same schema the engine validates against — the form IS the flow.
# --------------------------------------------------------------------------- #
def widget_for(agent, state: dict[str, Any]) -> dict[str, Any] | None:
    field = state.get("awaiting_field")
    flow_id = state.get("flow_id")
    if not field or not flow_id:
        return None
    try:
        skill = agent.registry.get(flow_id)
        spec = (skill.fields or {}).get(field)
    except Exception:
        spec = None
    if spec is None:
        return {"name": field, "kind": "text", "label": field.replace("_", " ")}

    kind = {
        "enum": "select", "boolean": "toggle",
        "integer": "number", "number": "number",
    }.get(spec.type, "text")
    # At a `confirm:` the engine says which tool a yes will run and with which
    # inputs. It holds the tool to exactly that: change an input after the yes
    # and nothing runs.
    pending = state.get("pending_widget") or {}
    return {
        "name": field,
        "kind": kind,
        "label": (spec.description or field.replace("_", " ")),
        "options": list(spec.values) if spec.values else None,
        "min": spec.min,
        "max": spec.max,
        "runs": (pending.get("data") or {}).get("runs") or None,
    }


def _dedupe(messages: list[str]) -> list[str]:
    """Drop a line that repeats the one before it.

    The engine emits each line once, but a model asked to speak naturally can
    emit the same Reply twice in a turn (observed against a local qwen3:8b:
    "Got it. What category is this expense?" arrived twice for one user
    message). Nothing downstream wants to render that, and collapsing only
    *consecutive* duplicates never hides a legitimately repeated prompt from a
    later step.
    """
    out: list[str] = []
    for m in messages:
        if not out or out[-1] != m:
            out.append(m)
    return out


def respond(agent, host, state: dict[str, Any]) -> dict[str, Any]:
    messages = _dedupe(host.drain_pending_messages())
    return {
        "messages": messages,
        "widget": widget_for(agent, state),
        "awaiting": state.get("awaiting_field"),
        "done": bool(state.get("done")),
        "flow_id": state.get("flow_id"),
        "fields": state.get("fields", {}),
        "state": state,
    }


class ChatIn(BaseModel):
    message: str
    state: dict[str, Any] | None = None


class SubmitIn(BaseModel):
    fields: dict[str, Any]
    state: dict[str, Any]


@app.get("/api/health")
def health() -> dict[str, Any]:
    agent, _ = get_agent()
    provider, model = _provider_model_safe()
    return {
        "ok": agent is not None,
        "skills": agent.list_skills() if agent else [],
        "provider": provider,
        "model": model,
        "llm_error": _BUILD_ERROR,
        # A SKILL.md the loader rejected is skipped, not fatal — so surface it
        # here instead of letting a typo'd skill silently vanish from chat.
        "skill_errors": skill_load_errors(),
    }


@app.get("/api/flow/outline")
def flow_outline() -> dict[str, Any]:
    """The real control flow of the skills in server/skills/, straight from
    their source — a read-only view the UI renders. No LLM involved.

    Works with no key configured: it reads the files, it does not run a turn.
    """
    out = []
    for folder in sorted(p for p in SKILLS_DIR.iterdir() if (p / "SKILL.md").exists()):
        try:
            outline = outline_folder(folder)
        except Exception as exc:  # a half-written skill must not blank the tab
            outline = {"mode": "error", "editable": False, "steps": [],
                       "note": f"{type(exc).__name__}: {exc}"}
        out.append({"name": folder.name, **outline})
    return {"skills": out}


@app.post("/api/chat")
def chat(body: ChatIn) -> dict[str, Any]:
    agent, host = get_agent()
    if agent is None:
        return _no_llm_response(body.state)
    state = body.state or initial_state()
    if state.get("done"):  # a finished flow starts fresh on the next message
        state = initial_state()
    state["pending_user_message"] = body.message
    try:
        state = agent.turn(state)
    except Exception as exc:
        return _turn_failed(body.state, exc)
    return respond(agent, host, state)


@app.post("/api/submit")
def submit(body: SubmitIn) -> dict[str, Any]:
    agent, host = get_agent()
    if agent is None:
        return _no_llm_response(body.state)
    state = body.state
    state["pending_field_updates"] = body.fields
    try:
        state = agent.turn(state)
    except Exception as exc:
        return _turn_failed(body.state, exc)
    return respond(agent, host, state)

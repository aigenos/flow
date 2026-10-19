"""aigenos_flow.integration — drop into an existing LangGraph ReAct agent.

One function call to add deterministic skill flows to an existing
``create_react_agent`` setup:

    from aigenos_flow.integration import build_react_with_skills

    agent = build_react_with_skills(
        llm=llm,
        tools=[show_meeting_rooms, show_bookings],   # your existing tools
        prompt=MY_SYSTEM,                              # your existing prompt
        skills_dir="skills/",                          # SKILL.md files
        use_flow=True,                            # False = body-as-prompt only
    )

The returned object is a compiled LangGraph graph — same interface as the
output of ``create_react_agent``.

What you get:
  - The skill catalog (name + description) is appended to your system
    prompt — level-1 progressive disclosure (Anthropic Skills convention).
  - An ``activate_skill(name)`` tool is added to your tool list. The LLM
    sees the catalog and decides — on its own — when to call it.
  - At the entry of every turn, an LLM classifier checks whether the user's
    message matches a skill in the catalog. If yes, the flow runs
    deterministically. If no, the ReAct loop runs as usual. The classifier
    is the *deterministic-when* part of activation; the LLM is the
    *deciding-which* part.
  - When a flow is active, control is in aigenos flow's runtime: typed field
    validation, memoized actions, mid-flow corrections, push/pop digressions.

What this does NOT do: routing is a judgement call, so a turn can land in
the ReAct loop when a flow would have been the better answer. That is only
safe if the ReAct loop cannot perform the guarded side effect itself, so a
tool a skill governs is not handed to the model raw — see ``flow_only`` and
the ``governed_tools`` argument below.

No regex and no keyword matching: which skill a message is for is decided
by a small, purpose-built structured-output call (see
``interpret.classify_skill``).
"""
from __future__ import annotations

import json
import logging
import secrets
import typing
import warnings
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool, tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command, interrupt

from .agent import FlowAgent
from .hosts.langgraph_host import LangGraphHost
from .interpret import classify_skill, decide_outside_flow, render_memory, render_paused
from .runtime import (
    new_flow_updates,
    node_updates,
    resume_state_updates,
    resume_updates,
    run_turn,
)
from .skills import parse_skill_md

log = logging.getLogger(__name__)

#: How many times one turn may re-enter the flow node without the flow either
#: asking the user something or finishing. Bounds the "replay to the next
#: ask:" continuation after a digression pops back, and a chain of
#: ``ctx.link()`` hand-offs to flows that never pause (two re-entries each).
_MAX_FLOW_HOPS = 16


# ---------------------------------------------------------------------------
# REPL helper — strip the tool-call JSON fragments that some LLMs leak into
# text content. Useful for any demo that prints AIMessage content directly.
# ---------------------------------------------------------------------------


def message_text(content: Any) -> str:
    """Extract human-readable text from an AIMessage's ``content``.

    Handles:
      - plain strings
      - list-of-blocks format from Anthropic ([{"type":"text","text":...}])
      - tool-call fragments some models emit as text (filtered out)

    Returns "" if the content is empty or pure tool-call noise. Callers
    should ``if text: print(...)`` so empty turns don't show as blank lines.
    """
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        parts: list[str] = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        text = "".join(parts).strip()
    else:
        text = str(content).strip()

    if not text:
        return ""

    if _looks_like_tool_fragment(text):
        return ""
    return text


def _looks_like_tool_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    fragment_starts = ('", "', '",', '"}', '"}}', ', "', '"parameters"', '"input"')
    if stripped.startswith(fragment_starts):
        return True
    if (
        '"parameters"' in stripped
        and '{' not in stripped[:10]
        and ' ' not in stripped[:20]
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# tools= shapes — both front doors accept the same two
# ---------------------------------------------------------------------------

_TOOLS_SHAPES = (
    "tools= takes a list of LangChain tools / plain callables, or a "
    "{name: callable} dict"
)


def _tools_as_items(tools: Any, *, where: str) -> list[tuple[str | None, Any]]:
    """Normalize the ``tools=`` argument to ``[(granted_name | None, tool)]``.

    Accepted: ``None``, a list/tuple/set of LangChain tools or plain
    callables, or a ``{name: callable}`` dict (the shape ``FlowAgent.from_dir``
    and the SKILL.md ``actions:`` registry document). Anything else is a
    ``TypeError`` naming the accepted shapes — never an AttributeError from
    deep inside LangGraph.

    The dict key is the name a SKILL.md's ``tool:`` resolves to, so it — not
    the function's ``__name__`` — is the name the model-facing copy must
    carry, or governance would look for the wrong tool.
    """
    if tools is None:
        return []
    if isinstance(tools, dict):
        return [(str(k), v) for k, v in tools.items()]
    if isinstance(tools, (list, tuple, set)):
        return [(None, t) for t in tools]
    # A ToolRegistry (or anything else that can hand us its raw tools) is
    # accepted for the flow side; there is nothing to give the ReAct loop.
    raw = getattr(tools, "_raw", None)
    if isinstance(raw, dict):
        return [(str(k), v) for k, v in raw.items()]
    raise TypeError(
        f"{where}(tools=...): {_TOOLS_SHAPES}; got {type(tools).__name__}"
    )


def _as_lc_tool(fn: Any, name: str | None = None) -> BaseTool:
    """Return a LangChain tool for ``fn``; already-a-tool passes through.

    A plain callable is adapted with ``StructuredTool.from_function``, using
    its docstring when it has one and a generated description when it does
    not — a missing docstring must never break agent construction. ``name``
    (the granted registry name) wins over the function's own.
    """
    if isinstance(fn, BaseTool):
        if name and fn.name != name:
            return fn.model_copy(update={"name": name})
        return fn
    if not callable(fn):
        raise TypeError(f"{_TOOLS_SHAPES}; got {type(fn).__name__}")
    tool_name = name or getattr(fn, "name", None) or getattr(fn, "__name__", None) or "tool"
    description = (getattr(fn, "__doc__", None) or "").strip() or f"Tool {tool_name}."
    try:
        return StructuredTool.from_function(fn, name=tool_name, description=description)
    except Exception as exc:  # pragma: no cover — exotic callables
        raise TypeError(
            f"tool {tool_name!r} could not be adapted to a LangChain tool "
            f"({type(exc).__name__}: {exc}). Wrap it with @tool yourself."
        ) from exc


# ---------------------------------------------------------------------------
# Governed tools — a write a skill owns must be unreachable except through
# that skill's flow. Defence in depth for "the classifier picked no skill".
# ---------------------------------------------------------------------------

_FLOW_ONLY = "aigenos_flow_only"
_FLOW_ONLY_TARGET = "aigenos_flow_only_target"
_FLOW_ONLY_SKILL = "aigenos_flow_only_skill"


def is_flow_only(tool_obj: Any) -> bool:
    """True if ``tool_obj`` is a flow-only shim (see :func:`flow_only`)."""
    return bool((getattr(tool_obj, "metadata", None) or {}).get(_FLOW_ONLY))


def unwrap_flow_only(tool_obj: Any) -> Any:
    """The real tool behind a flow-only shim (or ``tool_obj`` unchanged)."""
    meta = getattr(tool_obj, "metadata", None) or {}
    return meta.get(_FLOW_ONLY_TARGET, tool_obj)


def _shim(target: Any, *, name_of: BaseTool, note: str, body, skill: str | None) -> BaseTool:
    schema = name_of.args_schema
    kwargs: dict[str, Any] = {}
    if schema is not None:
        kwargs["args_schema"] = schema
    return StructuredTool(
        name=name_of.name,
        description=(name_of.description or "").rstrip() + "\n\n" + note,
        func=body,
        metadata={
            _FLOW_ONLY: True,
            _FLOW_ONLY_TARGET: target,
            _FLOW_ONLY_SKILL: skill,
        },
        **kwargs,
    )


def flow_only(tool_obj: Any, *, skill: str | None = None, message: str | None = None) -> BaseTool:
    """Make a tool unreachable to the model — only its flow may execute it.

    Returns a drop-in replacement with the same name, arguments and
    description that **refuses** every direct call. Give the replacement to
    your agent and the same object to ``tools=``: the flow runtime unwraps it
    and calls the real tool when (and only when) the skill's gates pass.

        from aigenos_flow.integration import flow_only, wrap_agent

        transfer_money = flow_only(transfer_money, skill="transfer-funds")
        my_agent = create_react_agent(llm, tools=[lookup, transfer_money])
        safe = wrap_agent(my_agent, skills_dir="skills/", llm=llm,
                          tools=[bank_transfer])

    Why: activation is a judgement call. On a turn the classifier does not
    route to the skill, the ReAct loop answers — and anything still in its
    tool list is reachable, ungated. Wrapping the write makes the worst case
    a re-ask instead of an unvalidated side effect.

    ``skill`` only sharpens the refusal text. ``message`` overrides it.
    """
    target = unwrap_flow_only(tool_obj)
    lc = _as_lc_tool(target)
    by = f"the {skill!r} flow" if skill else "a deterministic flow"
    refusal = message or (
        f"REFUSED: {lc.name!r} is governed by {by} and cannot be called "
        "directly. Nothing happened. Do not tell the user it did — restate "
        "what they asked for so the flow can run its checks first."
    )

    def _refuse(*_a: Any, **_k: Any) -> str:
        log.warning(
            "governed tool %r was called outside its flow — refused (%s)",
            lc.name,
            skill or "no skill named",
        )
        return refusal

    return _shim(
        target,
        name_of=lc,
        note=f"GOVERNED: only {by} may execute this. A direct call is refused.",
        body=_refuse,
        skill=skill,
    )


def _routing_shim(tool_obj: Any, *, skill: str, token: str, name: str | None = None) -> BaseTool:
    """A flow-only shim that also hands the turn to ``skill``'s flow.

    Used by :func:`create_agent` / :func:`build_react_with_skills`, which own
    the model-facing tool list. A model that reaches for a governed write
    does not get it: the deterministic flow starts instead, from an empty
    field set, and elicits what it needs from the user.
    """
    lc = _as_lc_tool(unwrap_flow_only(tool_obj), name)
    payload = json.dumps({
        "mode": "flow",
        "activated": skill,
        "aigenos_activation_token": token,
        "note": (
            f"{lc.name!r} is governed by the {skill!r} flow and was NOT called. "
            "The deterministic flow is taking over. Do not reply further this turn."
        ),
    })

    def _route(*_a: Any, **_k: Any) -> str:
        log.warning(
            "governed tool %r was called outside its flow — handing the turn "
            "to skill %r instead",
            lc.name,
            skill,
        )
        return payload

    return _shim(
        unwrap_flow_only(tool_obj),
        name_of=lc,
        note=(
            f"GOVERNED: only the {skill!r} flow may execute this. Calling it "
            "starts that flow instead of performing the action."
        ),
        body=_route,
        skill=skill,
    )


def _split_tool_names(value: str | None) -> set[str]:
    """``"a, b c"`` -> ``{"a", "b", "c"}`` (same syntax as ``allowed-tools``)."""
    if not value:
        return set()
    return {part for part in str(value).replace(",", " ").split() if part}


def governed_write_tools(registry) -> dict[str, str]:
    """Map ``tool name -> skill name`` for every write a loaded skill owns.

    A tool is a *write* unless the skill declares it read-only (an action
    with ``readonly: true``, or a name on the ``readonly-tools:`` line).
    Read-only lookups stay available to the ReAct loop.
    """
    out: dict[str, str] = {}
    for skill in registry.all():
        if not getattr(skill, "workflow", True):
            continue  # instruction-only skill: it governs nothing
        actions = getattr(skill, "actions", None) or {}
        readonly = _split_tool_names(getattr(skill, "readonly_tools", None))
        for spec in actions.values():
            name = getattr(spec, "tool", None)
            if name and getattr(spec, "readonly", False):
                readonly.add(name)
        writes: set[str] = set()
        for spec in actions.values():
            name = getattr(spec, "tool", None)
            if name and not getattr(spec, "readonly", False):
                writes.add(name)
        # Imperative skills (handlers.py) name their tools on `allowed-tools`
        # without a declarative action spec; anything not marked read-only is
        # assumed to write.
        writes |= _split_tool_names(getattr(skill, "allowed_tools", None))
        for name in sorted(writes - readonly):
            out.setdefault(name, skill.name)
    return out


def _inner_tools_by_name(agent) -> dict[str, Any]:
    """Best-effort: the tool registry of a compiled LangGraph agent's ToolNode."""
    for node in (getattr(agent, "nodes", None) or {}).values():
        for obj in (node, getattr(node, "bound", None)):
            registry = getattr(obj, "tools_by_name", None)
            if isinstance(registry, dict):
                return registry
    return {}


def _check_governed_tools_policy(agent, registry, policy: str) -> None:
    """Fail closed (or warn) when a wrapped agent keeps a governed write.

    ``wrap_agent`` cannot edit a compiled graph's tool list, so this is a
    build-time check, not a repair. Name collisions are an error; tools it
    cannot vouch for are a warning, because no library can tell that the
    inner ``transfer_money`` does the same job as the skill's
    ``bank_transfer``.
    """
    if policy == "allow":
        return
    governed = governed_write_tools(registry)
    if not governed:
        return
    inner = _inner_tools_by_name(agent)
    if not inner:
        # A hand-built graph calls its tools from its own node, where there is
        # nothing to inspect. Silence here would read as "checked and clean".
        named = ", ".join(sorted(repr(n) for n in governed))
        warnings.warn(
            "wrap_agent: the wrapped agent has no ToolNode to inspect, so its "
            f"tools were NOT checked. If it can call a tool that does the job of "
            f"a governed write ({named}), a turn the classifier does not route "
            "reaches that tool ungated. Wrap it with aigenos_flow.flow_only() "
            "where your node calls it, remove it from the agent, or pass "
            "governed_tools='allow' to silence this.",
            UserWarning,
            stacklevel=3,
        )
        return
    collisions = sorted(
        n for n, t in inner.items() if n in governed and not is_flow_only(t)
    )
    if collisions:
        named = ", ".join(f"{n!r} (governed by skill {governed[n]!r})" for n in collisions)
        raise ValueError(
            f"wrap_agent: the wrapped agent can still call {named} itself. "
            "On any turn the classifier does not route to that skill, the "
            "model reaches the tool directly and the flow's gates never run. "
            "Wrap it with aigenos_flow.integration.flow_only(tool, "
            "skill=...) in your "
            "agent's tool list (the flow still executes the real tool), "
            "remove it from the wrapped agent, or pass "
            "governed_tools='allow' to keep the ungated path."
        )
    loose = sorted(n for n, t in inner.items() if not is_flow_only(t))
    if loose:
        shown = ", ".join(repr(n) for n in loose[:5])
        more = f" (+{len(loose) - 5} more)" if len(loose) > 5 else ""
        warnings.warn(
            f"wrap_agent: {len(loose)} tool(s) on the wrapped agent are not "
            f"governed by any skill: {shown}{more}. A turn the classifier does "
            "not route reaches them ungated. Wrap any tool that duplicates a "
            "governed workflow with aigenos_flow.integration.flow_only(), or pass "
            "governed_tools='allow' to silence this.",
            UserWarning,
            stacklevel=3,
        )


# ---------------------------------------------------------------------------
# State shape — your existing react state PLUS aigenos flow keys.
# ---------------------------------------------------------------------------


class _State(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    # aigenos flow keys
    pending_user_message: str | None
    pending_field_updates: dict[str, Any] | None
    flow_id: str | None
    fields: dict[str, Any]
    results: dict[str, Any]
    say_cursor: int
    links_done: list
    field_history: dict[str, list[Any]]
    history: list[dict[str, str]]
    awaiting_user: bool
    awaiting_field: str | None
    pending_fields: list | None
    flow_stack: list
    done: bool
    # Chat-leaf routing is read on the NEXT turn.
    chat_config: dict[str, Any] | None
    pending_widget: dict[str, Any] | None
    # The at-most-once guard: one flow run = one flow_run_id, and effects_log
    # records what already fired under it. Both MUST be checkpointed or a
    # confirmed intent can fire a second side effect on a later turn.
    flow_run_id: str | None
    effects_log: dict[str, Any]
    # What each confirmation card showed; the released tool is held to it.
    approvals: dict[str, Any]
    # The conversation's memory (runtime.FlowState): what each run came to, and
    # the run a cancel set aside. Undeclared, LangGraph would drop both.
    journal: list
    parked: dict[str, Any] | None
    # How many times in a row the flow node has run without a fresh user
    # action — bounds the "keep replaying until the next ask" continuation.
    flow_hops: int


#: Keys the wrapper owns. Everything else on the wrapped agent's own state
#: schema is passed through to it and merged back unchanged.
_OWNED_KEYS: frozenset[str] = frozenset(_State.__annotations__) | {"messages"}


def _merged_state_schema(agent) -> tuple[type, tuple[str, ...]]:
    """``(state_schema, extra_keys)`` for the wrapper graph.

    The wrapped agent keeps its state: every key of its own state schema that
    the wrapper does not own is copied onto the wrapper's schema, so the
    caller can pass it in, the inner agent sees it, and it survives
    checkpointing. Reducers are deliberately dropped on the copy — the inner
    agent already applied its own while it ran, and the wrapper stores the
    result (one writer per turn), so an accumulating reducer cannot
    double-apply.

    LangGraph *managed* values (``remaining_steps`` and friends, whose
    annotation carries a class rather than a reducer function) are left
    alone: they belong to the inner graph.
    """
    schema = getattr(getattr(agent, "builder", None), "state_schema", None)
    hints: dict[str, Any] = {}
    if schema is not None:
        try:
            hints = typing.get_type_hints(schema, include_extras=True)
        except Exception:  # pragma: no cover — exotic/pydantic state schemas
            hints = {}
    extra: dict[str, Any] = {}
    for key, ann in hints.items():
        if key in _OWNED_KEYS:
            continue
        ann = _strip_required(ann)
        metas = typing.get_args(ann)[1:] if typing.get_origin(ann) is Annotated else ()
        if any(isinstance(m, type) for m in metas):
            continue  # managed value — the inner graph owns it
        extra[key] = typing.get_args(ann)[0] if metas else ann
    if not extra:
        return _State, ()
    try:
        own = typing.get_type_hints(_State, include_extras=True)
    except Exception:  # pragma: no cover
        return _State, ()
    merged = TypedDict("_WrappedState", {**own, **extra}, total=False)  # type: ignore[operator]
    return merged, tuple(extra)


def _strip_required(ann: Any) -> Any:
    """Unwrap ``Required[...]`` / ``NotRequired[...]`` (typing or typing_extensions)."""
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin is not None and args and str(origin).endswith(("Required", "NotRequired")):
        return args[0]
    return ann


def build_react_with_skills(
    *,
    llm,
    tools: list | dict | None = None,
    prompt: str = "",
    skills_dir: str | Path,
    use_flow: bool = True,
    checkpointer=None,
    mode: str = "flex",
    a2ui: bool = True,
    host=None,
    governed_tools: str = "flow_only",
    tools_dir: str | Path | None = None,
):
    """Build a ReAct agent that knows how to activate skills.

    Args:
        llm: A LangChain BaseChatModel (Anthropic, OpenAI, Ollama, ...).
        tools: Your existing tools — a list of LangChain tools / plain
            callables, or a ``{name: callable}`` dict. They double as tools a
            skill's steps may name, next to the library in
            ``<skills_dir>/tools/`` (and ``tools_dir``).
        prompt: Your existing system prompt (a ``str``, or a
            ``SystemMessage`` whose content is one). The skill catalog is
            appended to it automatically.
        skills_dir: Directory containing skill subfolders (each with a
            ``SKILL.md``, optionally ``handlers.py``). It must exist and,
            when ``use_flow`` is True, contain at least one flow-capable
            skill — otherwise this raises rather than quietly returning a
            plain, ungated ReAct agent.
        use_flow: If True (default), skills with a ``flow:`` (or a
            ``handlers.py`` ``@flow``) are run by aigenos flow's
            deterministic runtime when activated. Pass False for a catalog of
            instruction-only skills (body-as-prompt, no gates, no runtime).
        checkpointer: Optional LangGraph checkpointer for persistence.
        a2ui: Attach the paused field's widget spec to the interrupt payload
            and to the paused assistant message (default True).
        governed_tools: What happens to a tool a skill governs.
            ``"flow_only"`` (default) keeps it out of the model's reach: the
            model-facing copy starts the governing flow instead of performing
            the action, and only the flow executes the real tool.
            ``"allow"`` hands the raw tool to the model as well — the
            pre-0.3.1 behavior, ungated on any turn the classifier misses.

    Returns:
        A compiled LangGraph graph with the same interface as
        ``create_react_agent``'s output.
    """
    if governed_tools not in {"flow_only", "allow"}:
        raise ValueError(
            "governed_tools must be 'flow_only' or 'allow', got "
            f"{governed_tools!r}"
        )
    prompt = _prompt_text(prompt, where="build_react_with_skills")
    user_tools = _tools_as_items(tools, where="build_react_with_skills")

    skills_path = Path(skills_dir)
    # Fail closed: a typo'd or missing folder must never build an agent with
    # no gates in it. There is no silent downgrade to a plain agent.
    if not skills_path.is_dir():
        raise FileNotFoundError(
            f"skills directory not found: {skills_dir} "
            f"(resolved to {skills_path.resolve()})"
        )
    skills_meta = _discover_skills(skills_path)
    if not skills_meta:
        raise FileNotFoundError(
            f"no SKILL.md found under {skills_path.resolve()} — a skills "
            "directory holds one subfolder per skill, each with a SKILL.md"
        )
    if use_flow and not any(m["flow_capable"] for m in skills_meta):
        names = ", ".join(repr(m["name"]) for m in skills_meta)
        raise ValueError(
            f"no flow-capable skill in {skills_path.resolve()}: {len(skills_meta)} "
            f"SKILL.md found ({names}), none declaring `steps:`, a `flow:` or a "
            "handlers.py @flow. Nothing would be gated. Add `steps:` to the "
            "skill that owns the workflow, or — for a catalog of "
            "instruction-only skills — call build_react_with_skills(..., "
            "use_flow=False), which is explicitly prompt-only."
        )

    catalog = _render_catalog(skills_meta)
    prompt_block = (
        f"{prompt}\n\n"
        "# Available skills\n\n"
        "When the user's request clearly matches one of the skills below, "
        "call ``activate_skill(skill_name)`` to invoke it. Otherwise, just "
        "chat or use your other tools.\n\n"
        "IMPORTANT — ``activate_skill`` is ONLY for starting a NEW multi-step "
        "workflow (creating a new booking, filing a new expense, etc.). For "
        "read-only requests — \"show me\", \"list\", \"view\", \"what are my\", "
        "\"how many\" — use the regular query tools (e.g. show_bookings, "
        "show_meeting_rooms) and DO NOT re-activate a skill. If a skill just "
        "finished and the user asks to see the results, call the matching "
        "read-only tool; never start the workflow again.\n\n"
        f"{catalog}"
    )

    flow_agent: FlowAgent | None = None
    if use_flow:
        host = host or LangGraphHost(llm=llm)
        # The user's tools double as tools a skill may name, next to the
        # library in `<skills_dir>/tools/`.
        flow_agent = FlowAgent.from_dir(
            skills_path, host=host, mode=mode, tools=tools, tools_dir=tools_dir
        )

    # One unforgeable token per build. It travels in the activate_skill tool
    # result and is the ONLY thing that flips this graph into flow mode from
    # the ReAct side, so no inner tool payload can forge an activation.
    activation_token = secrets.token_urlsafe(16)

    # Model-facing tool list. A write a skill governs is replaced by a shim
    # that hands the turn to that skill's flow: the model cannot perform the
    # action, and the user's intent still gets served — through the gates.
    model_tools: list[Any] = []
    shimmed: dict[str, str] = {}
    governed = (
        governed_write_tools(flow_agent.registry)
        if (flow_agent is not None and governed_tools == "flow_only")
        else {}
    )
    for granted_name, t in user_tools:
        raw = unwrap_flow_only(t)
        lc = _as_lc_tool(raw, granted_name)
        owner = governed.get(lc.name)
        if is_flow_only(t):
            model_tools.append(t)  # already governed by the caller
        elif owner:
            model_tools.append(
                _routing_shim(raw, skill=owner, token=activation_token, name=lc.name)
            )
            shimmed[lc.name] = owner
        else:
            model_tools.append(lc)
    if shimmed:
        log.info(
            "governed tools kept out of the model's reach: %s",
            ", ".join(f"{n} -> skill {s}" for n, s in sorted(shimmed.items())),
        )
        # Tell the model the truth about these tools, so it asks instead of
        # claiming an action it cannot perform.
        listed = "\n".join(
            f"- **{n}** — governed by the {s!r} skill" for n, s in sorted(shimmed.items())
        )
        prompt_block += (
            "\n\n# Governed tools\n\n"
            "Calling one of these does NOT perform the action — it starts the "
            "skill that owns it, and that flow collects and verifies what it "
            "needs first. Never tell the user the action happened unless a "
            "tool result says it did.\n\n" + listed
        )

    activate_skill_tool = _make_activate_skill_tool(
        skills_meta, flow_agent, activation_token
    )
    all_tools = model_tools + [activate_skill_tool]

    # No flow agent ⇒ the ReAct agent is the whole show (use_flow=False, an
    # explicitly prompt-only catalog) — wire the checkpointer directly into
    # create_react_agent so state persists.
    if flow_agent is None:
        return _create_react_agent(
            llm,
            tools=all_tools,
            prompt=prompt_block,
            checkpointer=checkpointer,
        )

    react_agent = _create_react_agent(llm, tools=all_tools, prompt=prompt_block)
    return _wrap_with_flow_nodes(
        react_agent,
        flow_agent,
        checkpointer,
        a2ui=a2ui,
        activation_token=activation_token,
    )


def _prompt_text(prompt: Any, *, where: str) -> str:
    """Accept a ``str`` or a ``SystemMessage``; reject anything else loudly.

    ``create_react_agent`` also accepts a callable / Runnable prompt. This
    builder composes the skill catalog into the system prompt as text, so
    those shapes cannot be honoured — saying so beats ``str()``-mangling a
    message repr into the agent's instructions.
    """
    if prompt is None:
        return ""
    if isinstance(prompt, str):
        return prompt
    if isinstance(prompt, SystemMessage) and isinstance(prompt.content, str):
        return prompt.content
    raise TypeError(
        f"{where}(prompt=...) takes a str (or a SystemMessage whose content is "
        f"a str); got {type(prompt).__name__}. The skill catalog is appended to "
        "the system prompt as text, so callable/Runnable prompts are not "
        "supported — build the string yourself."
    )


def _create_react_agent(*args, **kwargs):
    """``create_react_agent``, without leaking our own deprecation notice.

    langgraph 1.x warns that ``create_react_agent`` moved to
    ``langchain.agents``. That is a message about *this* module's internals,
    which the caller cannot act on, so it is filtered here and nowhere else.
    """
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r".*create_react_agent has been moved.*",
        )
        return create_react_agent(*args, **kwargs)


# ---------------------------------------------------------------------------
# The two front doors
# ---------------------------------------------------------------------------


def create_agent(
    llm,
    skills_dir: str | Path = "skills/",
    *,
    tools: list | dict | None = None,
    prompt: str = "",
    a2ui: bool = True,
    mode: str = "flex",
    checkpointer=None,
    host=None,
    governed_tools: str = "flow_only",
    tools_dir: str | Path | None = None,
):
    """THE aigenos-flow agent — ReAct with deterministic skills inside.

    Point it at a skills folder and send it queries. A message that matches a
    skill runs that skill's flow **deterministically** (typed fields, gates,
    replayed order); everything else — and the conversation after a flow
    completes — is ordinary ReAct question/answer over the accumulated
    context. You write SKILL.md files (a list of ``steps:``) and the functions
    they name; this function does the rest.

        from aigenos_flow import create_agent

        agent = create_agent(llm, "skills/", tools=[bank_transfer])
        agent.invoke({"messages": [("user", "send $5000 to mom")]},
                     config={"configurable": {"thread_id": "t1"}})

    While a flow is paused on a field, the run interrupts with
    ``{"prompt", "field", "flow_id", "widget"}`` — resume with
    ``Command(resume={"field": value})`` (a widget submission, validated in
    pure Python, no LLM) or ``Command(resume="free text")``.

    ``tools`` is a list of LangChain tools / plain callables, or a
    ``{name: callable}`` dict. They are tools a skill's steps may name *and*
    the ReAct loop's tools — except that a tool a skill governs is not handed
    to the model raw. Tools can also live with the skills: every public
    function in ``<skills_dir>/tools.py`` or ``<skills_dir>/tools/*.py`` is a
    tool the skills can name (``tools_dir`` adds a folder kept elsewhere);
    those are run by flows only. Routing is a
    judgement call, so on a turn that lands in the ReAct loop the model gets
    a shim: calling it starts the governing flow (empty field set, gates
    armed) instead of performing the action. Pass
    ``governed_tools="allow"`` to keep the raw tool reachable by the model.

    ``skills_dir`` must exist and hold at least one flow-capable skill;
    otherwise this raises. It never returns a plain, ungated ReAct agent that
    merely looks like the real thing.

    ``a2ui`` (default True): attach the paused field's widget spec — the one
    declared in the skill via ``widget:``, or auto-generated from the
    FieldSpec — to the interrupt payload and to the last assistant message's
    ``additional_kwargs["aigenos_widget"]``, so an AG-UI/A2UI client can
    render it. ``a2ui=False`` skips widget resolution entirely.

    Returns a compiled LangGraph graph (same interface as
    ``create_react_agent``'s output). A MemorySaver checkpointer is installed
    by default so paused flows persist across ``invoke`` calls; pass your own
    ``checkpointer`` in production.
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
    return build_react_with_skills(
        llm=llm,
        tools=tools,
        prompt=prompt,
        skills_dir=skills_dir,
        use_flow=True,
        checkpointer=checkpointer,
        mode=mode,
        a2ui=a2ui,
        host=host,
        governed_tools=governed_tools,
        tools_dir=tools_dir,
    )


def wrap_agent(
    agent,
    *,
    skills_dir: str | Path,
    llm=None,
    tools: list | dict | None = None,
    a2ui: bool = True,
    mode: str = "flex",
    checkpointer=None,
    host=None,
    governed_tools: str = "flow_only",
    tools_dir: str | Path | None = None,
):
    """Make an EXISTING LangGraph agent deterministic where it matters.

    ``agent`` is any compiled graph whose state has a ``messages`` key and
    that answers ``invoke({"messages": [...]})`` — the output of
    ``create_react_agent``, or a LangGraph graph of your own. It keeps its
    tools, its prompt and its state; this wrapper adds one deterministic
    boundary in front of it:

    - each new user message is checked against your skills by a structured
      classifier (deterministic *when*, LLM-judged *which*, no regex);
    - a match hands the turn to the flow runtime — typed fields, structural
      gates, correction replay — until the flow completes;
    - everything else (and the conversation after a flow) is delegated to
      your agent untouched. Extra keys on your agent's own state schema are
      passed through and merged back, so nodes keyed on them still work.

        from aigenos_flow.integration import flow_only, wrap_agent

        safe_agent = wrap_agent(my_react_agent, skills_dir="skills/",
                                llm=llm, tools=[bank_transfer])

    **Wrapping does not remove the ungated path.** Classification is a
    judgement call: on a turn it names no skill, the turn goes to your agent,
    and any write still in its tool list is reachable without the flow's
    gates. A compiled graph's tool list cannot be edited from here, so this
    is checked, not repaired:

    - a tool on your agent whose name a skill governs is an error — wrap it
      with :func:`flow_only` (the flow still executes the real tool), drop it
      from your agent, or pass ``governed_tools="allow"``;
    - tools this wrapper cannot vouch for are a warning, because nothing can
      tell that your ``transfer_money`` is the same job as the skill's
      ``bank_transfer``. Wrap those with :func:`flow_only` too.

    ``llm`` powers interpretation/classification for the flows (pass
    ``host=`` instead to reuse a configured host). ``tools`` are tools the
    skills' steps may name — a list or a ``{name: callable}`` dict — next to
    the library in ``<skills_dir>/tools/`` (``tools_dir`` adds a folder kept
    elsewhere). ``a2ui`` behaves exactly as in
    :func:`create_agent`. Pauses interrupt with the same payload; resume with
    ``Command(resume=...)``.
    """
    if governed_tools not in {"flow_only", "allow"}:
        raise ValueError(
            f"governed_tools must be 'flow_only' or 'allow', got {governed_tools!r}"
        )
    _tools_as_items(tools, where="wrap_agent")  # shape check, same error as create_agent
    if host is None:
        if llm is None:
            raise ValueError("wrap_agent needs llm= (or a pre-built host=)")
        host = LangGraphHost(llm=llm)
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
    skills_path = Path(skills_dir)
    if not skills_path.is_dir():
        raise FileNotFoundError(
            f"skills directory not found: {skills_dir} "
            f"(resolved to {skills_path.resolve()})"
        )
    flow_agent = FlowAgent.from_dir(
        skills_path, host=host, mode=mode, tools=tools, tools_dir=tools_dir
    )
    _require_messages_state(agent)
    _check_governed_tools_policy(agent, flow_agent.registry, governed_tools)
    return _wrap_with_flow_nodes(agent, flow_agent, checkpointer, a2ui=a2ui)


def _require_messages_state(agent) -> None:
    """Refuse a wrapped graph whose state has no ``messages`` key.

    The wrapper delegates a turn with ``invoke({"messages": [...]})`` and
    reads the reply back out of ``messages``; a graph keyed on something else
    cannot round-trip. Say so at wrap time instead of raising ``KeyError:
    'messages'`` from inside a node on the first turn.
    """
    schema = getattr(getattr(agent, "builder", None), "state_schema", None)
    if schema is None:
        return
    try:
        hints = typing.get_type_hints(schema, include_extras=True)
    except Exception:  # pragma: no cover
        return
    if hints and "messages" not in hints:
        raise ValueError(
            "wrap_agent needs a graph whose state has a 'messages' key; "
            f"{getattr(schema, '__name__', schema)!r} has "
            f"{sorted(hints)}. Rename the key, or add a thin adapter node "
            "that maps 'messages' onto it."
        )


# ---------------------------------------------------------------------------
# Skill discovery (filesystem walk only — no Python import)
# ---------------------------------------------------------------------------


def _discover_skills(skills_path: Path) -> list[dict]:
    """Walk skills_path. For each subdir with a SKILL.md, return its
    name, description, body, and whether it has a handlers.py file."""
    out: list[dict] = []
    if not skills_path.is_dir():
        return out
    for sub in sorted(skills_path.iterdir()):
        if not sub.is_dir():
            continue
        sm = sub / "SKILL.md"
        if not sm.exists():
            continue
        fm, body = parse_skill_md(sm.read_text(encoding="utf-8"))
        has_handlers = (sub / "handlers.py").exists()
        out.append({
            "name": fm.name,
            "description": fm.description,
            "body": body,
            "has_handlers": has_handlers,
            # A skill is runnable by the deterministic runtime if it has
            # `steps:` (the short form), a declarative `flow:`, or a Python
            # @flow (handlers.py). Instruction-only skills stay body-as-prompt.
            "flow_capable": has_handlers or fm.flow is not None or fm.steps is not None,
            "path": sub,
        })
    return out


def _render_catalog(skills: list[dict]) -> str:
    if not skills:
        return "(no skills available)"
    return "\n".join(f"- **{s['name']}**: {s['description']}" for s in skills)


# ---------------------------------------------------------------------------
# The activate_skill tool — same surface, two behaviors
# ---------------------------------------------------------------------------


def _make_activate_skill_tool(
    skills_meta: list[dict], flow_agent: FlowAgent | None, activation_token: str
):
    """Build the activate_skill tool given the skill catalog.

    Behavior:
      - Unknown skill name → tool error message
      - Skill with handlers.py AND flow_agent present → return Command
        that flips state into flow mode; the outer graph routes to the
        flow node on the same turn
      - Otherwise → return the SKILL.md body to the LLM (body-as-prompt)
    """
    bodies = {s["name"]: s["body"] for s in skills_meta}
    flow_capable = {s["name"] for s in skills_meta if s["flow_capable"]}

    @tool
    def activate_skill(
        skill_name: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Any:
        """Activate a CREATE/MUTATE workflow from the skill catalog.

        Call this ONLY when the user is asking to CREATE, START, FILE,
        SUBMIT, RESERVE, or otherwise INITIATE a new instance of the
        workflow this skill describes — e.g. "book a room", "file an
        expense", "schedule a meeting".

        Do NOT call this for read-only requests like "show me my
        bookings", "list my expenses", "what are my meetings", "view
        appointments" — those have dedicated query tools (e.g.
        show_bookings, show_meeting_rooms). Using activate_skill for a
        read-only request triggers an unwanted new workflow and creates
        duplicates.

        Do NOT re-activate the same skill twice in a row after it just
        finished. If a workflow just completed and the user is now asking
        about results, use a query tool, not activate_skill.

        Args:
            skill_name: Name of the skill to activate.
        """
        if skill_name not in bodies:
            return Command(update={
                "messages": [
                    ToolMessage(
                        content=json.dumps({
                            "error": f"Unknown skill {skill_name!r}",
                            "available": sorted(bodies),
                        }),
                        tool_call_id=tool_call_id,
                    )
                ],
            })

        if flow_agent is not None and skill_name in flow_capable:
            return Command(update={
                "messages": [
                    ToolMessage(
                        content=json.dumps({
                            "activated": skill_name,
                            "mode": "flow",
                            # Minted at build time; the ONLY thing that flips
                            # the graph into flow mode from the ReAct side, so
                            # an inner tool's payload cannot forge one.
                            "aigenos_activation_token": activation_token,
                            "note": "Deterministic flow taking over. Do not reply further this turn.",
                        }),
                        tool_call_id=tool_call_id,
                    )
                ],
                # Inert in the wrapped topology (the inner graph's state has
                # no flow keys — react_node lifts the signed marker instead),
                # kept correct for anyone embedding this tool directly.
                **new_flow_updates(skill_name),
                "history": [],
                "flow_stack": [],
            })

        return bodies[skill_name]

    return activate_skill


# ---------------------------------------------------------------------------
# Graph wrapping — keeps create_react_agent at the core, adds flow nodes
# only when needed
# ---------------------------------------------------------------------------


def _field_widget(flow_agent: FlowAgent, state: dict) -> dict | None:
    """Resolve the widget spec for the field the flow is paused on.

    Resolution order (inside resolve_widget_for_field): a widget pinned on
    the field in SKILL.md → the skill's widget → auto-generated from the
    FieldSpec. Returns a plain dict (the same WidgetSpec JSON the npm
    renderer and the AG-UI/A2UI protocol adapters consume), or None.
    """
    field, flow_id = state.get("awaiting_field"), state.get("flow_id")
    if not field or not flow_id:
        return None
    try:
        from .integrations.flow import resolve_widget_for_field

        spec = resolve_widget_for_field(
            skill=flow_agent.registry.get(flow_id),
            field_name=field,
            state_fields=state.get("fields") or {},
            # What a `pick:` or `widget:` step built for this pause. Without
            # it the client gets a plain text input for the first field the
            # grid would have filled, instead of the grid.
            pending_widget=state.get("pending_widget"),
        )
        return spec.model_dump() if hasattr(spec, "model_dump") else dict(spec)
    except Exception:
        return None  # widget resolution must never break the conversation


def _wrap_with_flow_nodes(
    react_agent,
    flow_agent: FlowAgent,
    checkpointer,
    *,
    a2ui: bool = False,
    activation_token: str | None = None,
):
    """Wrap react_agent into a larger graph that adds a flow node and a
    wait node. Topology:

        START → entry → {react, flow}
        react → conditional → {flow if activated, END otherwise}
        flow  → conditional → {wait if awaiting user, END otherwise}
        wait  → flow

    The ``entry`` node runs an LLM classifier (``interpret.classify_skill``)
    against the latest user message when no flow is mid-flight. If the
    classifier names a skill in the catalog, the runtime force-activates
    it before any ReAct loop runs — turning what would be a stochastic
    tool-call decision into a deterministic ``activate skill X iff the
    catalog says X matches``. The classifier is one structured-output LLM
    call (~150ms), not a regex.

    If the classifier picks no skill, the entry returns ``{}`` and the
    ReAct loop runs as normal — the LLM can still call ``activate_skill``
    itself if it wants to.

    ``activation_token`` is the build-time secret that the ``activate_skill``
    tool (and the governed-tool shims) stamp into their tool result. Only a
    tool result carrying it can flip this graph into flow mode. ``None``
    means "no such tool exists in this topology" (``wrap_agent``), and then
    nothing at all is lifted out of tool results.
    """

    host = flow_agent.host
    registry = flow_agent.registry
    state_schema, extra_keys = _merged_state_schema(react_agent)
    known_flows = {s.name for s in registry.all()}

    def _activation_update(flow_id: str, state) -> dict:
        """Start a fresh flow run. ``runtime.new_flow_updates`` mints the new
        ``flow_run_id`` and carries the effects log over, so activating a
        skill can never erase the record that money already moved."""
        return {
            **new_flow_updates(flow_id, state),
            "history": [],
            "flow_stack": [],
            "flow_hops": 0,
        }

    def entry(state) -> dict:
        # A fresh invoke: the flow node may consume this turn's user message
        # and gets a full continuation budget again.
        if state.get("flow_id") and not state.get("done"):
            return {"flow_hops": 0}  # mid-flow already: no classification needed

        msgs = state.get("messages") or []
        last_user: str | None = None
        for m in reversed(msgs):
            if isinstance(m, HumanMessage):
                last_user = m.content if isinstance(m.content, str) else str(m.content)
                break
        if not last_user:
            return {"flow_hops": 0}

        # A flow has just FINISHED on this thread. Its own description still
        # matches anything said about it ("did my hotel fit the policy?"), so
        # the cold-start classifier below would start it all over again. Ask
        # the question the runtime asks instead: is this a request to start
        # something, or talk about what was done? Talk goes to the agent.
        finished_id = state.get("flow_id") if state.get("done") else None
        has_finished = bool(finished_id and registry.has(finished_id))
        # The same question when a run was cancelled part-way and set aside:
        # "continue" must be able to pick it up, and the classifier below can
        # only ever start something new.
        if has_finished or state.get("parked"):
            decision = decide_outside_flow(
                host=host,
                skills=list(registry.all()),
                user_message=last_user,
                finished=registry.get(finished_id) if has_finished else None,
                fields=state.get("fields") if has_finished else None,
                results=state.get("results") if has_finished else None,
                memory=render_memory(state.get("journal"), None, in_flow=False),
                paused=render_paused(state.get("parked"), in_flow=False),
            )
            if decision is not None:  # None: a scripted host; classify as before
                if decision.action == "resume":
                    restored = resume_updates(state, registry)
                    if restored is None:
                        return {"parked": None, "flow_hops": 0}
                    return {
                        **restored,
                        "flow_hops": 0,
                        "messages": [AIMessage(content="Picking up where you left off.")],
                    }
                if decision.action == "start_skill" and decision.skill:
                    return _activation_update(decision.skill, state)
                return {"flow_hops": 0}

        # The classifier picks the best-matching aigenos flow workflow, or
        # returns None when the user's intent doesn't match any. On None
        # we fall through to ReAct — the LLM can still call activate_skill,
        # and read-only queries (the most common None case) get the right
        # tool from the ReAct loop. Two LLM-driven paths to activation,
        # zero regex.
        skill_name = classify_skill(host=host, user_message=last_user, registry=registry)
        if not skill_name:
            return {"flow_hops": 0}

        # Activate — same shape as the activate_skill tool's Command(update=...).
        return _activation_update(skill_name, state)

    def entry_route(state) -> str:
        if state.get("flow_id") and not state.get("done"):
            return "flow"
        return "react"

    def _react_input(state) -> tuple[list, dict[str, Any]]:
        # The wrapped agent keeps its state: forward every key of its own
        # schema alongside `messages`, and merge back what it returns.
        prev_msgs = list(state.get("messages") or [])
        inner_input: dict[str, Any] = {"messages": prev_msgs}
        for key in extra_keys:
            if key in state:
                inner_input[key] = state[key]
        return prev_msgs, inner_input

    def react_node(state) -> dict:
        prev_msgs, inner_input = _react_input(state)
        return _react_update(state, prev_msgs, react_agent.invoke(inner_input))

    async def areact_node(state) -> dict:
        # An agent with async nodes (an MCP client, an async retriever) only
        # runs under the async API. Driven with ainvoke/astream, this graph
        # delegates the same way, so such an agent can be wrapped at all.
        prev_msgs, inner_input = _react_input(state)
        return _react_update(state, prev_msgs, await react_agent.ainvoke(inner_input))

    def _react_update(state, prev_msgs: list, result) -> dict:
        if not isinstance(result, dict) or "messages" not in result:
            raise ValueError(
                "the wrapped agent returned no 'messages' key "
                f"(got {sorted(result) if isinstance(result, dict) else type(result).__name__}). "
                "wrap_agent needs a graph whose state has a 'messages' key."
            )
        out_msgs = list(result.get("messages") or [])

        # Diff by message id, never by length: an inner agent that trims or
        # summarises its own history SHRINKS the list, and a positional slice
        # would silently drop its reply. New ids are appended; ids that
        # disappeared are mirrored with RemoveMessage so the wrapper's
        # transcript follows the inner agent's own context management.
        before_ids = {m.id for m in prev_msgs if getattr(m, "id", None)}
        before_objs = {id(m) for m in prev_msgs}
        after_ids = {m.id for m in out_msgs if getattr(m, "id", None)}
        new_msgs = [
            m
            for m in out_msgs
            if (m.id not in before_ids if getattr(m, "id", None) else id(m) not in before_objs)
        ]
        removed = [
            RemoveMessage(id=mid) for mid in before_ids if mid not in after_ids
        ]
        update: dict = {"messages": removed + new_msgs}
        for key in extra_keys:
            if key in result:
                update[key] = result[key]

        # Lift the activation a governed-tool shim / activate_skill asked for.
        # Provenance, not content: the payload must carry this build's token,
        # so an ordinary tool result that happens to contain
        # {"mode": "flow", "activated": ...} — third-party API text, an echoed
        # user string — cannot hijack the graph into a money-moving flow.
        if activation_token:
            for m in new_msgs:
                if not isinstance(m, ToolMessage):
                    continue
                try:
                    payload = json.loads(m.content) if isinstance(m.content, str) else {}
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("aigenos_activation_token") != activation_token:
                    if payload.get("mode") == "flow" and payload.get("activated"):
                        log.warning(
                            "ignored an unsigned flow-activation payload in tool "
                            "result %r — only aigenos-flow's own tools may "
                            "activate a skill",
                            m.name,
                        )
                    continue
                activated = payload.get("activated")
                if activated in known_flows:
                    update.update(_activation_update(activated, state))
                    break
        return update

    def react_route(state) -> str:
        if state.get("flow_id") and not state.get("done"):
            return "flow"
        return "end"

    def flow_node(state) -> dict:
        # Backfill the new user message from the message log ONLY when this is
        # a free-form turn. A structured widget submission populates
        # pending_field_updates instead; backfilling its transcript line into
        # pending_user_message would re-feed "confirm: true" through the
        # interpreter and resurrect the confirm-loop the bypass exists to kill.
        # A continuation hop (the flow popped back from a digression and has
        # more steps to replay) must NOT re-feed the last user message — it was
        # already consumed on the hop that started this turn.
        hops = int(state.get("flow_hops") or 0)
        fresh_action = bool(
            state.get("pending_user_message") or state.get("pending_field_updates")
        )
        if not fresh_action and hops == 0:
            msgs = state.get("messages") or []
            for m in reversed(msgs):
                if isinstance(m, HumanMessage):
                    content = m.content if isinstance(m.content, str) else str(m.content)
                    state["pending_user_message"] = content
                    fresh_action = True
                    break

        fired_before = set(state.get("effects_log") or {})
        try:
            run_turn(state, flow_agent.registry, flow_agent.host, mode=flow_agent.mode)
        except Exception:
            # Keep the record of a write that fired before the failure; a step
            # that raises is not checkpointed and a retry would fire it again.
            from .runtime import absorb_step_failure

            if not absorb_step_failure(state, flow_agent.host, fired_before):
                raise
        pending = flow_agent.host.drain_pending_messages()
        new_msgs = [AIMessage(content=p) for p in pending]

        # Widgets: `show:`/tool-result widgets the host collected this turn,
        # plus the input widget for the field the flow paused on. Attached to
        # the last assistant message so any messages-stream consumer sees them.
        drain_w = getattr(flow_agent.host, "drain_pending_widgets", None)
        shown = list(drain_w()) if callable(drain_w) else []
        if a2ui:
            input_widget = _field_widget(flow_agent, state)
            if shown or input_widget:
                if not new_msgs:
                    new_msgs = [AIMessage(content="")]
                kw = dict(new_msgs[-1].additional_kwargs or {})
                if shown:
                    kw["aigenos_widgets"] = shown
                if input_widget:
                    kw["aigenos_widget"] = input_widget
                new_msgs[-1] = AIMessage(content=new_msgs[-1].content, additional_kwargs=kw)
        # runtime.node_updates is the single source of truth for what a flow
        # node checkpoints, including flow_run_id and effects_log, without
        # which one confirmed intent could fire a second side effect on a
        # later turn. Build this dict from it, never by hand.
        return {
            **node_updates(state),
            "messages": new_msgs,
            # Both sides of the conversation, as FlowAgent.as_langgraph keeps
            # them: the in-flow memory reads the assistant's lines from here.
            "history": list(state.get("history", []))
            + [{"role": "assistant", "content": p} for p in pending if p],
            "flow_hops": hops + 1,
        }

    def flow_route(state) -> str:
        if state.get("awaiting_user"):
            return "wait"
        if state.get("done") or not state.get("flow_id"):
            return "end"
        # The flow is still live but is not waiting for anything: it has more
        # steps to replay (this is what popping back from a digression looks
        # like). Keep going in the SAME turn so the turn ends on a real
        # prompt — "back to where we were" with no question and no widget
        # leaves the caller with nothing to render. Bounded, so a runtime that
        # makes no progress can never spin.
        if int(state.get("flow_hops") or 0) < _MAX_FLOW_HOPS:
            return "flow"
        log.warning(
            "flow %r stopped after %d hops without awaiting input or finishing",
            state.get("flow_id"),
            _MAX_FLOW_HOPS,
        )
        return "end"

    def wait_node(state) -> dict:
        msgs = state.get("messages") or []
        last_ai = next(
            (m.content for m in reversed(msgs) if isinstance(m, AIMessage)),
            "Please respond.",
        )
        payload = {
            "prompt": last_ai,
            "field": state.get("awaiting_field"),
            "flow_id": state.get("flow_id"),
        }
        if a2ui:
            payload["widget"] = _field_widget(flow_agent, state)
        user_input = interrupt(payload)
        # Shared routing (runtime.resume_state_updates): dict → structured
        # widget submission (LLM bypass), str → free-form chat. We add a
        # HumanMessage to `messages` so the ReAct message log + UI still see
        # that the user acted; for a dict that's a short transcript line.
        updates = resume_state_updates(user_input)
        # A fresh user action restarts the continuation budget.
        updates = {**updates, "flow_hops": 0}
        if isinstance(user_input, dict):
            transcript = ", ".join(f"{k}: {v}" for k, v in user_input.items())
            return {"messages": [HumanMessage(content=transcript)], **updates}
        return {"messages": [HumanMessage(content=str(user_input))], **updates}

    g: StateGraph = StateGraph(state_schema)
    g.add_node("entry", entry)
    g.add_node("react", RunnableLambda(react_node, afunc=areact_node))
    g.add_node("flow", flow_node)
    g.add_node("wait", wait_node)

    g.add_edge(START, "entry")
    g.add_conditional_edges("entry", entry_route, {"flow": "flow", "react": "react"})
    g.add_conditional_edges("react", react_route, {"flow": "flow", "end": END})
    g.add_conditional_edges(
        "flow", flow_route, {"wait": "wait", "flow": "flow", "end": END}
    )
    g.add_edge("wait", "flow")

    return g.compile(checkpointer=checkpointer)

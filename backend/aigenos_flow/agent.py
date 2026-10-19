"""FlowAgent: loads a skills directory and compiles it to either a
self-contained LangGraph (via ``as_langgraph()``) or a single node you
can plug into an existing graph (``turn()``).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .declarative import AuditSink, ToolRegistry
from .host import Host
from .runtime import (  # noqa: F401 — FlowState re-exported for type hints
    FlowState,
    initial_state,
    node_updates,
    resume_state_updates,
    run_turn,
    absorb_step_failure,
)
from .skills import SkillRegistry, load_skills_dir


#: How many extra ``run_turn`` calls one user turn may make while the flow is
#: still live but waiting for nothing (replaying a suspended flow to its next
#: ask). Bounded so a runtime that makes no progress can never spin. A
#: ``ctx.link()`` to a flow that never pauses costs two (start it, come back),
#: so 3 ended a chain of two such links mid-way with nothing on screen.
_MAX_CONTINUATION_HOPS = 16


def _ungovern(tools: Any) -> Any:
    """Replace ``flow_only`` shims with the real tools they guard.

    ``flow_only`` hands the model a copy that refuses; the flow runtime is the
    one caller entitled to the original. Keeping the unwrap here means the
    same object can be passed to an agent and to ``tools=``.
    """

    def one(t: Any) -> Any:
        meta = getattr(t, "metadata", None) or {}
        if meta.get("aigenos_flow_only"):
            return meta.get("aigenos_flow_only_target", t)
        return t

    if isinstance(tools, dict):
        return {k: one(v) for k, v in tools.items()}
    if isinstance(tools, (list, tuple, set)):
        return [one(t) for t in tools]
    return tools


class FlowAgent:
    """A loaded set of skills plus a host. Knows how to compile itself into
    different framework targets.

    Two response modes:

    - ``"flex"`` (default): the LLM is asked to always include a natural
      conversational Reply alongside any structured items. The agent feels
      like an LLM chatbot whose field-filling flow runs on top.
    - ``"strict"``: the LLM is asked only to interpret items; Reply is
      optional. The agent reads more like a form. Use this when latency,
      cost, or determinism of the assistant's literal words matters.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        host: Host,
        *,
        mode: str = "flex",
    ):
        if mode not in {"flex", "strict"}:
            raise ValueError(f"mode must be 'flex' or 'strict', got {mode!r}")
        self.registry = registry
        self.host = host
        self.mode = mode

    @classmethod
    def from_dir(
        cls,
        skills_dir: Path | str,
        host: Host,
        *,
        mode: str = "flex",
        tools: Any = None,
        audit: AuditSink | None = None,
        allow_empty: bool = False,
        tools_dir: Path | str | None = None,
    ) -> "FlowAgent":
        """Load a skills directory.

        Tools in ``<skills_dir>/tools/`` (and ``tools.py``) are picked up by
        themselves: that folder is the library every skill here can name.
        ``tools_dir`` adds a library kept somewhere else.

        ``tools`` grants the host tools that declarative ``actions:`` may
        reference — a list of callables / LangChain tools, a name->fn dict, or a
        ToolRegistry. Imperative skills (and pure-compute declarative skills)
        need no tools. ``audit`` overrides the default in-process audit sink.

        A tool wrapped with ``aigenos_flow.flow_only`` is unwrapped here: the
        shim exists to keep a model from calling the tool directly, and the
        flow is exactly the caller that is allowed to.

        ``allow_empty`` permits a directory that contains no skills at all —
        for a server whose first skill has not been authored yet. A directory
        holding skills that all FAILED to load still raises either way: that is
        a broken deployment, not an empty one.
        """
        registry = load_skills_dir(
            skills_dir,
            tool_registry=ToolRegistry.from_tools(_ungovern(tools)),
            audit=audit,
            tools_dir=tools_dir,
        )
        if len(registry) == 0:
            failed = getattr(registry, "errors", {}) or {}
            if failed:
                detail = "; ".join(f"{name}: {err}" for name, err in failed.items())
                raise ValueError(
                    f"no skill in {skills_dir!r} could be loaded — "
                    f"{len(failed)} failed: {detail}"
                )
            if not allow_empty:
                raise ValueError(
                    f"no skills found in {skills_dir!r}. Point it at a folder of "
                    "SKILL.md directories, or pass allow_empty=True if starting "
                    "with none is intended."
                )
        return cls(registry=registry, host=host, mode=mode)

    def list_skills(self) -> list[str]:
        return [s.name for s in self.registry.all()]

    # ----------------------------------------------------------------- LangGraph
    def as_langgraph(self) -> Any:
        """Compile to a LangGraph CompiledStateGraph.

        Topology: START -> turn -> (done -> END) or (awaiting_user -> wait -> turn).

        The turn/wait split is required so messages emitted before
        interrupt() are checkpointed (interrupt raises before the node
        returns, so a single node would lose them).
        """
        try:
            from langgraph.graph import END, START, StateGraph
            from langgraph.types import interrupt
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "as_langgraph() requires `langgraph` to be installed. "
                "pip install langgraph"
            ) from e

        registry = self.registry
        host = self.host
        mode = self.mode

        turn = self.turn

        def turn_node(state: FlowState) -> dict[str, Any]:
            # FlowAgent.turn replays to the flow's next pause, so a pop back
            # from a digression ends the turn on a real question instead of
            # "back to where we were" with nothing to render.
            work = dict(state)
            fired_before = set(work.get("effects_log") or {})
            try:
                work = turn(work)  # type: ignore[arg-type]
            except Exception:
                # A failed step is not checkpointed. If a write fired before the
                # failure, keep its record instead of raising (see
                # runtime.absorb_step_failure), or a retry fires it again.
                if not absorb_step_failure(work, host, fired_before):  # type: ignore[arg-type]
                    raise
            state = work  # type: ignore[assignment]
            pending = host.drain_pending_messages()
            # runtime.node_updates is the single source of truth for what a
            # flow node checkpoints — including chat_config and the
            # flow_run_id/effects_log at-most-once guard — so this builder and
            # the ReAct-embedded one in integration.py cannot drift apart.
            updates: dict[str, Any] = {
                **node_updates(state),
                "history": state.get("history", []),
                "pending_assistant_messages": pending,
            }
            if pending:
                updates["history"] = list(updates["history"]) + [
                    {"role": "assistant", "content": p} for p in pending
                ]
            return updates

        def wait_node(state: FlowState) -> dict[str, Any]:
            pending = state.get("pending_assistant_messages") or []
            prompt = (
                pending[-1]
                if pending
                else f"Please provide {state.get('awaiting_field', 'input')}."
            )
            user_input = interrupt(
                {
                    "prompt": prompt,
                    "messages": list(pending),
                    "field": state.get("awaiting_field"),
                    "flow_id": state.get("flow_id"),
                }
            )
            # Shared routing: dict → structured widget submission (LLM bypass),
            # str → free-form message. See runtime.resume_state_updates.
            return {**resume_state_updates(user_input), "pending_assistant_messages": []}

        def route(state: FlowState) -> str:
            if state.get("done"):
                return "end"
            if state.get("awaiting_user"):
                return "wait"
            return "end"

        graph = StateGraph(FlowState)
        graph.add_node("turn", turn_node)
        graph.add_node("wait", wait_node)
        graph.add_edge(START, "turn")
        graph.add_conditional_edges("turn", route, {"wait": "wait", "end": END})
        graph.add_edge("wait", "turn")

        return graph.compile(
            checkpointer=getattr(host, "checkpointer", None),
        )

    # ----------------------------------------------------------------- bare runtime
    def turn(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run one user turn, up to the flow's next pause.

        No LangGraph required — useful for tests and for embedding into any
        framework that doesn't provide a checkpointed graph runtime. The
        caller is responsible for persisting ``state`` between turns.

        "Up to the next pause" is load-bearing: a step that resumes a
        suspended flow (popping back from a digression) leaves the flow live,
        waiting for nothing and not done. The turn keeps replaying until the
        flow asks the user something or finishes, so the caller always ends a
        turn with something to render.
        """
        state = run_turn(state, self.registry, self.host, mode=self.mode)  # type: ignore[arg-type]
        for _ in range(_MAX_CONTINUATION_HOPS):
            if state.get("awaiting_user") or state.get("done") or not state.get("flow_id"):
                break
            # A continuation hop must not re-feed the message this turn already
            # consumed — it would be interpreted a second time.
            state["pending_user_message"] = None
            state["pending_field_updates"] = None
            state = run_turn(state, self.registry, self.host, mode=self.mode)  # type: ignore[arg-type]
        return state

    def session(self, flow: str | None = None) -> "FlowSession":
        """Start a stateful conversation — ``turn()`` without the plumbing.

        ``flow`` pins the session to one skill (skipping activation). Omit it
        to let the interpreter route the first message.
        """
        return FlowSession(self, flow)


class FlowSession:
    """A conversation with one FlowAgent that reads like a conversation.

    Wraps the state-dict threading of ``FlowAgent.turn()``:

        chat = agent.session("transfer-funds")
        chat.user("send $5000 to mom")           # free text → interpreted
        chat.submit(verification_code="481920")  # widget submit → no LLM at all

        chat.awaiting   # field the flow is paused on (None when not paused)
        chat.spec       # that field's FieldSpec — drive YOUR widget from it
        chat.replies    # assistant lines produced by the last turn
        chat.done       # flow completed?
        chat.state      # the underlying FlowState — persist it if you like

    Both entry points return ``self``, so scripted drives chain:
    ``chat.user("...").submit(confirm=True)``.
    """

    def __init__(self, agent: FlowAgent, flow: str | None = None) -> None:
        self._agent = agent
        self.state: dict[str, Any] = initial_state()
        if flow is not None:
            agent.registry.get(flow)  # fail fast on a typo'd skill name
            self.state["flow_id"] = flow
        self.replies: list[str] = []

    # ---- the two ways a user acts ------------------------------------
    def user(self, text: str) -> "FlowSession":
        """A free-text user message — interpreted by the LLM into commands."""
        self.state["pending_user_message"] = text
        return self._run()

    def submit(self, **fields: Any) -> "FlowSession":
        """A structured widget submission — validated in pure Python,
        bypassing the LLM entirely. Resubmitting a field is a correction and
        re-arms any gate that depends on it."""
        self.state["pending_field_updates"] = dict(fields)
        return self._run()

    def _run(self) -> "FlowSession":
        self.state = self._agent.turn(self.state)
        drain = getattr(self._agent.host, "drain_pending_messages", None)
        self.replies = list(drain()) if callable(drain) else []
        # Both sides of the conversation go in the history (as the LangGraph
        # node does), so "what did you just ask me?" has something to read.
        if self.replies:
            self.state["history"] = list(self.state.get("history") or []) + [
                {"role": "assistant", "content": r} for r in self.replies if r
            ]
        return self

    # ---- what the UI needs after each turn ---------------------------
    @property
    def awaiting(self) -> str | None:
        return self.state.get("awaiting_field")

    @property
    def spec(self) -> Any:
        field, flow_id = self.awaiting, self.state.get("flow_id")
        if not field or not flow_id:
            return None
        return self._agent.registry.get(flow_id).fields.get(field)

    @property
    def approval(self) -> Any:
        """The request this run has waiting with someone else, if it has one
        (``confirm:`` with ``by:``). An ``ApprovalRequest``: who is asked, the
        question, what a yes will run."""
        from .approvals import store_for

        run = str(self.state.get("flow_run_id") or "")
        return next((r for r in store_for(self._agent.host).for_run(run) if r.status == "pending"), None)

    def decide(self, approved: bool, *, by: str = "", note: str = "") -> "FlowSession":
        """The other person answers, and the flow takes its next turn. For tests
        and scripts: in a deployment the answer comes from wherever requests are
        shown to the people who decide them."""
        from .approvals import store_for

        request = self.approval
        if request is None:
            raise RuntimeError("nothing is waiting for someone else's yes")
        from .runtime import LOOK_IN

        store_for(self._agent.host).decide(request.id, approved, by=by, note=note)
        self.state["pending_field_updates"] = {LOOK_IN: request.id}
        return self._run()

    @property
    def widget(self) -> dict[str, Any] | None:
        """The widget the flow built for this pause, when it built one: a list
        to pick from, a designed widget with its data, or the card a
        confirmation shows (which tool a yes will run, and with which inputs)."""
        return self.state.get("pending_widget")

    @property
    def done(self) -> bool:
        return bool(self.state.get("done"))

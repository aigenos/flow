"""One-turn runtime: interpret the user message, mutate state, re-execute
the flow until it pauses or completes.

The three public surfaces are ``FlowState`` (the TypedDict checkpointed by
LangGraph), ``apply_items`` (applies interpreter output to state), and
``run_turn`` (the entrypoint).
"""
from __future__ import annotations

import logging
import math
import re
import uuid
from typing import Any, TypedDict

from .context import (
    ChatMode,
    Context,
    FlowDone,
    FlowLink,
    NeedField,
    NeedFields,
    NeedWidget,
)
from .engine.safe_eval import eval_strict
from .fields import FieldSpec, FieldValidationError, coerce_and_validate
from .host import Host
from .interpret import (
    Cancel,
    Chitchat,
    Correct,
    OutOfScope,
    Reopen,
    Reply,
    Restart,
    Resume,
    SetField,
    StartFlow,
    _needs_pending_ask,
    _render_results,
    interpret_turn,
    render_memory,
    render_paused,
)
from .skills import Skill, SkillRegistry

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class Message(TypedDict):
    role: str  # "user" | "assistant" | "system"
    content: str


class FlowStackFrame(TypedDict, total=False):
    """A suspended flow while a digression is active.

    ``flow_run_id`` and ``effects_log`` ride along because they belong to the
    flow RUN, not to the conversation: without them a digression (or any flow
    switch) handed the parent back a blank no-double-spend record, and one
    confirmed intent could fire its side effect twice.
    """

    flow_id: str
    fields: dict[str, Any]
    results: dict[str, Any]
    say_cursor: int
    flow_run_id: str | None
    effects_log: dict[str, Any]
    # ctx.link(): the links this flow has already taken, by position, as
    # {"flow": target, "completed": bool}, and the target it is suspended on
    # now (None for a model-driven digression).
    links_done: list[dict[str, Any]]
    pending_link: str | None


class FlowState(TypedDict, total=False):
    """The state schema every node sees. ``total=False`` because LangGraph's
    reducers merge partial updates — nodes return changed keys only."""

    flow_id: str | None
    fields: dict[str, Any]
    results: dict[str, Any]
    say_cursor: int
    # The ctx.link() calls the active flow has already taken, by position:
    # {"flow": target, "completed": True if it did its job (not cancelled, not declined)}.
    links_done: list[dict[str, Any]]

    field_history: dict[str, list[Any]]

    history: list[Message]
    pending_user_message: str | None
    # Structured widget submissions land here directly — no LLM interpretation,
    # no string round-trip. Each (k, v) is coerced + applied as SetField.
    pending_field_updates: dict[str, Any] | None
    pending_assistant_messages: list[str]
    # When a flow pauses on ctx.widget(), the (data-bound) widget payload to
    # render: {widget, data, fields, submit_schema}. None when not awaiting a
    # widget. The frontend renders it and posts the `fields` back as a structured
    # pending_field_updates submission.
    pending_widget: dict[str, Any] | None

    awaiting_user: bool
    awaiting_field: str | None
    pending_fields: list[str] | None
    flow_stack: list[FlowStackFrame]
    done: bool

    # Set while the flow is paused on its `chat:` step (the scoped agentic
    # leaf): {tools, pin, greeting, system}. Cleared at the top of every
    # replay and re-set only if the flow reaches the chat step again — so a
    # correction that re-opens an earlier gate automatically exits chat mode.
    chat_config: dict[str, Any] | None

    # Stable id for ONE flow run (set at flow start, constant across its turns).
    # Keys idempotent side effects so a re-run after a correction can't double-fire.
    flow_run_id: str | None
    # Sticky record of fired side effects, keyed "action:idempotency_key". Unlike
    # ``results`` this is NOT cleared on a correction, so a post-fire correction
    # cannot re-trigger an already-executed effect (the double-spend guard).
    effects_log: dict[str, Any]
    # What each confirmation card showed, as "<flow_run_id>:<field>" ->
    # {action: {input: value}}. A released tool is called with those inputs or
    # not at all (declarative.py). Keyed by run, so a flow that ran in between
    # cannot overwrite another's entry.
    approvals: dict[str, Any]

    # WHAT HAPPENED, as opposed to what runs. ``fields`` / ``results`` drive the
    # flow and belong to one run: a cancel ends them. These two outlive the run.
    #
    # ``journal``: one entry per workflow run that ended, was declined, was
    # cancelled or was restarted (see ``_journal``). Read-only memory for the
    # model, so "who did I pick last time?" has an answer after a cancel.
    journal: list[dict[str, Any]]
    # ``parked``: the run the user most recently cancelled part-way, kept so
    # "continue" can pick it up (see ``_park`` / ``_restore_parked``). It never
    # holds a secret or an approval that has not been used: those are asked for
    # again.
    parked: dict[str, Any] | None

    user_data: dict[str, Any]


def initial_state() -> FlowState:
    return {
        "flow_id": None,
        "fields": {},
        "results": {},
        "say_cursor": 0,
        "links_done": [],
        "field_history": {},
        "history": [],
        "pending_user_message": None,
        "pending_field_updates": None,
        "pending_assistant_messages": [],
        "pending_widget": None,
        "awaiting_user": False,
        "awaiting_field": None,
        "pending_fields": None,
        "flow_stack": [],
        "done": False,
        "chat_config": None,
        "flow_run_id": None,
        "effects_log": {},
        "approvals": {},
        "journal": [],
        "parked": None,
        "user_data": {},
    }


def resume_state_updates(user_input: Any) -> dict[str, Any]:
    """Map a LangGraph ``interrupt()`` resume value into FlowState updates.

    This is the SINGLE source of truth for how a resumed turn is routed, so
    the standalone graph (``FlowAgent.as_langgraph``) and the ReAct-embedded
    graph (``integration.build_react_with_skills``) cannot drift apart — a
    past divergence here is exactly what let widget submissions loop.

    Two resume shapes:
      - dict  → structured widget submission. Goes to ``pending_field_updates``
        and bypasses the LLM interpreter entirely (deterministic by nature;
        round-tripping it through the model is what caused the confirm-loop).
      - str   → free-form chat. Goes to ``pending_user_message`` and through
        the normal ``interpret_turn`` path.

    Both shapes always reset the *other* pending key to None so a stale value
    from a prior turn can't re-fire (FlowState keys have no LangGraph reducer —
    a key omitted from a node's return is left untouched in the checkpoint).
    """
    if isinstance(user_input, dict):
        return {
            "pending_field_updates": dict(user_input),
            "pending_user_message": None,
            "awaiting_user": False,
        }
    return {
        "pending_user_message": str(user_input),
        "pending_field_updates": None,
        "awaiting_user": False,
    }


#: How many fired-effect records one conversation keeps. The log is append-only
#: within a conversation (see ``reset_for_new_flow``), so it needs a ceiling;
#: oldest entries fall off first.
_EFFECTS_LOG_MAX = 256


#: A resume payload of ``{LOOK_IN: <request id>}`` answers nothing. It is how a
#: chat that is waiting on someone else's yes asks the flow to take its next
#: turn once that yes has arrived. (An empty payload cannot be used for this:
#: LangGraph reads ``Command(resume={})`` as "resume nothing".)
LOOK_IN = "_approval"

#: Every FlowState key a graph node MUST return for the runtime's guarantees to
#: survive checkpointing. LangGraph leaves a key a node omits untouched only if
#: the state schema declares a channel for it; a key the schema does not declare
#: is dropped outright, which is how ``create_agent`` / ``wrap_agent`` silently
#: lost the at-most-once guard. Both graph builders derive their node return
#: from ``node_updates`` so they cannot drift apart again.
CHECKPOINTED_KEYS: tuple[str, ...] = (
    "flow_id",
    "fields",
    "results",
    "say_cursor",
    "links_done",
    "field_history",
    "awaiting_user",
    "awaiting_field",
    "pending_fields",
    "flow_stack",
    "done",
    # Chat-leaf routing is read on the NEXT turn.
    "chat_config",
    "pending_widget",
    # The at-most-once guard. One flow run = one flow_run_id for its whole
    # lifetime; effects_log records what already fired under it.
    "flow_run_id",
    "effects_log",
    # What each confirmation showed: read when the tool it released is called.
    "approvals",
    # The conversation's memory. Neither is reset when a flow starts or ends.
    "journal",
    "parked",
)

#: Defaults for the run-scoped keys, so ``node_updates`` never returns a bare
#: ``None`` where a dict is expected.
_KEY_DEFAULTS: dict[str, Any] = {
    "fields": {},
    "results": {},
    "say_cursor": 0,
    "links_done": [],
    "field_history": {},
    "flow_stack": [],
    "effects_log": {},
    "approvals": {},
    "journal": [],
    "awaiting_user": False,
    "done": False,
}


def node_updates(state: FlowState) -> dict[str, Any]:
    """The checkpoint payload for one flow node, as a plain dict.

    THE single source of truth for what a graph node writes back, shared by
    ``FlowAgent.as_langgraph`` and the ReAct-embedded graph. The two pending
    inputs are always cleared: ``run_turn`` consumes them on the state object,
    but the node's return dict is what LangGraph checkpoints, so omitting them
    leaves a stale submission to re-fire next turn.
    """
    out: dict[str, Any] = {}
    for key in CHECKPOINTED_KEYS:
        default = _KEY_DEFAULTS.get(key)
        value = state.get(key, default)
        if default is not None and value is None:
            value = default
        if key in ("awaiting_user", "done"):
            value = bool(value)
        out[key] = value
    out["pending_user_message"] = None
    out["pending_field_updates"] = None
    return out


def new_flow_updates(flow_id: str, state: FlowState | None = None) -> dict[str, Any]:
    """The state-update dict that starts a fresh flow run, for graph nodes that
    activate a skill by returning updates rather than mutating state (the
    ``activate_skill`` tool and the classify node in ``integration.py``).

    A FRESH ``flow_run_id`` is the load-bearing part. An activation that resets
    ``fields`` but leaves the previous run's id in the checkpoint would key the
    new run's side effect to the old run's effects-log entry, and the new,
    legitimate transfer would be silently deduped away. Existing log entries are
    carried over on purpose: an author-chosen ``idempotency_key`` is meant to
    dedupe across runs, and a default key is the fresh id, so nothing collides.
    """
    return {
        "flow_id": flow_id,
        "fields": {},
        "results": {},
        "say_cursor": 0,
        "links_done": [],
        "field_history": {},
        "awaiting_user": False,
        "awaiting_field": None,
        "pending_fields": None,
        "done": False,
        "chat_config": None,
        "pending_widget": None,
        "flow_run_id": uuid.uuid4().hex,
        "effects_log": dict((state or {}).get("effects_log") or {}),
    }


def resume_updates(state: FlowState, registry: SkillRegistry) -> dict[str, Any] | None:
    """The state-update dict that puts the parked run back, for graph nodes that
    resume by returning updates rather than mutating state (the entry node in
    ``integration.py``). ``None`` when there is nothing left to restore.
    """
    work: FlowState = dict(state)  # type: ignore[assignment]
    work["flow_stack"] = list(work.get("flow_stack") or [])
    if not _restore_parked(work, registry):
        return None
    return node_updates(work)


def reset_for_new_flow(state: FlowState, flow_id: str) -> None:
    """Mutate ``state`` in place to start a new flow. Does NOT clear history
    or user_data — only flow-local execution state."""
    state["flow_id"] = flow_id
    state["fields"] = {}
    state["results"] = {}
    state["say_cursor"] = 0
    state["links_done"] = []
    state["field_history"] = {}
    state["awaiting_user"] = False
    state["awaiting_field"] = None
    state["pending_fields"] = None
    state["done"] = False
    state["chat_config"] = None
    # A fresh run id per flow run. flow_run_id is stable across the run's turns
    # so an idempotency key keeps deduping the side effect.
    state["flow_run_id"] = uuid.uuid4().hex
    # The effects log is CARRIED OVER, never replaced. Every entry is keyed by
    # the action's idempotency key (the fresh flow_run_id by default), so a new
    # run can still fire its own effect — but starting a second flow can no
    # longer erase the record that the first one's money already moved. Wiping
    # it here is what let "transfer -> check balance -> correct -> re-confirm"
    # spend twice.
    log = dict(state.get("effects_log") or {})
    if len(log) > _EFFECTS_LOG_MAX:
        drop = len(log) - _EFFECTS_LOG_MAX
        for k in list(log)[:drop]:
            log.pop(k, None)
    state["effects_log"] = log


# ---------------------------------------------------------------------------
# Memory: the record of what happened, and the run a cancel set aside
# ---------------------------------------------------------------------------
#
# A flow is deterministic because the state that drives it is scoped to one
# run: a cancel ends that run and nothing of it can act later. Forgetting that
# the run ever happened is a different thing, and it made the product look
# broken ("who did I pick?" answered with the flow's first question). So two
# things outlive a run, and neither can cause an action:
#
#   journal  what each run came to. The model reads it to answer questions.
#   parked   the run the user last cancelled part-way, restored only when the
#            user asks to continue, and restored without its approvals.

_JOURNAL_MAX = 8


def _shown_fields(skill: Skill | None, fields: dict[str, Any] | None) -> dict[str, Any]:
    """Collected values as the model may read them: secrets hidden, blanks dropped."""
    out: dict[str, Any] = {}
    for name, value in (fields or {}).items():
        if value is None:
            continue
        spec = (skill.fields or {}).get(name) if skill is not None else None
        if getattr(spec, "sensitive", False):
            out[name] = "(hidden)"
        elif isinstance(value, float) and value.is_integer():
            out[name] = int(value)  # 500, not 500.0: the model repeats what it reads
        else:
            out[name] = value
    return out


def _fired(run_id: Any, log: dict[str, Any] | None) -> bool:
    """Has a write gone through under this run id? (The default idempotency key.)"""
    rid = str(run_id or "")
    return bool(rid) and any(rid in str(k) for k in (log or {}))


def _journal(state: FlowState, skill: Skill, outcome: str, *, waiting_for: str | None = None) -> None:
    """Record what the active run came to: ended | declined | cancelled | restarted."""
    entry = {
        "flow": skill.name,
        "run": state.get("flow_run_id"),
        "outcome": outcome,
        "fields": _shown_fields(skill, state.get("fields")),
        "results": _render_results(state.get("results") or {}, budget=1500, each=420),
        "handoffs": [dict(h) for h in (state.get("links_done") or [])],
        "waiting_for": waiting_for,
        "wrote": outcome in ("cancelled", "restarted")
        and _fired(state.get("flow_run_id"), state.get("effects_log")),
    }
    # One entry per run: a finished flow that is replayed (an edit on its
    # review card) updates its entry instead of adding a second one.
    journal = [
        e for e in (state.get("journal") or [])
        if not (e.get("run") == entry["run"] and e.get("flow") == entry["flow"])
    ]
    journal.append(entry)
    state["journal"] = journal[-_JOURNAL_MAX:]


def _memory(
    state: FlowState,
    *,
    in_flow: bool,
    finished_run: str | None = None,
    skill: Skill | None = None,
) -> tuple[str, str]:
    """The (record, paused task) prompt sections for this state.

    After a finished flow the caller already shows that run in full with the
    end of the conversation, so both are left out here.
    """
    journal = state.get("journal") or []
    history = state.get("history")
    if finished_run is not None:
        journal = [e for e in journal if e.get("run") != finished_run]
        history = None
    # Inside a flow the model also reads what this run has looked up so far, so
    # "who did I pick?" is answered with the name on the list and not its id.
    current = ""
    changed: dict[str, list[Any]] = {}
    if in_flow and state.get("results"):
        current = _render_results(state.get("results") or {}, budget=1500, each=420)
    if in_flow and skill is not None:
        # Every value a field has held in this run, minus the one it holds now.
        fields = state.get("fields") or {}
        for name, values in (state.get("field_history") or {}).items():
            if name not in (skill.fields or {}) or _needs_pending_ask(skill, name):
                continue  # never a secret, and a yes/no approval is not a "choice"
            before = [v for v in dict.fromkeys(values) if v != fields.get(name)]
            if before:
                changed[name] = before[-3:]
    return (
        render_memory(journal, history, in_flow=in_flow, current=current, changed=changed),
        render_paused(state.get("parked"), in_flow=in_flow),
    )


def _errand_chain(state: FlowState) -> list[FlowStackFrame]:
    """The frames directly under the active flow that reached it by ctx.link().

    Together with the active flow they are ONE errand, however many skills it
    is made of. A flow suspended under a side trip is not part of it: the user
    left it waiting, they did not hand it off.
    """
    stack = state.get("flow_stack") or []
    n = 0
    for frame in reversed(stack):
        if not frame.get("pending_link"):
            break
        n += 1
    return stack[len(stack) - n:] if n else []


def _spent_approvals(skill: Skill, results: dict[str, Any]) -> set[str]:
    """Confirmations whose every gated write has already run in this flow."""
    gated: dict[str, list[str]] = {}
    for name, fn in (getattr(skill, "actions", None) or {}).items():
        requires = getattr(fn, "_flow_effect_requires", None) or getattr(fn, "_flow_action_requires", None)
        if requires:
            gated.setdefault(requires, []).append(name)
    return {field for field, actions in gated.items() if all(a in results for a in actions)}


def _for_parking(skill: Skill | None, frame: dict[str, Any], log: dict[str, Any]) -> dict[str, Any]:
    """One flow of a run being set aside, without what must be asked for again.

    A parked run never holds a secret, and never an approval that has not been
    used: "never mind" withdraws a yes. So every code is dropped, and every
    confirmation except one whose write has already gone through (asking for
    that again would be asking the user to approve something that is done).

    If nothing of this flow has gone through yet, everything computed so far is
    dropped with them, so picking it up shows the summary again before it asks
    for the yes. A flow that has already written keeps its results: they are
    what stops the write from being attempted twice.
    """
    out = {
        "flow_id": frame.get("flow_id"),
        "fields": dict(frame.get("fields") or {}),
        "results": dict(frame.get("results") or {}),
        "say_cursor": int(frame.get("say_cursor") or 0),
        "links_done": list(frame.get("links_done") or []),
        "pending_link": frame.get("pending_link"),
        "flow_run_id": frame.get("flow_run_id"),
    }
    if skill is None:
        return out
    fired = _fired(out["flow_run_id"], log)
    spent = _spent_approvals(skill, out["results"]) if fired else set()
    drop = [
        n for n, v in out["fields"].items()
        if v is not None and _needs_pending_ask(skill, n) and n not in spent
    ]
    for name in drop:
        out["fields"].pop(name, None)
    if drop and not fired:
        out["results"] = {}
        out["say_cursor"] = 0
    return out


def _park(state: FlowState, registry: SkillRegistry) -> None:
    """Set the run being cancelled aside, so "continue" can pick it up.

    Called before the cancel unwinds anything. Covers the whole errand (the
    active flow and the flows that handed off to it). A run with nothing
    entered is not kept: there is nothing to come back to, and it must not
    replace an earlier one that had something.
    """
    def skill_of(name: Any) -> Skill | None:
        return registry.get(name) if name and registry.has(name) else None

    log = state.get("effects_log") or {}
    active = _for_parking(
        skill_of(state.get("flow_id")),
        {
            "flow_id": state.get("flow_id"),
            "fields": state.get("fields"),
            "results": state.get("results"),
            "say_cursor": state.get("say_cursor"),
            "links_done": state.get("links_done"),
            "flow_run_id": state.get("flow_run_id"),
        },
        log,
    )
    frames = [_for_parking(skill_of(f.get("flow_id")), dict(f), log) for f in _errand_chain(state)]
    if not frames and not any(v is not None for v in active["fields"].values()):
        return
    shown: dict[str, Any] = {}
    for part in [*frames, active]:
        shown.update(_shown_fields(skill_of(part["flow_id"]), part["fields"]))
    state["parked"] = {
        **active,
        "frames": frames,
        "root": frames[0]["flow_id"] if frames else active["flow_id"],
        "waiting_for": state.get("awaiting_field"),
        "shown": shown,
    }


def _restore_parked(state: FlowState, registry: SkillRegistry) -> bool:
    """Make the parked run the active one again. False if it cannot be.

    Restoring cannot fire anything: the run comes back paused, the replay stops
    at its first unanswered question, and a write is still behind its own
    confirmation, which was not kept. A write that had already gone through is
    in the effects log under the same run id, so it does not go through twice.
    """
    parked = state.get("parked")
    if not parked:
        return False
    state["parked"] = None
    frames = [dict(f) for f in (parked.get("frames") or [])]
    names = [f.get("flow_id") for f in frames] + [parked.get("flow_id")]
    if not all(n and registry.has(n) and registry.get(n).flow_fn is not None for n in names):
        return False  # the skills changed since it was parked

    stack = list(state.get("flow_stack") or [])
    if state.get("flow_id") and not state.get("done"):
        # Something is running. Normally it waits underneath, like any flow
        # under a side trip. Two cases where it is replaced instead: it is the
        # very run being restored (a later point of it is still open), or it
        # has nothing in it (the user started again, then asked to go back).
        chain = _errand_chain(state)
        run_ids = {f.get("flow_run_id") for f in frames} | {parked.get("flow_run_id")}
        live_ids = {f.get("flow_run_id") for f in chain} | {state.get("flow_run_id")}
        entered = any(v is not None for v in (state.get("fields") or {}).values()) or any(
            v is not None for f in chain for v in (f.get("fields") or {}).values()
        )
        if (live_ids & run_ids) or not entered:
            if chain:
                del stack[len(stack) - len(chain):]
        else:
            state["flow_stack"] = stack
            _push_stack_frame(state)
            stack = list(state["flow_stack"])

    state["flow_stack"] = stack + frames
    state["flow_id"] = parked["flow_id"]
    state["fields"] = dict(parked.get("fields") or {})
    state["results"] = dict(parked.get("results") or {})
    state["say_cursor"] = int(parked.get("say_cursor") or 0)
    state["links_done"] = list(parked.get("links_done") or [])
    state["flow_run_id"] = parked.get("flow_run_id") or uuid.uuid4().hex
    state["field_history"] = {}
    state["awaiting_user"] = False
    state["awaiting_field"] = None
    state["pending_fields"] = None
    state["done"] = False
    state["chat_config"] = None
    state["pending_widget"] = None
    # Another flow may have run since: what its lists show is read again.
    _refresh_reads(state, registry.get(state["flow_id"]))
    return True


def _resume_parked(state: FlowState, registry: SkillRegistry, host: Host) -> bool:
    """Restore the parked run and say so. False (and says so) if it is gone."""
    if _restore_parked(state, registry):
        host.emit("Picking up where you left off.")
        return True
    host.emit("That is no longer there to pick up. Tell me what you would like to do.")
    return False


def _read_into_resumed(
    state: FlowState, skill: Skill, host: Host, user_message: str, mode: str
) -> None:
    """Apply what the resuming message CHANGES, and nothing else.

    "continue, but make it 600" and "actually send it to someone else" both ask
    to go back and to change something. The message is read against the resumed
    flow and only its value changes are kept. Its reply and anything that would
    move the conversation again are dropped: the flow's next question is the
    reply. Nothing is being asked at this point, so a confirmation or a code in
    the message cannot land (``_filter_must_elicit``).
    """
    interp = interpret_turn(
        host=host, skill=skill, user_message=user_message,
        current_fields=state.get("fields", {}), mode=mode, digressions=[],
        awaiting_field=None, pending_fields=None,
    )
    changes = [it for it in interp.items if isinstance(it, (SetField, Correct, Reopen))]
    if changes:
        apply_items(state, changes, skill, host, mode="strict")


def _restart(state: FlowState, registry: SkillRegistry, skill: Skill, host: Host) -> Skill:
    """Begin the errand in hand again from its first question.

    The errand, not just the active skill: "start over" said while a payee is
    being added inside send-money goes back to send-money's first question.
    A new run id comes with it, so the fresh run is gated like any other; what
    the dropped run had already written stays written, and the user is told.
    """
    chain = _errand_chain(state)
    root = chain[0].get("flow_id") if chain else state.get("flow_id")
    log = state.get("effects_log") or {}
    fired = any(
        _fired(rid, log)
        for rid in [state.get("flow_run_id"), *(f.get("flow_run_id") for f in chain)]
    )
    _journal(state, skill, "restarted", waiting_for=state.get("awaiting_field"))
    if chain:
        stack = list(state.get("flow_stack") or [])
        del stack[len(stack) - len(chain):]
        state["flow_stack"] = stack
    reset_for_new_flow(state, root)  # type: ignore[arg-type]
    host.emit("Okay, starting over." + (" What already went through stays done." if fired else ""))
    return registry.get(root)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Apply interpretation items to state
# ---------------------------------------------------------------------------


def _turn_outside_flow(
    state: FlowState,
    registry: Any,
    host: Host,
    user_message: str,
    *,
    finished: Skill | None,
) -> tuple[bool, str | None, bool]:
    """Handle one message that arrives with no workflow running.

    Returns ``(handled, start, resume)``:

    * ``(True, None, False)`` — a conversational reply was emitted; the turn is over.
    * ``(False, "skill-name", False)`` — the user asked to start that workflow.
    * ``(False, None, True)`` — the user asked to go back to the parked run.
    * ``(False, None, False)`` — this host cannot decide (a scripted test host);
      the caller keeps its previous behaviour.

    Nothing here can cause a side effect: the reply path has no tools, starting
    a workflow only ever enters it at its first step, behind its gates, and a
    resumed one comes back without its approvals (see ``_restore_parked``).
    """
    from .interpret import decide_outside_flow  # local to avoid cycles at import

    memory, paused = _memory(
        state, in_flow=False,
        finished_run=state.get("flow_run_id") if finished is not None else None,
    )
    decision = decide_outside_flow(
        host=host,
        skills=list(registry.all()),
        user_message=user_message,
        finished=finished,
        fields=state.get("fields") if finished is not None else None,
        results=state.get("results") if finished is not None else None,
        handoffs=state.get("links_done") if finished is not None else None,
        history=state.get("history") if finished is not None else None,
        memory=memory,
        paused=paused,
    )
    if decision is None:
        return False, None, False
    if decision.action == "resume":
        return False, None, True
    if decision.action == "start_skill" and decision.skill:
        return False, decision.skill, False

    state.setdefault("history", []).append({"role": "user", "content": user_message})
    host.emit((decision.reply or "").strip() or "Sure — what would you like to do next?")
    state["pending_user_message"] = None
    return True, None, False


def _rejection_text(name: str, reason: str, spec: Any = None) -> str:
    """What the END USER sees when a value fails validation.

    Names the field in words ("verification code", never ``verification_code``)
    so a message carrying several values says which one was refused, and never
    shows a regular expression: a person typing a one-time code should not read
    ``must match pattern '^[0-9]{6}$'``. The flow re-asks with the field's own
    description on the next line, which is where the expected shape is explained.
    """
    label = (name or "value").replace("_", " ").strip() or "value"
    if reason.startswith("must match pattern"):
        return f"That {label} doesn't look right — it isn't in the expected format."
    if reason.startswith("must be one of") and spec is not None and getattr(spec, "values", None):
        from .fields import option_label

        return "Please choose one of: " + ", ".join(option_label(v) for v in spec.values) + "."
    bound = re.fullmatch(r"must be (>=|<=) (-?[0-9.]+)", reason)
    if bound:
        limit = float(bound.group(2))
        shown = str(int(limit)) if limit == int(limit) else str(limit)
        if bound.group(1) == ">=":
            return f"That {label} is too low: the minimum is {shown}."
        return f"That {label} is too high: the maximum is {shown}."
    if reason.endswith("as boolean"):
        return "Please answer yes or no."
    if "whole number" in reason:
        return f"I need a whole number for the {label}."
    if "as a number" in reason or "finite number" in reason or "is not a number" in reason:
        return f"I need a number for the {label}."
    if reason == "value is required":
        return f"I need a value for the {label}."
    return f"I couldn't accept that {label}: {reason}"


def apply_items(
    state: FlowState,
    items: list[Any],
    skill: Skill,
    host: Host,
    *,
    mode: str = "flex",
    registry: SkillRegistry | None = None,
) -> bool:
    """Mutate ``state`` per the interpreter's typed items.

    Returns ``False`` only if the flow was cancelled (turn is over). For
    every other item type the caller continues to ``run_flow_once`` so the
    flow can either complete or pause on the next unfilled field.

    ``registry`` lets a cancel set the run aside for a later "continue" (the
    other flows of the errand are looked up in it). Without it a cancel is
    still recorded in the journal; it just cannot be picked up again.
    ``Restart`` and ``Resume`` change which flow is active, so ``run_turn``
    handles them before calling this.
    """
    spoke_reply = any(isinstance(c, Reply) for c in items)
    held_before = _approvals_held(state, skill)
    saw_out_of_scope = False
    corrected: list[str] = []
    # The model writes its Reply BEFORE the runtime has validated anything, so
    # it can say "the code 'skip' has been entered" one line above the runtime
    # refusing that code. Hold the Reply until the writes have landed, and drop
    # it when any was rejected: the runtime's own message is the true one.
    reply_texts: list[str] = []
    rejected = False

    for item in items:
        if isinstance(item, Reply):
            if item.text and item.text.strip():
                reply_texts.append(item.text.strip())

        elif isinstance(item, (SetField, Correct)):
            # Whether this write is a CORRECTION is the runtime's call, not the
            # model's. The interpreter's Correct-vs-SetField choice is a hint for
            # prompting only: writing a new value over an already-filled field is
            # a correction either way. Letting the item type decide meant a model
            # that emitted SetField instead of Correct silently skipped the gate
            # re-arm, and "yes" could approve a payee the user never saw.
            wrote, was_correction = _write_field(
                state, skill, item.field, item.value, host
            )
            if not wrote:
                rejected = True
            if wrote and was_correction:
                corrected.append(item.field)

        elif isinstance(item, Reopen):
            # "Let me pick a different payee": an answer taken back is a
            # correction to "not answered". Everything that depended on it is
            # voided below with the other corrections, and the replay stops at
            # its question. A confirmation or a code is never reopened this
            # way: an unspent one is asked for anyway, and a spent one is spent.
            current = state.setdefault("fields", {})
            if (
                item.field in (skill.fields or {})
                and not _needs_pending_ask(skill, item.field)
                and current.get(item.field) is not None
            ):
                current[item.field] = None
                corrected.append(item.field)

        elif isinstance(item, Cancel):
            for text in reply_texts:
                host.emit(text)
            if state.get("flow_stack"):
                # Another flow is waiting underneath and takes over next. If the
                # model already said so in its own words, do not say it twice.
                if not reply_texts:
                    host.emit("Okay, cancelled that.")
            else:
                host.emit("Okay, cancelled. Let me know what you'd like to do next.")
            # The run ends here and nothing of it can act again. What it was is
            # written down, and the run is set aside in case the user asks to
            # continue it (both before the state is unwound).
            _journal(state, skill, "cancelled", waiting_for=state.get("awaiting_field"))
            # A request waiting on someone else is withdrawn with the flow: a
            # yes to it would be a yes to something that will not run.
            from .approvals import store_for

            store_for(host).withdraw(str(state.get("flow_run_id") or ""))
            if registry is not None:
                _park(state, registry)
            state["done"] = True
            state["flow_id"] = None
            return False

        elif isinstance(item, StartFlow):
            # If the runtime already classified into the same flow, redundant.
            # Mid-flow switches (digressions) are the caller's job — they
            # check ``StartFlow`` outside this function.
            if not state.get("flow_id"):
                state["flow_id"] = item.flow_id

        elif isinstance(item, OutOfScope):
            saw_out_of_scope = True

        elif isinstance(item, Chitchat):
            pass  # classification only

    if not rejected:
        asked = [state.get("awaiting_field"), *(state.get("pending_fields") or [])]
        prompts = [
            (getattr((skill.fields or {}).get(n), "description", None) or "").strip()
            for n in asked if n
        ]
        for text in reply_texts:
            if any(asked):
                text = _without_trailing_question(text, prompts)
            if text:
                host.emit(text)

    # Invalidation runs ONCE, after every write in the turn has landed, so a
    # correction cannot be out-raced by a confirmation supplied in the same turn.
    if corrected:
        invalidate_dependents(state, skill, corrected, host, held_before=held_before)

    if not spoke_reply and not rejected:
        synthesized = _synthesize_reply(items, saw_out_of_scope, skill, mode=mode)
        if synthesized:
            host.emit(synthesized)

    return True


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


def _without_trailing_question(text: str, prompts: list[str] | None = None) -> str:
    """``text`` without a closing question, when something comes before it.

    The flow asks the questions. A model that answers and then asks one of its
    own ("You picked Landlord. How much would you like to send?") put two
    questions on screen, its own and the flow's. The answer is kept and the
    question is left to the flow. A reply that is ONLY a question is kept: it
    may be the model asking what the user meant.

    ``prompts`` are the flow's own questions for what it is waiting on. One
    repeated word for word is removed wherever it stands: the flow is about to
    say exactly that.
    """
    for prompt in prompts or []:
        if prompt and prompt in text and text.strip() != prompt:
            text = text.replace(prompt, " ")
    text = " ".join(text.split())
    parts = [p for p in _SENTENCE_END_RE.split(text.strip()) if p]
    if len(parts) >= 2 and parts[-1].rstrip().endswith("?"):
        return " ".join(parts[:-1]).strip()
    return text


def _synthesize_reply(
    items: list[Any],
    saw_out_of_scope: bool,
    skill: Skill,
    *,
    mode: str,
) -> str | None:
    """Brief fallback when the LLM didn't include a Reply itself. Unobtrusive
    by design — the goal is to soften the transition into the next field
    prompt, not to over-explain."""
    if saw_out_of_scope:
        first_sentence = skill.description.split(".")[0].strip()
        if first_sentence:
            return f"I can only help with: {first_sentence}."
        return "Let's stay focused on the current task."

    if mode != "flex":
        return None

    set_count = sum(isinstance(c, SetField) or isinstance(c, Correct) for c in items)
    if set_count >= 1:
        return "Got it."
    if any(isinstance(c, Chitchat) for c in items):
        return None
    return None


def _non_finite(value: Any) -> bool:
    """True for a float NaN / +inf / -inf. Bools and ints are always finite."""
    return isinstance(value, float) and not math.isfinite(value)


def _is_gate_secret(skill: Skill, name: str) -> bool:
    """True when ``name`` holds a value the user authorizes WITH, rather than a
    value they are authorizing: a confirmation boolean or a sensitive secret
    (a one-time code). These are exactly the values a correction must void."""
    spec = (skill.fields or {}).get(name)
    if spec is None:
        return False
    if getattr(spec, "type", None) == "boolean":
        return True
    return bool(getattr(spec, "sensitive", False))


def _approvals_held(state: FlowState, skill: Skill) -> set[str]:
    """Confirmations already given and codes already collected, right now."""
    fields = state.get("fields") or {}
    return {n for n, v in fields.items() if v not in (None, False, "") and _needs_pending_ask(skill, n)}


def absorb_step_failure(state: FlowState, host: Host, effects_before: set[str]) -> bool:
    """Called by a graph node when a turn raised part-way. True = checkpoint, do not re-raise.

    A graph step that raises is not checkpointed, so every write that had
    already fired in it was forgotten: press Pay, the flight and the hotel are
    held, the cab service times out, press Pay again, and both are held a second
    time. When a gated write fired before the failure, the node keeps the state
    reached so far (the fired-effect log above all), tells the user, and ends
    the turn. The next message replays the flow: what fired is skipped, what
    failed is tried again.

    With nothing fired there is nothing to protect, and the exception goes on
    to the caller as before.
    """
    if not (set(state.get("effects_log") or {}) - effects_before):
        return False
    _log.exception("a step failed after a write had fired; keeping the fired-effect record")
    host.emit(
        "A step failed after part of this request had already gone through. "
        "What went through will not be repeated. Send a message to carry on."
    )
    state["pending_user_message"] = None
    state["pending_field_updates"] = None
    state["awaiting_user"] = False
    state["awaiting_field"] = None
    state["pending_fields"] = None
    return True


def invalidate_dependents(
    state: FlowState,
    skill: Skill,
    corrected: list[str],
    host: Host | None = None,
    *,
    held_before: set[str] | None = None,
) -> None:
    """Void everything downstream of a corrected value. Fail-closed.

    A correction changes the transaction the user is authorizing, so three
    things are no longer valid:

      1. Every memoized action result. Unconditionally — a hand-written
         ``@action(depends_on=[...])`` list is not evidence, and an incomplete
         one let a stale "no 2FA needed" decision survive a $500 -> $5000
         correction. Gated side effects are still at-most-once via
         ``effects_log``, which this deliberately does NOT clear, so re-running
         a replay cannot re-fire money that already moved.
      2. The say cursor, so summaries re-emit with the corrected value.
      3. Any confirmation already given and any verification code already
         collected — a code the user typed for $1000 must not authorize
         $25,000, and a payee swap after 2FA needs a fresh code.

    When the ONLY corrections were to confirmations/secrets themselves (the
    user changing their own "yes" to "no"), a collected code is left alone:
    nothing about the transaction changed, so re-asking for it would be noise.
    """
    # A list a `repeat:` collected is not a result to recompute: it is the
    # user's answers, item by item. It stays.
    repeats = dict(getattr(skill, "repeats", None) or {})
    kept = {k: v for k, v in (state.get("results") or {}).items() if k in repeats}
    state["results"] = kept
    state["say_cursor"] = 0
    fields = state.setdefault("fields", {})
    # "Add another?" sizes a list; it authorizes nothing, so nothing voids it.
    sizing = set(repeats.values())

    # A gated side effect has ALREADY fired in this run, and the user is now
    # changing an input to it. At-most-once means the correction cannot move the
    # money again — but staying silent about that was the worst of both: the flow
    # re-asked for a confirmation, took it, and did nothing. Say so instead.
    if host is not None and state.get("effects_log"):
        host.emit(
            "Heads up: part of this request already went through, so that change "
            "can't be applied to it. Start a new request if you need it undone."
        )

    data_corrections = [
        n for n in corrected if not _is_gate_secret(skill, n) or n in set(repeats.values())
    ]
    voided = False
    for name, spec in (skill.fields or {}).items():
        if fields.get(name) is None or name in sizing:
            continue
        if data_corrections:
            # A value being authorized changed: void every confirmation and
            # every secret, including one written in this same turn.
            if name in data_corrections:
                continue
            if _is_gate_secret(skill, name):
                fields[name] = None
                # Only an approval that was in place BEFORE this turn was
                # "given earlier": a yes sent in the same message as the
                # change, or an ordinary yes/no answer, is not one.
                if held_before and name in held_before:
                    voided = True
        else:
            if name in corrected:
                continue
            if getattr(spec, "type", None) == "boolean":
                fields[name] = None

    # An approval the user already gave has just been thrown away. Asking for
    # it again with the same words as the first time read as the product
    # forgetting it; say why instead.
    if voided and host is not None:
        host.emit("That changes what you approved, so your earlier approval no longer applies.")


def _write_field(
    state: FlowState,
    skill: Skill,
    name: str,
    value: Any,
    host: Host,
) -> tuple[bool, bool]:
    """Validate and store one field value.

    Returns ``(wrote, was_correction)``. Invalidation of dependent state is
    NOT done here — the caller does it once per turn, after every write has
    landed (see ``invalidate_dependents``).
    """
    spec = skill.fields.get(name)

    # A yes that is someone else's to give never arrives this way: not from the
    # user in the conversation, not from the model, not from a submitted form.
    # It reaches the flow only through the approval store (approvals.py).
    who = getattr(spec, "asked_of", None)
    if who:
        host.emit(f"That one is for {who} to answer, so I cannot take it here. It is with them now.")
        return False, False

    # NUMBERS ARE FINITE — checked BEFORE coercion. json.loads accepts the bare
    # tokens NaN / Infinity / -Infinity, so a widget submission or an HTTP resume
    # body can hand the runtime a float that satisfies `min: 0.01` (every
    # comparison against NaN is False) and makes a "needs 2FA above $1000" gate
    # decide no code is needed. There is no valid workflow value here, so re-ask.
    if _non_finite(value):
        host.emit(_rejection_text(name, "must be a finite number"))
        return False, False

    try:
        coerced = coerce_and_validate(spec, value) if spec else value
    except FieldValidationError as e:
        host.emit(_rejection_text(name, e.reason, spec))
        return False, False
    except (OverflowError, ValueError, TypeError) as e:  # defense in depth
        shown = "<redacted>" if getattr(spec, "sensitive", False) else value
        _log.warning("coercion of %r for field %r raised %r", shown, name, e)
        host.emit(_rejection_text(name, str(e)))
        return False, False

    if _non_finite(coerced):  # a coercion that manufactured one
        host.emit(_rejection_text(name, "must be a finite number"))
        return False, False

    # A rule of the flow that reads this answer and nothing else
    # (`check: amount > 0`) is a property of the answer, so it is judged here,
    # where a wrong type is judged. Left to the replay, the value landed first:
    # the turn said "Got it." and the very next line refused it.
    if coerced is not None:
        for condition, sentence in (getattr(skill, "answer_checks", None) or {}).get(name, ()):
            try:
                holds = bool(eval_strict(condition, {name: coerced}))
            except Exception:  # noqa: BLE001 - a rule that cannot be judged is not met
                holds = False
            if not holds:
                from .declarative import _render_path

                host.emit(
                    _render_path(sentence, {name: coerced}) if sentence
                    else _rejection_text(name, "it does not meet this step's rule")
                )
                return False, False

    fields = state.setdefault("fields", {})
    history = state.setdefault("field_history", {})
    # Sending back the value already stored changes nothing, so it voids
    # nothing: "yes, 2 mugs please" with the quantity already 2 keeps the yes.
    was_correction = fields.get(name) is not None and fields.get(name) != coerced

    fields[name] = coerced
    history.setdefault(name, []).append(coerced)
    return True, was_correction


def _set_field(
    state: FlowState,
    skill: Skill,
    name: str,
    value: Any,
    host: Host,
    *,
    is_correction: bool = False,
) -> None:
    """Back-compat single-field write (``is_correction`` is now only a hint —
    the runtime re-derives it). New code uses ``_write_field`` plus one
    ``invalidate_dependents`` call per turn."""
    wrote, was_correction = _write_field(state, skill, name, value, host)
    if wrote and (was_correction or is_correction):
        invalidate_dependents(state, skill, [name], host)


# ---------------------------------------------------------------------------
# Conditional / dynamic fields
# ---------------------------------------------------------------------------


def visible_fields(skill: Skill, current_fields: dict) -> dict[str, FieldSpec]:
    """Return only the fields whose ``visible_when`` conditions are met.

    A field with no ``visible_when`` is always visible. A field with
    ``visible_when={"k": v}`` is visible only when ``current_fields[k] == v``
    for ALL entries in the dict.
    """
    result: dict[str, FieldSpec] = {}
    for name, spec in skill.fields.items():
        if spec.visible_when:
            if all(
                current_fields.get(k) == v for k, v in spec.visible_when.items()
            ):
                result[name] = spec
        else:
            result[name] = spec
    return result


# ---------------------------------------------------------------------------
# Flow execution
# ---------------------------------------------------------------------------


def run_flow_once(state: FlowState, skill: Skill, host: Host) -> bool:
    """Run the flow function from the top with the current state.

    Returns:
        True  if the flow completed (state["done"] set).
        False if the flow paused on an unfilled field (state["awaiting_user"]
              set, prompt emitted).
    """
    # Cleared each replay; set again only if a widget/chat step pauses this run.
    state["pending_widget"] = None
    state["chat_config"] = None
    # Filter field_specs to only visible fields (Feature 4). The host must ride
    # along — ctx.show/chat render through it (emit_widget, llm_text).
    current = state.get("fields", {})
    active_field_specs = visible_fields(skill, current)
    ctx = Context(state=state, field_specs=active_field_specs, emit=host.emit, host=host)

    try:
        skill.flow_fn(ctx)
    except NeedField as need:
        host.emit(need.prompt)
        state["awaiting_user"] = True
        state["awaiting_field"] = need.field
        state["pending_fields"] = None
        return False
    except NeedFields as need:
        host.emit("\n".join(need.prompts))
        state["awaiting_user"] = True
        state["awaiting_field"] = need.fields[0]
        state["pending_fields"] = need.fields
        return False
    except NeedWidget as nw:
        if nw.prompt:
            host.emit(nw.prompt)
        state["pending_widget"] = {
            "widget": nw.widget_id,
            "data": nw.data,
            "fields": nw.fields,
            "submit_schema": nw.submit_schema,
            # Auto-generated widgets (pick:) carry their spec inline; None for
            # catalog widgets the frontend resolves by id.
            "spec": nw.spec,
        }
        state["awaiting_user"] = True
        # awaiting_field points at the first field the widget fills, so the
        # existing "render the awaiting field" frontends still have a handle.
        state["awaiting_field"] = nw.fields[0] if nw.fields else None
        # ALL the fields this pause is asking for. The user-only guard reads it
        # to tell "the flow asked for this on this turn" from "the model
        # volunteered it", and leaving a stale list from an earlier multi-field
        # pause in place would widen that window.
        state["pending_fields"] = list(nw.fields) or None
        return False
    except ChatMode as cm:
        state["chat_config"] = dict(cm.config)
        state["awaiting_user"] = True
        state["awaiting_field"] = None
        state["pending_fields"] = None
        return False
    except FlowDone:
        pass
    except FlowLink as link:
        if link.target_flow_id == skill.name:
            raise ValueError(
                f"skill {skill.name!r} called ctx.link() on itself. A flow cannot hand off to "
                "itself; to start over, end the flow and let the user begin again."
            ) from None
        # A link is a call, not a jump: the frame records which link it is
        # suspended on, and the pop marks that one done so the replay walks
        # past it. Anything recorded at or after this position belonged to a
        # branch the flow is no longer on.
        state["links_done"] = list(state.get("links_done") or [])[: link.index]
        _push_stack_frame(state, pending_link=link.target_flow_id)
        reset_for_new_flow(state, link.target_flow_id)
        return False

    state["done"] = True
    state["awaiting_user"] = False
    state["awaiting_field"] = None
    state["pending_fields"] = None
    return True


def _refresh_reads(state: FlowState, skill: Skill) -> None:
    """Forget the resumed flow's lookups, so its replay reads them again.

    A suspended flow keeps its memoized results. That is right for a write (it
    must not fire twice, and ``effects_log`` sees to that separately), and wrong
    for a read once another flow has run in between: the other flow may have
    changed what the read returns. pay-someone listed the payees, the user went
    off to add one, and came back to a list without the payee the bot had just
    said it saved.

    Only ``readonly: true`` lookups whose result is shown to the user are
    dropped (the declarative loader marks them). A read has no side effect, so
    repeating it is safe; a verification the flow already passed is not
    repeated, and neither is a lookup that speaks with ``then_say:``.
    """
    results = state.get("results") or {}
    actions = getattr(skill, "actions", None) or {}
    for name in list(results):
        # Only a lookup that feeds something the user looks at (a `pick:` grid,
        # a `show:` card, a widget). One that a `check:` consumed is left
        # alone: re-running "verify this code" on every resume asked the user
        # for a one-time code they had already entered.
        if getattr(actions.get(name), "_flow_effect_refresh", False):
            results.pop(name, None)


def _push_stack_frame(state: FlowState, *, pending_link: str | None = None) -> None:
    stack = state.setdefault("flow_stack", [])
    stack.append(
        {
            "flow_id": state.get("flow_id"),
            "fields": dict(state.get("fields", {})),
            "results": dict(state.get("results", {})),
            "say_cursor": state.get("say_cursor", 0),
            "links_done": list(state.get("links_done") or []),
            "pending_link": pending_link,
            # Run-scoped, so they suspend with the run. Without these the child
            # flow's reset handed the parent a blank fired-effect record and a
            # new run id, and re-confirming after the digression spent again.
            "flow_run_id": state.get("flow_run_id"),
            "effects_log": dict(state.get("effects_log") or {}),
        }
    )


def _pop_stack_frame(state: FlowState, *, child_completed: bool = False) -> bool:
    """Resume the flow on top of the stack. ``child_completed`` says whether the
    flow being left did its job (True) or was cancelled, abandoned, or ended
    with its final write declined (False); a parent that got here through
    ctx.link() is told which."""
    stack = state.get("flow_stack") or []
    if not stack:
        return False
    frame = stack.pop()
    state["flow_id"] = frame["flow_id"]
    state["fields"] = frame["fields"]
    state["results"] = frame["results"]
    state["say_cursor"] = frame["say_cursor"]
    # Coming back from a ctx.link(): that link has been taken, whether its
    # target did its job, was cancelled, or was declined at its last
    # confirmation. Either way the parent does not hand off again when its
    # replay reaches the same line; ctx.link() returns which happened.
    done = list(frame.get("links_done") or [])
    if frame.get("pending_link"):
        done.append({"flow": frame["pending_link"], "completed": bool(child_completed)})
    state["links_done"] = done
    state["flow_run_id"] = frame.get("flow_run_id")
    # Union, parent-frame-wins: the parent's own fired effects come back, and
    # anything the child fired while it owned the state stays on record.
    merged = dict(state.get("effects_log") or {})
    merged.update(frame.get("effects_log") or {})
    state["effects_log"] = merged
    state["flow_stack"] = stack
    state["done"] = False
    return True


# ---------------------------------------------------------------------------
# Chat mode (the scoped agentic leaf a `chat:` step opens)
# ---------------------------------------------------------------------------


def _chat_tool_def(name: str, fn: Any) -> dict[str, Any]:
    """OpenAI-format tool definition from a raw tool. LangChain tools carry
    .description/.args_schema; plain callables fall back to their docstring
    and an open parameter schema (pinned args are injected regardless)."""
    desc = getattr(fn, "description", None) or getattr(fn, "__doc__", None) or name
    params: dict[str, Any] | None = None
    schema_obj = getattr(fn, "tool_call_schema", None) or getattr(fn, "args_schema", None)
    if schema_obj is not None:
        try:
            params = schema_obj.model_json_schema()
        except Exception:  # noqa: BLE001 — schema introspection is best-effort
            params = None
    if not params:
        params = {"type": "object", "properties": {}, "additionalProperties": True}
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(desc).strip()[:500],
            "parameters": params,
        },
    }


def _answer_in_chat(
    state: FlowState,
    skill: Skill,
    host: Host,
    user_message: str,
    config: dict[str, Any],
) -> None:
    """Answer one free-form question inside the chat leaf.

    The model freely picks WHICH allowlisted tool answers the question; the
    pinned args (e.g. the validated account number) are injected AFTER the
    model's choice and override anything it emitted — scope is state-owned,
    never model-owned. Degrades gracefully: no tool support -> plain answer;
    no text support -> a labeled data dump.
    """
    import json as _json

    from .declarative import _normalize_tool

    raw_tools = skill.chat_tools or {}
    names = [t for t in (config.get("tools") or []) if t in raw_tools]
    fields = state.get("fields", {})
    pinned = {arg: fields.get(fld) for arg, fld in (config.get("pin") or {}).items()}
    pin_note = ", ".join(f"{k}={v}" for k, v in pinned.items()) or "none"
    system = config.get("system") or (
        f"You answer questions in the scope of the {skill.name} workflow. "
        f"Fixed context (always applied automatically, never change or ask for it): {pin_note}. "
        "Pick the ONE tool that best answers the user's question."
    )
    # The skill's natural-language guidelines steer chat/assist answers too.
    from .interpret import render_guidelines

    system += render_guidelines(skill)
    # `context:` grounds answers in the workflow's own outputs — the named
    # fields / action results are embedded so the model can answer about what
    # the flow just did without a tool round-trip.
    ctx_names = config.get("context") or []
    if ctx_names:
        results = state.get("results", {})
        ctx_data = {
            n: (results.get(n) if n in results else fields.get(n))
            for n in ctx_names
        }
        system += (
            "\nWorkflow data you may answer from directly:\n"
            + _json.dumps(ctx_data, default=str)[:4000]
        )

    calls: list[Any] = []
    if names:
        try:
            calls = host.llm_tool_call(
                system=system,
                user=user_message,
                tools=[_chat_tool_def(n, raw_tools[n]) for n in names],
                tool_choice="auto",
            )
        except NotImplementedError:
            calls = []

    answered = False
    for call in calls[:1]:  # v1: one tool call per turn
        if call.name not in raw_tools:
            continue
        args = {**(call.args or {}), **pinned}  # pin OVERRIDES the model
        invocation_context: Any = {
            "skill_name": skill.name,
            "action_name": "chat",
            "flow_run_id": state.get("flow_run_id"),
        }
        try:
            from .connectors.models import InvocationContext

            invocation_context = InvocationContext.model_validate(invocation_context)
        except ImportError:  # pragma: no cover
            pass
        result = _normalize_tool(raw_tools[call.name])(args, invocation_context)

        emit_widget = getattr(host, "emit_widget", None)
        if emit_widget is not None and isinstance(result, (dict, list)):
            from .autowidget import spec_for

            emit_widget(spec_for(result, title=call.name.replace("_", " ").title()))

        text = _chat_text(
            host,
            system=system,
            user=(
                f"Question: {user_message}\n"
                f"Tool {call.name} returned:\n"
                f"{_json.dumps(result, default=str)[:4000]}\n"
                "Answer the question briefly and concretely from this data."
            ),
        )
        host.emit(text or f"Here's what {call.name.replace('_', ' ')} returned.")
        answered = True

    if not answered:
        text = _chat_text(host, system=system, user=user_message)
        if text:
            host.emit(text)
        elif names:
            host.emit(
                "I can answer questions using: "
                + ", ".join(n.replace("_", " ") for n in names)
                + ". What would you like to know?"
            )
        else:
            host.emit("I couldn't find a tool to answer that here.")


def _chat_text(host: Host, *, system: str, user: str) -> str | None:
    llm_text = getattr(host, "llm_text", None)
    if llm_text is None:
        return None
    try:
        out = llm_text(system=system, user=user)
    except NotImplementedError:
        return None
    return str(out).strip() or None


# ---------------------------------------------------------------------------
# Public entry: one user turn
# ---------------------------------------------------------------------------


def run_turn(
    state: FlowState,
    registry: SkillRegistry,
    host: Host,
    *,
    mode: str = "flex",
) -> FlowState:
    """Run one full user turn.

    ``mode`` selects interpretation strategy:
      - ``"flex"`` (default): free-form JSON structured output. Works on
        any modern instruction model.
      - ``"strict"``: constrained tool calling — typed args make wrong
        field names / values impossible at the API layer. Requires
        ``host.supports_tool_use``; falls back to flex silently otherwise
        so a misconfigured deployment degrades instead of crashing.

    Inputs in state:
        - pending_user_message: the new user message (consumed)
        - flow_id: active flow, or None for first turn

    Outputs in state (set by this function):
        - awaiting_user, awaiting_field: if we paused on an ask
        - done: if the flow completed
        - pending_user_message: cleared
        - history: user message appended; assistant messages flushed by host
    """
    if mode == "strict" and not getattr(host, "supports_tool_use", True):
        mode = "flex"

    user_message = state.get("pending_user_message")
    direct_updates = state.get("pending_field_updates")
    # True once this turn has put the parked run back (see _resume_parked).
    resumed = False
    # Was a run open when the turn began, and which? A flow that only started
    # on this turn has nothing to start over.
    open_at_start = bool(state.get("flow_id")) and not state.get("done")
    run_at_start = state.get("flow_run_id")

    # 0. A FINISHED flow does not own the conversation: a later message is not
    # read against it under that skill's one-topic prompt. A structured
    # re-submission still belongs to the flow (an "edit" on a review widget is
    # a correction), and so does a suspended parent frame.
    if (
        user_message
        and not direct_updates
        and state.get("done")
        and state.get("flow_id")
        and not state.get("flow_stack")
        and registry.has(state["flow_id"])
    ):
        handled, start, resume = _turn_outside_flow(
            state, registry, host, user_message,
            finished=registry.get(state["flow_id"]),
        )
        if handled:
            return state
        if resume:
            resumed = _resume_parked(state, registry, host)
            if not resumed:
                state.setdefault("history", []).append({"role": "user", "content": user_message})
                state["pending_user_message"] = None
                return state
        elif start:
            # A fresh run — new run id, cleared fields — and the SAME message is
            # then read against it below, so "book a room tomorrow at 3" starts
            # the booking with the date and time already understood.
            reset_for_new_flow(state, start)

    # 1. Resolve which flow is active. If none, ask the LLM classifier.
    if not state.get("flow_id"):
        from .interpret import classify_skill  # local to avoid cycles at import
        flow_id: str | None = None
        asked_outside = False
        if user_message and (state.get("journal") or state.get("parked")):
            # Something has already happened in this conversation (a run was
            # cancelled, or ended and its frame is gone). The cold-start
            # classifier knows none of it: "continue" matched nothing and "who
            # did I pick?" started the flow again. Ask the question asked after
            # a finished flow instead: answer, start something, or go back.
            asked_outside = True
            handled, flow_id, resume = _turn_outside_flow(
                state, registry, host, user_message, finished=None
            )
            if handled:
                return state
            if resume:
                resumed = _resume_parked(state, registry, host)
                if not resumed:
                    state.setdefault("history", []).append({"role": "user", "content": user_message})
                    state["pending_user_message"] = None
                    return state
    if not state.get("flow_id"):
        if flow_id is None:
            flow_id = classify_skill(host=host, user_message=user_message or "", registry=registry)
        if flow_id is None and user_message and not asked_outside:
            # Nothing matched: talk like a normal assistant instead of forcing
            # the user into a workflow or telling them to rephrase.
            handled, start, _ = _turn_outside_flow(
                state, registry, host, user_message, finished=None
            )
            if handled:
                return state
            flow_id = start
        if flow_id is None and user_message:
            # Safety-net fallback: if the classifier was indecisive and there
            # is exactly ONE aigenos flow workflow loaded, route the user there.
            # Mirrors the "single-skill demo" convenience.
            workflows = [
                s for s in registry.all() if getattr(s, "workflow", True)
            ]
            if len(workflows) == 1:
                flow_id = workflows[0].name
        if flow_id is None:
            host.emit("I'm not sure what you'd like to do. Could you rephrase?")
            return state
        reset_for_new_flow(state, flow_id)

    skill = registry.get(state["flow_id"])  # type: ignore[arg-type]

    # 2a. Direct field updates from a widget submission — bypass the LLM.
    # Structured payloads are deterministic by nature; sending them through
    # the interpreter risks the LLM dropping or mis-coercing the value (e.g.
    # qwen3 occasionally re-emits an empty SetField for "confirm: Yes").
    if direct_updates:
        corrected_direct: list[str] = []
        held_direct = _approvals_held(state, skill)
        # What the pause on screen is asking for. A confirmation or a secret
        # is accepted only for THAT pause, so a yes delivered twice (a double
        # click, a retried request) cannot land in the next flow's own
        # `confirm` after a hand-off and release a payment whose question was
        # never shown.
        asked = set(state.get("pending_fields") or ())
        if state.get("awaiting_field"):
            asked.add(state["awaiting_field"])
        # A payload that answers the question on screen may fill later answers
        # too. One that answers nothing being asked was not made for this pause.
        out_of_band = bool(asked) and not (asked & set(direct_updates))
        for name, value in direct_updates.items():
            if name == LOOK_IN:
                continue  # not an answer: a client asking the flow to take its next turn
            # CLOSED VOCABULARY. A structured submission may only write fields
            # the active skill declares. Anything else — a typo, or a payload
            # shaped like internal state ("done", "flow_run_id", "effects_log")
            # — is dropped and logged rather than landing in state['fields']
            # where a widget binding or a compute name could read it back.
            if name not in (skill.fields or {}):
                _log.warning(
                    "dropping submitted key %r: not a declared field of skill %r",
                    name, skill.name,
                )
                continue
            if out_of_band and _needs_pending_ask(skill, name):
                _log.warning(
                    "dropping submitted %r: skill %r is not asking for it at this step",
                    name, skill.name,
                )
                continue
            # Re-submitting an already-filled field (e.g. an "edit" control on a
            # review widget) is a CORRECTION — it must invalidate memoized action
            # results so a downstream policy gate re-evaluates on the new value.
            wrote, was_correction = _write_field(state, skill, name, value, host)
            if wrote and was_correction:
                corrected_direct.append(name)
        if corrected_direct:
            invalidate_dependents(state, skill, corrected_direct, host, held_before=held_direct)
        state["pending_field_updates"] = None

    elif direct_updates is not None and (state.get("awaiting_field") or state.get("pending_fields")):
        waiting_on = (skill.fields or {}).get(str(state.get("awaiting_field") or ""))
        if getattr(waiting_on, "asked_of", None):
            # The flow is waiting on someone else, and this is the client
            # looking in to see whether they have answered. Nothing was asked
            # of the user, so there is nothing to tell them off for.
            pass
        else:
            # A submission with nothing in it (Continue on a list with no row
            # chosen, from a client that allows it): say why the card is back.
            host.emit("I did not get an answer there. Choose or enter one to continue.")
        state["pending_field_updates"] = None

    # 2. Interpret the user message against the active flow.
    if user_message and resumed:
        # The message asked to go back. It may also change something.
        state.setdefault("history", []).append({"role": "user", "content": user_message})
        _read_into_resumed(state, skill, host, user_message, mode)
        state["pending_user_message"] = None
    elif user_message:
        # What already happened, for the model to answer from. Rendered before
        # this message joins the history, so it is not shown to itself.
        memory, paused = _memory(state, in_flow=True, skill=skill)
        state.setdefault("history", []).append({"role": "user", "content": user_message})
        # Every OTHER aigenos flow workflow is reachable from the active flow
        # via StartFlow. Push the current frame, run the child, pop back —
        # parent state preserved by default. These side trips are the
        # `digressions` handed to interpret_turn; there is no separate kind.
        # A flow that is waiting on its own ctx.link() is not somewhere else
        # to go: the user is inside the step it handed off. Offered to the model
        # as "another workflow", "lets send to vivek" (said while add-payee was
        # asking for a routing number) was read as "start send-money", and the
        # errand ended with "No payee was added."
        handed_off = {
            f.get("flow_id") for f in (state.get("flow_stack") or []) if f.get("pending_link")
        }
        reachable = [
            s for s in registry.all()
            if getattr(s, "workflow", True) and s.name != skill.name and s.name not in handed_off
        ]
        interp = interpret_turn(
            host=host,
            skill=skill,
            user_message=user_message,
            current_fields=state.get("fields", {}),
            mode=mode,
            digressions=reachable,
            awaiting_field=state.get("awaiting_field"),
            pending_fields=state.get("pending_fields"),
            memory=memory,
            paused=paused,
        )
        # 2·. "Start over" and "go back to what I stopped" change which run is
        # active, so they are settled first and the rest of the message is not
        # applied: it was read against the flow being left.
        if any(isinstance(it, Restart) for it in interp.items) and (
            not open_at_start or run_at_start != state.get("flow_run_id")
        ):
            # "Do that again" just started this run from its first question.
            # Restarting it would only say "starting over" about nothing.
            interp.items = [it for it in interp.items if not isinstance(it, Restart)]
        if any(isinstance(it, Restart) for it in interp.items):
            skill = _restart(state, registry, skill, host)
            interp.items = []
        elif state.get("parked") and any(isinstance(it, Resume) for it in interp.items):
            if _resume_parked(state, registry, host):
                skill = registry.get(state["flow_id"])  # type: ignore[arg-type]
                _read_into_resumed(state, skill, host, user_message, mode)
            interp.items = []
        # 2a. Mid-flow StartFlow → push parent frame, run child flow. The
        # parent's collected fields are preserved on the stack and restored
        # on completion / pop. Self-recursion (StartFlow targeting the
        # active flow) is silently dropped — you can't push yourself onto
        # your own stack — and a debug log records the no-op.
        child_start = next(
            (it for it in interp.items if isinstance(it, StartFlow)),
            None,
        )
        resume_as: str | None = None
        if child_start and child_start.flow_id in handed_off:
            # The model named a flow that is waiting on a hand-off. Going "back"
            # to it would pop the hand-off as failed. The user is in the flow it
            # handed to, so that is where they stay: either it is the active
            # flow (drop the StartFlow, read the rest of the message here), or
            # it is suspended under a side trip (resume IT, not its parent).
            frames = state.get("flow_stack") or []
            at = max(i for i, f in enumerate(frames) if f.get("flow_id") == child_start.flow_id)
            if at == len(frames) - 1:
                _log.debug("ignoring StartFlow(%s): it is waiting on its own hand-off", child_start.flow_id)
                interp.items = [it for it in interp.items if not isinstance(it, StartFlow)]
                child_start = None
            else:
                resume_as = frames[at + 1].get("flow_id")
        if (
            child_start
            and child_start.flow_id != skill.name
            and registry.has(child_start.flow_id)
        ):
            target = resume_as or child_start.flow_id
            stack = state.get("flow_stack") or []
            if any(f.get("flow_id") == target for f in stack):
                # RESUME: the target flow is already in progress on the stack
                # (an ancestor). The user wants to return to it, NOT start a
                # fresh duplicate. Discard abandoned frames above it and restore
                # its preserved fields. Without this, returning to a parent flow
                # silently loses everything it had collected.
                while stack and stack[-1].get("flow_id") != target:
                    stack.pop()
                state["flow_stack"] = stack
                _pop_stack_frame(state)  # restores target frame (fields/results)
                skill = registry.get(state["flow_id"])  # type: ignore[arg-type]
                # The question on screen belonged to the flow just left. Left in
                # state, it told the guard that the RESUMED flow was asking for
                # its confirmation, and a model-supplied yes was accepted.
                state["awaiting_field"] = None
                state["pending_fields"] = None
                _refresh_reads(state, skill)
                # Re-interpret the SAME message against the resumed flow so the
                # user can resume AND act in one turn ("...just confirm the
                # transfer"). Field extraction now uses the resumed flow's schema.
                reinterp = interpret_turn(
                    host=host, skill=skill, user_message=user_message,
                    current_fields=state.get("fields", {}), mode=mode, digressions=[],
                    awaiting_field=state.get("awaiting_field"),
                    pending_fields=state.get("pending_fields"),
                )
                apply_items(
                    state,
                    [it for it in reinterp.items if not isinstance(it, StartFlow)],
                    skill, host, mode=mode,
                )
            else:
                # NEW child flow → push parent frame, start the child fresh.
                _push_stack_frame(state)
                reset_for_new_flow(state, target)
                for it in interp.items:
                    if isinstance(it, Reply) and it.text:
                        host.emit(it.text.strip())
                skill = registry.get(state["flow_id"])  # type: ignore[arg-type]
            state["pending_user_message"] = None
        else:
            if child_start and child_start.flow_id == skill.name:
                _log.debug(
                    "ignoring self-recursive StartFlow(%s) — already active",
                    child_start.flow_id,
                )
            # chat_config reflects the LAST replay: the flow is paused on its
            # `chat:` step. Structured commands (corrections, cancel) still own
            # the turn — that's what keeps account-switching deterministic. A
            # turn with none of those is a free-form question for the scoped
            # tool loop; the interpreter's Reply/OutOfScope takes are dropped.
            chat_cfg = state.get("chat_config")
            assist_cfg = getattr(skill, "assist", None)
            actionable = any(
                isinstance(it, (SetField, Correct, Cancel)) for it in interp.items
            )
            if chat_cfg and not actionable:
                _answer_in_chat(state, skill, host, user_message, chat_cfg)
                state["pending_user_message"] = None
            elif (
                assist_cfg is not None
                and not actionable
                and state.get("awaiting_user")
            ):
                # Mid-flow side question: answer it for real (scoped tools /
                # plain LLM), then fall through to the replay below — which
                # re-raises the pending ask/widget and re-emits its prompt.
                # Free conversation; deterministic return to the flow.
                awaiting = state.get("awaiting_field")
                steer = dict(assist_cfg)
                steer.setdefault(
                    "system",
                    f"You are assisting inside the {skill.name} workflow. The user "
                    f"asked a side question while the workflow waits for "
                    f"{awaiting or 'their next input'}. Answer briefly and helpfully; "
                    "do not ask follow-up questions — the workflow will re-prompt.",
                )
                _answer_in_chat(state, skill, host, user_message, steer)
                state["pending_user_message"] = None
            else:
                cont = apply_items(
                    state, list(interp.items), skill, host, mode=mode, registry=registry
                )
                state["pending_user_message"] = None
                if not cont:
                    waiting = state.get("flow_stack") or []
                    by_link = bool(waiting and waiting[-1].get("pending_link"))
                    if not state.get("flow_id") and _pop_stack_frame(state):
                        skill = registry.get(state["flow_id"])  # type: ignore[arg-type]
                        _refresh_reads(state, skill)
                        if not by_link:
                            # A cancelled side trip: say the earlier flow is back,
                            # as a finished one does. A ctx.link() parent speaks
                            # for itself on its next line.
                            host.emit("Now, back to where we were.")
                    else:
                        return state

    # 3. Re-execute the flow.
    state.pop("flow_declined", None)  # type: ignore[misc]
    completed = run_flow_once(state, skill, host)
    # Set by a flow whose last step is a gated write the user said no to. It
    # ran to its end and did nothing, and a parent told "finished" would carry
    # on as if the write had happened (pay a payee who was never saved).
    # ("handoff" when it stopped because a skill it linked to did not finish.)
    declined = state.pop("flow_declined", False)  # type: ignore[misc]

    # 3a. The flow handed off with ctx.link(). The target is checked HERE, while
    # the parent can still be put back: left alone, a misspelled or
    # instruction-only target surfaced a turn later as a bare KeyError, with
    # flow_id already pointing at something that does not exist.
    target = state.get("flow_id")
    stack = state.get("flow_stack") or []
    if not completed and target != skill.name and stack and stack[-1].get("pending_link") == target:
        problem = None
        if not registry.has(target):  # type: ignore[arg-type]
            problem = f"no such skill is loaded (known: {sorted(s.name for s in registry.all())})"
        elif registry.get(target).flow_fn is None:  # type: ignore[arg-type]
            problem = "it is an instruction-only skill with no flow to run"
        if problem:
            stack[-1]["pending_link"] = None  # not taken: the parent is restored as it was
            _pop_stack_frame(state)
            raise ValueError(f"skill {skill.name!r} called ctx.link({target!r}), but {problem}.")  # also a YAML `link:`

    # 4. If completed, pop the suspended frame and resume the parent flow.
    if completed:
        # Written down before the frame is popped: what this run came to.
        _journal(
            state, skill,
            ("stopped" if declined == "handoff" else "declined") if declined else "ended",
        )
    frames = state.get("flow_stack") or []
    came_by_link = bool(frames and frames[-1].get("pending_link"))
    if completed and _pop_stack_frame(state, child_completed=not declined):
        if registry.has(state.get("flow_id")):  # type: ignore[arg-type]
            _refresh_reads(state, registry.get(state["flow_id"]))  # type: ignore[arg-type]
        # A digression interrupted the parent, so say it is resuming. A
        # ctx.link() was the parent's own next step: its next line speaks for it.
        if not came_by_link:
            host.emit("Now, back to where we were.")

    return state

"""Per-run execution trace — the evidence behind the determinism claim.

The product's promise is that the *runtime*, not the model, decides what
happens. A trace is what makes that inspectable: which step ran, which guard
skipped it, what each gate evaluated to, in order.

Recording is opt-in and scoped by a ContextVar, so a normal run pays for one
``.get()`` per step and nothing else. Concurrent runs (the server handles
many) never see each other's entries.

Usage::

    from aigenos_flow import trace

    with trace.recording() as entries:
        agent.turn(state)
    # entries -> [{"step": 0, "verb": "ask", "outcome": "paused", ...}, ...]
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

# None = nobody is recording on this task; entries are dropped.
_SINK: ContextVar[list[dict[str, Any]] | None] = ContextVar("aigenos_flow_trace", default=None)


@contextmanager
def recording() -> Iterator[list[dict[str, Any]]]:
    """Collect trace entries emitted on this task for the duration of the block."""
    entries: list[dict[str, Any]] = []
    token = _SINK.set(entries)
    try:
        yield entries
    finally:
        _SINK.reset(token)


def active() -> bool:
    return _SINK.get() is not None


def emit(
    *,
    step: int,
    verb: str,
    outcome: str,
    detail: str | None = None,
    guard: str | None = None,
) -> None:
    """Record one step outcome.

    Args:
        step: 0-based index into the flow's step list.
        verb: the step's verb (ask/say/do/pick/show/check/chat/widget).
        outcome: what the runtime did — "ran", "skipped", "paused",
            "passed", "failed".
        detail: short human-readable specifics (the field, the expression).
        guard: which guard caused a "skipped" outcome ("when" / "when_missing").
    """
    sink = _SINK.get()
    if sink is None:
        return
    entry: dict[str, Any] = {"step": step, "verb": verb, "outcome": outcome}
    if detail is not None:
        entry["detail"] = detail
    if guard is not None:
        entry["guard"] = guard
    sink.append(entry)


def signature(entries: list[dict[str, Any]]) -> str:
    """A compact, order-sensitive fingerprint of a trace.

    Two runs of the same flow over the same interpretation must produce the
    same signature — that is the determinism property, reduced to one
    comparable string. Deliberately excludes `detail`, which can legitimately
    carry rendered text; the decision path is what must be identical.
    """
    return "|".join(
        f"{e['step']}:{e['verb']}:{e['outcome']}{':' + e['guard'] if e.get('guard') else ''}"
        for e in entries
    )

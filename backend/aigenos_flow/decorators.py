"""@flow and @action decorators — what the dev imports.

We don't auto-register into a global because handlers.py is imported once per
Skill load. The registry hooks itself up at load time by inspecting the module
after import. Decorators just tag callables with sentinel attributes the
loader looks for.
"""
from __future__ import annotations

from typing import Any, Callable


_FLOW_MARKER = "_flow_is_flow"
_ACTION_MARKER = "_flow_is_action"
_ACTION_NAME = "_flow_action_name"
_ACTION_DEPENDS_ON = "_flow_action_depends_on"
_ACTION_READONLY = "_flow_action_readonly"
_ACTION_REQUIRES = "_flow_action_requires"


def flow(fn: Callable[..., None]) -> Callable[..., None]:
    """Mark a function as the entry-point of a Skill's flow.

    The loader expects exactly one `@flow`-tagged callable per `handlers.py`.

    Usage:

        from aigenos_flow import flow

        @flow
        def file_expense(ctx):
            ctx.collect("amount")
            ...
    """
    setattr(fn, _FLOW_MARKER, True)
    return fn


def action(
    fn_or_name: Callable[..., Any] | str | None = None,
    /,
    depends_on: list[str] | None = None,
    readonly: bool = False,
    requires: str | None = None,
) -> Any:
    """Mark a function as an action (memoized side-effectful step).

    An ``@action`` is assumed to WRITE unless it declares ``readonly=True`` —
    the same contract a declarative ``actions:`` entry follows. A write
    dispatched from a declarative ``do:`` step must name its confirmation field
    with ``requires=`` and the step must be guarded by a ``when:`` that proves
    that field is true; the loader refuses the skill otherwise, so the Python
    escape hatch cannot be a way around the gate.

    Supported call styles:

        @action(readonly=True)                     # a lookup / search
        def find_rooms(state): ...

        @action(requires="confirm")                # a write
        def submit(state): ...

        @action("submit_expense", requires="confirm")
        def submit(state): ...

        @action(depends_on=["room", "date"], readonly=True)
        def check_availability(state): ...
    """
    if readonly and requires is not None:
        raise ValueError(
            "@action(readonly=True) cannot also declare requires= — a read-only "
            "action has no side effect to gate"
        )
    if requires is not None and not isinstance(requires, str):
        raise ValueError("@action(requires=...) must name a declared boolean field")

    def wrap(
        fn: Callable[..., Any],
        name: str | None = None,
        deps: list[str] | None = None,
    ) -> Callable[..., Any]:
        setattr(fn, _ACTION_MARKER, True)
        setattr(fn, _ACTION_NAME, name or fn.__name__)
        setattr(fn, _ACTION_READONLY, bool(readonly))
        setattr(fn, _ACTION_REQUIRES, requires)
        if deps is not None:
            setattr(fn, _ACTION_DEPENDS_ON, deps)
        return fn

    if callable(fn_or_name):
        return wrap(fn_or_name, deps=depends_on)
    # Called as @action("name") or @action(depends_on=[...]) — return decorator
    explicit_name = fn_or_name

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        return wrap(fn, explicit_name, deps=depends_on)

    return deco


# ---------- module introspection ----------


def is_flow(obj: Any) -> bool:
    return callable(obj) and getattr(obj, _FLOW_MARKER, False) is True


def is_action(obj: Any) -> bool:
    return callable(obj) and getattr(obj, _ACTION_MARKER, False) is True


def action_name(obj: Any) -> str:
    return getattr(obj, _ACTION_NAME, obj.__name__)


def action_depends_on(obj: Any) -> list[str] | None:
    """Return the depends_on list for an @action, or None if not set."""
    return getattr(obj, _ACTION_DEPENDS_ON, None)


def action_readonly(obj: Any) -> bool:
    """True when the @action declared ``readonly=True``.

    Fail-closed default: an @action that says nothing is a WRITE, so the loader
    holds it to the same gating rules as a declared ``actions:`` entry.
    """
    return getattr(obj, _ACTION_READONLY, False) is True


def action_requires(obj: Any) -> str | None:
    """The confirmation field an @action write declared with ``requires=``."""
    return getattr(obj, _ACTION_REQUIRES, None)

"""Scenario test harness — the thing enterprise users actually need.

A scenario is a scripted sequence of user turns + expected outcomes. We replay
it against a TestHost so no LLM runs, and assert that the deterministic
runtime produced the expected state and emitted the expected messages.

This is what gets your dialog flows into CI.

Example::

    from aigenos_flow.testing import Scenario
    from aigenos_flow import SetField

    scenario = Scenario(skill="file_expense")
    scenario.turn(
        user="file an expense for $100 at Whole Foods, food category",
        emits=[SetField(field="amount", value=100),
               SetField(field="merchant", value="Whole Foods"),
               SetField(field="category", value="food")],
    )
    scenario.expect(fields={"amount": 100.0, "merchant": "Whole Foods", "category": "food"})
    scenario.expect(done=True)
    scenario.run(agent)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .hosts.test_host import TestHost
from .interpret import Turn
from .runtime import FlowState, initial_state


@dataclass
class _Turn:
    user: str
    emits: list[Turn]


@dataclass
class _Expect:
    fields: dict[str, Any] | None = None
    results: dict[str, Any] | None = None
    awaiting_field: str | None = None
    done: bool | None = None
    last_assistant_contains: str | None = None


@dataclass
class Scenario:
    skill: str | None = None
    turns: list[_Turn] = field(default_factory=list)
    expectations: list[_Expect] = field(default_factory=list)

    def turn(self, *, user: str, emits: list[Turn] | None = None) -> "Scenario":
        self.turns.append(_Turn(user=user, emits=emits or []))
        return self

    def expect(
        self,
        *,
        fields: dict[str, Any] | None = None,
        results: dict[str, Any] | None = None,
        awaiting_field: str | None = None,
        done: bool | None = None,
        last_assistant_contains: str | None = None,
        # Back-compat: old code used ``slots=`` / ``awaiting_slot=``.
        slots: dict[str, Any] | None = None,
        awaiting_slot: str | None = None,
    ) -> "Scenario":
        if fields is None and slots is not None:
            fields = slots
        if awaiting_field is None and awaiting_slot is not None:
            awaiting_field = awaiting_slot
        self.expectations.append(
            _Expect(
                fields=fields,
                results=results,
                awaiting_field=awaiting_field,
                done=done,
                last_assistant_contains=last_assistant_contains,
            )
        )
        return self

    def run(self, agent: Any, state: FlowState | None = None) -> FlowState:
        host = agent.host
        if not isinstance(host, TestHost):
            raise TypeError(
                "Scenario.run() requires the agent's host to be TestHost; "
                f"got {type(host).__name__}."
            )

        for t in self.turns:
            host.queue_commands(t.user, *t.emits)

        if state is None:
            state = initial_state()  # type: ignore[assignment]
            if self.skill:
                state["flow_id"] = self.skill

        all_assistant_messages: list[str] = []

        for t in self.turns:
            state["pending_user_message"] = t.user
            state = agent.turn(state)  # type: ignore[assignment]
            all_assistant_messages.extend(host.drain_pending_messages())

        for exp in self.expectations:
            _assert_expectation(exp, state, all_assistant_messages)

        return state


def _assert_expectation(
    exp: _Expect, state: FlowState, assistant_messages: list[str]
) -> None:
    if exp.fields is not None:
        for k, want in exp.fields.items():
            got = state.get("fields", {}).get(k)
            assert got == want, f"field {k!r}: expected {want!r}, got {got!r}"
    if exp.results is not None:
        for k, want in exp.results.items():
            got = state.get("results", {}).get(k)
            assert got == want, f"result {k!r}: expected {want!r}, got {got!r}"
    if exp.awaiting_field is not None:
        got = state.get("awaiting_field")
        assert got == exp.awaiting_field, (
            f"awaiting_field: expected {exp.awaiting_field!r}, got {got!r}"
        )
    if exp.done is not None:
        got = bool(state.get("done"))
        assert got == exp.done, f"done: expected {exp.done!r}, got {got!r}"
    if exp.last_assistant_contains is not None:
        last = assistant_messages[-1] if assistant_messages else ""
        assert exp.last_assistant_contains in last, (
            f"last assistant message did not contain {exp.last_assistant_contains!r}; "
            f"got {last!r}"
        )


expect = _Expect

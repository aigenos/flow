"""Tests for flex vs strict response modes.

flex (default): LLM is expected to always Reply; runtime synthesizes a brief
acknowledgement when the LLM forgets. Conversation feels like an LLM chatbot
on top of a deterministic flow.

strict: LLM is not pushed to Reply; runtime stays quiet except for the
deterministic collect prompt and an OOS fallback. Conversation feels like
a form.
"""
from __future__ import annotations

import pytest

from aigenos_flow import FlowAgent, OutOfScope, SetField, initial_state
from aigenos_flow.hosts.test_host import TestHost

# `skills_dir` is provided by conftest.py — it resolves to the monorepo's
# examples/expense/skills via repo-root walk.


# ---------------------------------------------------------------- flex mode


def test_flex_synthesizes_got_it_when_llm_omits_reply_on_setslot(skills_dir):
    """Flex mode: LLM forgot the Reply on a slot fill — runtime adds 'Got it.'"""
    host = TestHost()
    agent = FlowAgent.from_dir(skills_dir, host=host, mode="flex")

    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "100",
        SetField(field="amount", value=100),  # no Reply alongside
    )
    state["pending_user_message"] = "100"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert any(m.strip() == "Got it." for m in msgs), msgs
    # Flow still advances to next slot.
    assert state["fields"]["amount"] == 100.0
    assert state.get("awaiting_field") == "merchant"


def test_flex_synthesizes_help_on_oos_without_reply(skills_dir):
    """Flex mode + OOS without Reply: surface skill description as fallback."""
    host = TestHost()
    agent = FlowAgent.from_dir(skills_dir, host=host, mode="flex")

    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "what's the weather?",
        OutOfScope(reason="weather"),
    )
    state["pending_user_message"] = "what's the weather?"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert any("expense" in m.lower() for m in msgs), msgs


# ---------------------------------------------------------------- strict mode


@pytest.mark.xfail(
    reason=(
        "Pre-existing TestHost API gap: queue_commands populates the flex-mode "
        "scripted dict, but strict mode reads from _scripted_tool_calls. Needs "
        "a queue_tool_calls helper on TestHost to script strict-mode runs."
    ),
    strict=False,
)
def test_strict_stays_quiet_on_setslot_without_reply(skills_dir):
    """Strict mode: no synthesized 'Got it.'; only the next deterministic
    collect prompt appears."""
    host = TestHost()
    agent = FlowAgent.from_dir(skills_dir, host=host, mode="strict")

    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "100",
        SetField(field="amount", value=100),
    )
    state["pending_user_message"] = "100"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert not any(m.strip() == "Got it." for m in msgs), msgs
    # Only the merchant collect prompt should appear.
    assert any("purchase" in m.lower() or "merchant" in m.lower() for m in msgs), msgs


@pytest.mark.xfail(
    reason=(
        "Pre-existing TestHost API gap: see sibling test_strict_stays_quiet_on_setslot_without_reply."
    ),
    strict=False,
)
def test_strict_still_surfaces_help_on_oos(skills_dir):
    """Strict mode still synthesizes the OOS fallback — otherwise the user
    would never know why their off-topic question was ignored. This is the
    one place strict mode is NOT silent."""
    host = TestHost()
    agent = FlowAgent.from_dir(skills_dir, host=host, mode="strict")

    state = initial_state()
    state["flow_id"] = "file-expense"

    host.queue_commands(
        "what's the weather?",
        OutOfScope(reason="weather"),
    )
    state["pending_user_message"] = "what's the weather?"
    state = agent.turn(state)

    msgs = host.drain_pending_messages()
    assert any("expense" in m.lower() for m in msgs), msgs


def test_invalid_mode_rejected(skills_dir):
    with pytest.raises(ValueError, match="mode"):
        FlowAgent.from_dir(skills_dir, host=TestHost(), mode="loose")

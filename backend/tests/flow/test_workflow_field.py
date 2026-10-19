"""Tests for ``workflow:`` boolean in SKILL.md frontmatter.

Default True; legacy ``kind: workflow``/``kind: digression`` normalize to
True with a DeprecationWarning. All workflows are routable from cold start
and mid-flow; self-recursive StartFlow is a no-op.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import pytest


def _studio_agent_dir() -> Path:
    override = os.getenv("FLOW_STUDIO_AGENT_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "examples" / "studio_agent"


sys.path.insert(0, str(_studio_agent_dir()))

from shared.rooms import reset_bookings  # noqa: E402

from aigenos_flow import (  # noqa: E402
    FlowAgent,
    Reply,
    SetField,
    StartFlow,
)
from aigenos_flow.hosts.test_host import TestHost  # noqa: E402
from aigenos_flow.skills import SkillFrontmatter, parse_skill_md  # noqa: E402


# ---------------------------------------------------------------------------
# Frontmatter-level: workflow defaults + kind alias deprecation
# ---------------------------------------------------------------------------


def test_workflow_defaults_to_true_when_omitted():
    """A SKILL.md with no ``workflow:`` field is treated as a workflow."""
    fm, _ = parse_skill_md(
        "---\n"
        "name: test-skill\n"
        "description: A test skill.\n"
        "---\n"
        "body"
    )
    assert fm.workflow is True


def test_explicit_workflow_false_is_honored():
    """Plain Anthropic skills can opt out via ``workflow: false``."""
    fm, _ = parse_skill_md(
        "---\n"
        "name: plain-skill\n"
        "description: A non-aigenos flow skill.\n"
        "workflow: false\n"
        "---\n"
        "body"
    )
    assert fm.workflow is False


def test_legacy_kind_workflow_warns_and_normalizes():
    """Old ``kind: workflow`` loads with a DeprecationWarning, sets workflow=True."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fm, _ = parse_skill_md(
            "---\n"
            "name: legacy-skill\n"
            "description: Legacy frontmatter.\n"
            "kind: workflow\n"
            "---\n"
            "body"
        )
    assert fm.workflow is True
    assert any(issubclass(w.category, DeprecationWarning) for w in caught), \
        f"expected DeprecationWarning for legacy kind: workflow, got {[w.category for w in caught]}"
    assert not hasattr(fm, "kind"), "kind should not appear on the new frontmatter model"


def test_field_widget_parses_from_yaml():
    """A per-field ``widget: my-widget`` line in SKILL.md surfaces on the
    parsed FieldSpec so the host integration can look it up at render time."""
    fm, _ = parse_skill_md(
        "---\n"
        "name: test-skill\n"
        "description: A test skill with a widget-bound field.\n"
        "fields:\n"
        "  duration_minutes:\n"
        "    type: integer\n"
        "    widget: duration_picker\n"
        "---\n"
        "body"
    )
    assert fm.fields["duration_minutes"].widget == "duration_picker"


def test_field_widget_defaults_to_none():
    """Fields without a ``widget:`` line leave it unset — host integrations
    use that to decide whether to auto-generate vs. look up the catalog."""
    fm, _ = parse_skill_md(
        "---\n"
        "name: test-skill\n"
        "description: A test skill.\n"
        "fields:\n"
        "  duration_minutes:\n"
        "    type: integer\n"
        "---\n"
        "body"
    )
    assert fm.fields["duration_minutes"].widget is None


def test_skill_level_widget_parses():
    """Skill-level ``widget:`` is the multi-field fallback."""
    fm, _ = parse_skill_md(
        "---\n"
        "name: test-skill\n"
        "description: A test skill.\n"
        "widget: master-form\n"
        "---\n"
        "body"
    )
    assert fm.widget == "master-form"


def test_legacy_kind_digression_warns_and_normalizes_to_workflow_true():
    """``kind: digression`` is the most important migration case: under the
    new design, what used to be a digression is just a regular workflow.
    Push/pop on mid-flow invocation is the default for every workflow."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fm, _ = parse_skill_md(
            "---\n"
            "name: legacy-digression\n"
            "description: Legacy digression.\n"
            "kind: digression\n"
            "---\n"
            "body"
        )
    assert fm.workflow is True
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ---------------------------------------------------------------------------
# Runtime: all workflows are reachable mid-flow (no digression-only filter)
# ---------------------------------------------------------------------------


def _build_agent() -> tuple[FlowAgent, TestHost]:
    reset_bookings()
    host = TestHost()
    agent = FlowAgent.from_dir(_studio_agent_dir() / "skills", host=host, mode="flex")
    return agent, host


def test_self_recursive_startflow_is_a_no_op():
    """If the LLM emits StartFlow targeting the currently active flow, the
    runtime should NOT push another stack frame. Otherwise the user would
    accumulate frames on every redundant StartFlow and never pop back."""
    agent, host = _build_agent()

    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    # The LLM mistakenly emits StartFlow for the SAME active flow alongside
    # a legitimate SetField. The SetField should still apply; no new frame
    # should be pushed.
    host.queue_commands(
        "tomorrow",
        StartFlow(flow_id="book-meeting-room"),
        SetField(field="date", value="2026-05-23"),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "tomorrow"
    state = agent.turn(state)
    host.drain_pending_messages()

    assert state["fields"]["date"] == "2026-05-23"
    assert not state.get("flow_stack"), \
        f"self-recursive StartFlow must not push a frame, got {state.get('flow_stack')!r}"
    assert state["flow_id"] == "book-meeting-room"


def test_mid_flow_startflow_to_any_workflow_pushes_frame():
    """Under the new model, ANY other workflow is reachable mid-flow via
    StartFlow — not just ones that used to be ``kind: digression``. Verify
    that list-rooms (no longer marked digression) still acts as a push/pop
    detour from inside book-meeting-room."""
    agent, host = _build_agent()

    host.queue_classification("book a meeting room", "book-meeting-room")
    host.queue_commands(
        "book a meeting room",
        Reply(text="Sure! What day?"),
    )
    host.queue_commands(
        "tomorrow",
        SetField(field="date", value="2026-05-23"),
    )
    host.queue_commands(
        "what rooms?",
        StartFlow(flow_id="list-rooms"),
        Reply(text="Sure."),
    )

    state = {"pending_user_message": "book a meeting room"}
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "tomorrow"
    state = agent.turn(state)
    host.drain_pending_messages()

    state["pending_user_message"] = "what rooms?"
    state = agent.turn(state)
    msgs = host.drain_pending_messages()

    # list-rooms ran (its handler emitted the room names) and we returned
    # to the parent flow with the date still set.
    assert any("redwood" in m.lower() for m in msgs), msgs
    assert state["fields"].get("date") == "2026-05-23", \
        f"parent flow's date lost across mid-flow workflow: {state['fields']!r}"
    assert state["flow_id"] == "book-meeting-room"
    assert not state.get("flow_stack")

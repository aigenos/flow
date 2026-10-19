"""Widget submissions bypass the LLM interpreter.

Regression test for the bug where small models (qwen3-class) occasionally
re-emitted an empty SetField for a "confirm: Yes" widget submission, which
made the flow re-ask the same boolean field in a loop. After the fix,
``pending_field_updates`` on the FlowState is applied directly via
``_set_field`` — no LLM round-trip, no interpretation failure mode.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.runtime import run_turn
from aigenos_flow.skills import load_skills_dir


def _write_confirm_skill(tmp: Path) -> None:
    skill_dir = tmp / "confirm-demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: confirm-demo
        description: A tiny flow that just asks for a boolean confirm.
        license: MIT
        handlers: ./handlers.py
        triggers:
          - confirm
        fields:
          confirm:
            type: boolean
            description: user confirms (yes / no)
        ---

        # Confirm demo
    """), encoding="utf-8")
    (skill_dir / "handlers.py").write_text(textwrap.dedent("""\
        from aigenos_flow import flow

        @flow
        def confirm_demo(ctx):
            ctx.ask("confirm")
    """), encoding="utf-8")


def test_dict_payload_sets_boolean_field_without_llm(tmp_path: Path):
    _write_confirm_skill(tmp_path)
    registry = load_skills_dir(tmp_path)

    host = TestHost()
    state = {
        "flow_id": "confirm-demo",
        "fields": {},
        # Widget submission: structured payload. The interpreter would have
        # to parse "confirm: Yes" via LLM — this path skips that entirely.
        "pending_field_updates": {"confirm": "Yes"},
    }

    run_turn(state, registry, host)

    assert state["fields"].get("confirm") is True, (
        "boolean field should be coerced + applied without invoking the LLM"
    )
    # The bypass should self-clear so the next turn is clean.
    assert state.get("pending_field_updates") in (None, {})


def test_dict_payload_with_invalid_value_emits_validation_error(tmp_path: Path):
    _write_confirm_skill(tmp_path)
    registry = load_skills_dir(tmp_path)

    host = TestHost()
    state = {
        "flow_id": "confirm-demo",
        "fields": {},
        "pending_field_updates": {"confirm": "maybe"},  # not a valid bool
    }

    run_turn(state, registry, host)

    # Validation error surfaces via host.emit — field stays unset, no crash.
    assert "confirm" not in state.get("fields", {})
    drained = host.drain_pending_messages()
    assert any("yes or no" in m for m in drained), drained

"""must_elicit field flag — schema + loader + interpreter prompt rendering.

The flag stops the interpreter LLM from filling fields by inference
(date-defaulting being the canonical hallucination). Enforcement is
two-layer: a prompt rule warns the LLM not to do it, and a post-hoc filter
deterministically drops any inferred values that slip through. The filter
is what makes the guarantee provable rather than aspirational.

These tests pin the contract:

  1. FieldSpec parses must_elicit from YAML / defaults to False.
  2. SKILL.md frontmatter round-trips it through the loader (both new
     ``fields:`` and legacy ``slots:`` keys).
  3. The interpreter system prompt tags the specific field when
     must_elicit=True and renders the user-only rule.
  4. The post-hoc filter drops ungrounded SetField values for must_elicit
     fields and keeps grounded ones.
"""
from __future__ import annotations

import yaml

from aigenos_flow.fields import FieldSpec
from aigenos_flow.interpret import (
    Reply,
    SetField,
    TurnInterpretation,
    _filter_must_elicit,
    _is_grounded_in_user,
    _render_field_block,
    _render_must_elicit_rule,
)
from aigenos_flow.skills import Skill, parse_skill_md


def test_must_elicit_defaults_to_false():
    spec = FieldSpec(type="string")
    assert spec.must_elicit is False


def test_must_elicit_round_trips_from_yaml():
    spec = FieldSpec.model_validate(
        yaml.safe_load("type: string\nmust_elicit: true\n")
    )
    assert spec.must_elicit is True


def test_must_elicit_round_trips_through_skill_md_new_key():
    text = """---
name: book-room
description: Book a room.
fields:
  date:
    type: string
    must_elicit: true
  notes:
    type: string
    required: false
---

# Book a room
"""
    fm, _ = parse_skill_md(text)
    assert fm.fields["date"].must_elicit is True
    assert fm.fields["notes"].must_elicit is False


def test_must_elicit_round_trips_through_skill_md_slots_alias():
    text = """---
name: book-room
description: Book a room.
slots:
  date:
    type: string
    must_elicit: true
---

# Book a room
"""
    fm, _ = parse_skill_md(text)
    assert fm.fields["date"].must_elicit is True


def _make_skill(fields: dict[str, FieldSpec]) -> Skill:
    return Skill(
        name="x", description="x", triggers=[], fields=fields,
        body="body", path=__import__("pathlib").Path("."),
    )


def test_interpreter_renders_user_only_tag_only_for_must_elicit_fields():
    skill = _make_skill({
        "date": FieldSpec(type="string", must_elicit=True, description="A day."),
        "room": FieldSpec(type="string", description="A room."),
    })
    block = _render_field_block(skill)
    date_line = next(l for l in block.splitlines() if l.startswith("- date"))
    room_line = next(l for l in block.splitlines() if l.startswith("- room"))
    assert "[user-only" in date_line
    assert "[user-only" not in room_line


def test_interpreter_emits_rule_when_any_must_elicit_field_exists():
    skill = _make_skill({
        "date": FieldSpec(type="string", must_elicit=True),
        "room": FieldSpec(type="string"),
    })
    rule = _render_must_elicit_rule(skill)
    assert "RULE" in rule
    assert "`date`" in rule
    assert "`room`" not in rule


def test_interpreter_emits_no_rule_when_no_must_elicit_field():
    skill = _make_skill({"room": FieldSpec(type="string")})
    assert _render_must_elicit_rule(skill) == ""


# ---------------------------------------------------------------------------
# Post-hoc guard: _is_grounded_in_user + _filter_must_elicit
# ---------------------------------------------------------------------------


def test_grounded_rejects_inferred_date():
    assert _is_grounded_in_user("date", "2026-05-24", "book redwood at 3pm for an hour") is False


def test_grounded_accepts_explicit_relative_date():
    assert _is_grounded_in_user("date", "2026-05-23", "book redwood tomorrow at 3pm") is True


def test_grounded_accepts_explicit_weekday():
    assert _is_grounded_in_user("date", "2026-05-25", "book it for monday at 3pm") is True


def test_grounded_accepts_iso_date_substring():
    assert _is_grounded_in_user("date", "2026-05-23", "book it on 2026-05-23 at 3pm") is True


def test_grounded_accepts_value_token_match():
    assert _is_grounded_in_user("merchant", "Whole Foods", "$100 at whole foods") is True


def test_grounded_rejects_unrelated_value():
    assert _is_grounded_in_user("merchant", "Joe Coffee", "$100 at whole foods") is False


def test_filter_drops_ungrounded_must_elicit_setfield():
    skill = _make_skill({
        "date": FieldSpec(type="string", must_elicit=True),
        "room": FieldSpec(type="string"),
    })
    interp = TurnInterpretation(items=[
        Reply(text="Got it."),
        SetField(field="date", value="2026-05-24"),
        SetField(field="room", value="redwood"),
    ])
    filtered = _filter_must_elicit(interp, skill, "book redwood at 3pm for an hour")
    types = [type(c).__name__ for c in filtered.items]
    assert "Reply" in types
    assert any(isinstance(c, SetField) and c.field == "room" for c in filtered.items)
    assert not any(isinstance(c, SetField) and c.field == "date" for c in filtered.items)


def test_filter_keeps_grounded_must_elicit_setfield():
    skill = _make_skill({
        "date": FieldSpec(type="string", must_elicit=True),
    })
    interp = TurnInterpretation(items=[
        SetField(field="date", value="2026-05-23"),
    ])
    filtered = _filter_must_elicit(interp, skill, "book redwood tomorrow at 3pm")
    assert any(isinstance(c, SetField) and c.field == "date" for c in filtered.items)


def test_filter_is_noop_when_no_must_elicit_field():
    skill = _make_skill({"room": FieldSpec(type="string")})
    interp = TurnInterpretation(items=[SetField(field="room", value="redwood")])
    filtered = _filter_must_elicit(interp, skill, "anything goes here")
    assert len(filtered.items) == 1

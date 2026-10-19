"""SKILL.md -> Widgets-catalog linkage.

Resolution order: field.widget, then skill.widget, then auto-generated.
Catalog is monkeypatched per-test so we don't depend on data/widgets/
contents.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aigenos_flow.engine import loader
from aigenos_flow.integrations.flow import resolve_widget_for_field
from aigenos_flow.schemas.widget_spec import SpecNode, WidgetSpec


def _make_widget(widget_id: str, *, title: str = "Custom") -> WidgetSpec:
    return WidgetSpec(
        id=widget_id,
        version="1.0.0",
        description=f"Pre-authored widget {widget_id}",
        bindings=["date"],
        submit_schema={
            "type": "object",
            "properties": {"duration_minutes": {"type": "string"}},
            "required": ["duration_minutes"],
        },
        spec=SpecNode(
            type="card",
            props={"title": title, "subtitle": "Pick on {{date}}"},
            children=[
                SpecNode(
                    type="form",
                    props={"submitLabel": "Go"},
                    children=[
                        SpecNode(
                            type="input",
                            props={"name": "duration_minutes", "label": "Duration"},
                        )
                    ],
                )
            ],
        ),
    )


def _make_skill(
    *,
    field_widget: str | None = None,
    skill_widget: str | None = None,
) -> SimpleNamespace:
    # Stand-in for aigenos_flow.skills.Skill; resolve_widget_for_field only
    # reads .name, .fields, and .widget.
    duration_spec = SimpleNamespace(
        type="integer",
        description="Meeting length in minutes.",
        values=None,
        min=15,
        max=480,
        required=True,
        examples=[],
        must_elicit=False,
        widget=field_widget,
    )
    return SimpleNamespace(
        name="book-meeting-room",
        fields={"duration_minutes": duration_spec},
        widget=skill_widget,
    )


@pytest.fixture
def catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Empty tmp catalog; each test seeds what it needs."""
    widget_dir = tmp_path / "widgets"
    widget_dir.mkdir()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "WIDGET_DIR", widget_dir)
    return widget_dir


def test_field_widget_takes_precedence_over_skill_widget(catalog: Path):
    """Per-field widget wins over skill-level."""
    loader.save_widget(_make_widget("field_picker", title="Field"))
    loader.save_widget(_make_widget("skill_form", title="Skill"))

    skill = _make_skill(field_widget="field_picker", skill_widget="skill_form")
    result = resolve_widget_for_field(
        skill=skill,
        field_name="duration_minutes",
        state_fields={"date": "2026-05-30"},
    )

    assert result.id == "field_picker", \
        f"expected per-field widget to win, got {result.id!r}"
    # Bindings were resolved against the supplied state — {{date}} is filled.
    assert "2026-05-30" in result.spec.props.get("subtitle", ""), \
        f"expected date to resolve into subtitle, got {result.spec.props!r}"


def test_skill_widget_used_when_field_widget_unset(catalog: Path):
    """No field widget -> skill-level widget is used."""
    loader.save_widget(_make_widget("skill_form", title="Skill"))

    skill = _make_skill(skill_widget="skill_form")
    result = resolve_widget_for_field(
        skill=skill,
        field_name="duration_minutes",
        state_fields={},
    )

    assert result.id == "skill_form"


def test_falls_back_to_autogen_when_widget_missing_from_catalog(catalog: Path):
    """Unknown widget id -> auto-generated form. Must not crash."""
    skill = _make_skill(field_widget="does_not_exist")
    result = resolve_widget_for_field(
        skill=skill,
        field_name="duration_minutes",
        state_fields={},
    )

    # The auto-generated widget has id ``flow_<skill_name>``.
    assert result.id == "flow_book-meeting-room", \
        f"expected fallback to auto-gen, got {result.id!r}"


def test_falls_back_to_autogen_when_no_widget_declared(catalog: Path):
    """No widget declared -> auto-generated form."""
    skill = _make_skill()  # no widgets declared anywhere
    result = resolve_widget_for_field(
        skill=skill,
        field_name="duration_minutes",
        state_fields={},
    )

    assert result.id == "flow_book-meeting-room"


def test_unbound_placeholder_renders_empty_not_crash(catalog: Path):
    """Unbound {{var}} resolves to empty string, not literal."""
    loader.save_widget(_make_widget("field_picker", title="Field"))

    skill = _make_skill(field_widget="field_picker")
    result = resolve_widget_for_field(
        skill=skill,
        field_name="duration_minutes",
        state_fields={},  # no `date` to substitute
    )

    assert result.id == "field_picker"
    # The {{date}} placeholder must be substituted with empty string, not
    # left literal — confirming binding resolution actually ran.
    subtitle = result.spec.props.get("subtitle", "")
    assert "{{" not in subtitle, \
        f"binding placeholder leaked into rendered subtitle: {subtitle!r}"

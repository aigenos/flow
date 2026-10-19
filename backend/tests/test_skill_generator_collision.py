"""Regression: generated @action names must not collide with the @flow
function name (or each other), or the later @action def overwrites the @flow
in the module namespace and the loader raises 'handlers.py has no @flow
function'. This bit a skill named 'create-jira-ticket' whose action the LLM
also named 'create_jira_ticket'.
"""
from pathlib import Path

from aigenos_flow.skill_generator import (
    GeneratedAction,
    GeneratedFieldSpec,
    GeneratedSkillSpec,
    render_handlers_py,
    render_skill_md,
)
from aigenos_flow.skills import load_skill


def _write(spec: GeneratedSkillSpec, tmp_path: Path) -> Path:
    folder = tmp_path / spec.name
    folder.mkdir()
    (folder / "SKILL.md").write_text(render_skill_md(spec), encoding="utf-8")
    (folder / "handlers.py").write_text(render_handlers_py(spec), encoding="utf-8")
    return folder


def test_action_name_collides_with_flow_name(tmp_path):
    spec = GeneratedSkillSpec(
        name="create-jira-ticket",
        description="Create a Jira ticket from a short description supplied by the user.",
        triggers=["create a jira ticket"],
        fields={"summary": GeneratedFieldSpec(type="string", description="The ticket summary line")},
        actions=[
            GeneratedAction(name="create_jira_ticket", purpose="create the ticket", fake_result={"id": "JIRA-1"}),
        ],
        confirmation_field=None,
    )
    skill = load_skill(_write(spec, tmp_path))  # must NOT raise "no @flow function"
    assert skill.name == "create-jira-ticket"


def test_duplicate_action_names_do_not_collide(tmp_path):
    spec = GeneratedSkillSpec(
        name="do-thing",
        description="Do a thing in two steps that happen to share the same name token.",
        triggers=["do the thing"],
        fields={"x": GeneratedFieldSpec(type="string", description="some input value to collect")},
        actions=[
            GeneratedAction(name="submit", purpose="first submit", fake_result={"ok": True}),
            GeneratedAction(name="submit", purpose="second submit", fake_result={"ok": True}),
        ],
        confirmation_field=None,
    )
    skill = load_skill(_write(spec, tmp_path))
    assert skill.name == "do-thing"

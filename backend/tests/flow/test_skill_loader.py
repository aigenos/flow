"""Tests for the SKILL.md loader (now in aigenos_flow.skills)."""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow.skills import load_skill, load_skills_dir, parse_skill_md


def test_parse_frontmatter_minimal():
    text = """---
name: hello
description: A tiny skill.
---

# Hello

Body text.
"""
    fm, body = parse_skill_md(text)
    assert fm.name == "hello"
    assert fm.description == "A tiny skill."
    assert body.startswith("# Hello")


def test_parse_rejects_missing_frontmatter():
    with pytest.raises(ValueError, match="frontmatter"):
        parse_skill_md("# no frontmatter here")


def test_load_expense_skill(skills_dir: Path):
    skill = load_skill(skills_dir / "file-expense")
    assert skill.name == "file-expense"
    assert "amount" in skill.fields
    assert skill.fields["amount"].type == "number"
    assert skill.fields["amount"].min == 0
    assert skill.flow_fn is not None
    assert callable(skill.flow_fn)
    assert "check_policy" in skill.actions
    assert "submit_expense" in skill.actions


def test_load_skills_dir(skills_dir: Path):
    reg = load_skills_dir(skills_dir)
    assert len(reg) == 1
    assert "file-expense" in reg


def test_name_validation_lowercase(tmp_path: Path):
    bad_skill = tmp_path / "bad"
    bad_skill.mkdir()
    (bad_skill / "SKILL.md").write_text("""---
name: BadName
description: invalid casing
---
""")
    (bad_skill / "handlers.py").write_text(
        "from aigenos_flow import flow\n@flow\ndef noop(ctx): pass\n"
    )
    with pytest.raises(Exception):  # Pydantic ValidationError
        load_skill(bad_skill)


def test_slots_alias_back_compat(tmp_path: Path):
    """SKILL.md files that still say `slots:` should keep working — the
    frontmatter parser rewrites the key to ``fields:`` on the fly.

    Folder + name use kebab-case to satisfy the Anthropic Skills spec
    validator added in 0.2; the `slots:` alias is the actual back-compat
    surface under test."""
    skill_dir = tmp_path / "back-compat"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("""---
name: back-compat
description: Uses the deprecated slots key.
slots:
  amount:
    type: number
    min: 0
---

# back compat
""")
    (skill_dir / "handlers.py").write_text(
        "from aigenos_flow import flow\n@flow\ndef bc(ctx): pass\n"
    )
    skill = load_skill(skill_dir)
    assert "amount" in skill.fields

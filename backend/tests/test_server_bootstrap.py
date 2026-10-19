"""A freshly installed server must come up usable.

Two bootstrap bugs, both found by installing the built wheel into a clean venv
and asking for /flow/skills the way the Studio does:

1. With no FG_SKILLS_DIR, the resolver looked for the monorepo's
   examples/studio_agent/skills, did not find it, and left /flow UNMOUNTED — every
   chat and Studio call answered 404 with nothing logged.
2. Once it did mount, an empty skills folder raised instead of serving an empty
   list, so a server whose first skill had not been authored yet was a 500.

A folder whose skills all FAILED to load must still raise: that is a broken
deployment, not an empty one, and silently serving it would be exactly the
fail-open behaviour the loader exists to prevent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost

BROKEN_SKILL = """---
name: broken
description: Declares a flow step verb that does not exist.
flow:
  - frobnicate: nope
---
Body.
"""


def test_from_dir_refuses_an_empty_directory_by_default(tmp_path: Path):
    """A developer who typo'd the path must hear about it."""
    with pytest.raises(ValueError) as err:
        FlowAgent.from_dir(tmp_path, host=TestHost())
    assert "no skills found" in str(err.value)
    assert "allow_empty" in str(err.value), "the error should name the way out"


def test_from_dir_allows_an_empty_directory_when_asked(tmp_path: Path):
    """What the server passes, so the Studio can author its first skill."""
    agent = FlowAgent.from_dir(tmp_path, host=TestHost(), allow_empty=True)
    assert agent.list_skills() == []


def test_a_directory_whose_skills_all_fail_still_raises(tmp_path: Path):
    """Fail closed: broken is not the same as empty, even with allow_empty."""
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "SKILL.md").write_text(BROKEN_SKILL, encoding="utf-8")

    for kwargs in ({}, {"allow_empty": True}):
        with pytest.raises(ValueError) as err:
            FlowAgent.from_dir(tmp_path, host=TestHost(), **kwargs)
        message = str(err.value)
        assert "could not be loaded" in message or "could be loaded" in message
        assert "broken" in message, "the failing skill must be named"


def test_a_packaged_install_falls_back_to_a_writable_skills_dir(tmp_path, monkeypatch):
    """No FG_SKILLS_DIR and no monorepo: resolve a dir under the data dir and
    CREATE it, so /flow mounts instead of vanishing."""
    from aigenos_flow import main as main_mod
    from aigenos_flow.engine import loader

    monkeypatch.delenv("FG_SKILLS_DIR", raising=False)
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path / "data")
    # Pretend we are installed, not in the monorepo: no examples/ above us.
    monkeypatch.setattr(main_mod, "__file__", str(tmp_path / "pkg" / "main.py"))

    resolved = main_mod._resolve_skills_dir()
    assert resolved == tmp_path / "data" / "skills"
    assert resolved.is_dir(), "the fallback must be created, not just named"


def test_an_explicit_but_missing_skills_dir_is_still_a_hard_error(monkeypatch):
    """Pointing FG_SKILLS_DIR at nothing is a deployment mistake, not a default."""
    from aigenos_flow import main as main_mod

    monkeypatch.setenv("FG_SKILLS_DIR", "/definitely/not/here/skills")
    assert main_mod._resolve_skills_dir() is None

"""A `say:` can quote what a tool returned.

`{{lookup.total}}` in a `say:` is the first thing an author tries. It once
reached the user as written, braces and all; then the loader refused it and
pointed at `then_say:`. It now simply works: a `say:` reads an answer by its
name and an action's result by a dotted path, and so does `else_say:`. A path
into something that was never declared is still a load error.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow.declarative import ToolRegistry
from aigenos_flow.skills import load_skills_dir

SKILL = """\
---
name: quote-demo
description: Demo. Use when testing templates.
allowed-tools: lookup
fields:
  item:
    type: enum
    values: [a, b]
    description: Which item?
actions:
  lookup:
    tool: lookup
    readonly: true
    args: {{ item: "{{{{item}}}}" }}
flow:
  - ask: item
  - do: lookup
{steps}
---
Demo.
"""


def _load(tmp_path: Path, steps: str):
    folder = tmp_path / "quote-demo"
    folder.mkdir()
    (folder / "SKILL.md").write_text(SKILL.format(steps=steps), encoding="utf-8")
    tools = ToolRegistry()
    tools.register("lookup", lambda args, ctx=None: {"ok": True, "total": "$5"})
    return load_skills_dir(tmp_path, tool_registry=tools)


def _run(tmp_path: Path, steps: str, result: dict):
    from aigenos_flow import FlowAgent
    from aigenos_flow.hosts.test_host import TestHost

    folder = tmp_path / "quote-demo"
    folder.mkdir()
    (folder / "SKILL.md").write_text(SKILL.format(steps=steps), encoding="utf-8")
    agent = FlowAgent.from_dir(tmp_path, host=TestHost(), tools={"lookup": lambda args: dict(result)})
    return agent.session("quote-demo").submit(item="a")


def test_a_say_quotes_a_result_by_its_path(tmp_path: Path):
    chat = _run(tmp_path, '  - say: "That comes to {{lookup.total}} for item {{item}}."', {"ok": True, "total": "$5"})
    assert chat.replies == ["That comes to $5 for item a."]


def test_an_else_say_quotes_a_result_too(tmp_path: Path):
    steps = ('  - check: "lookup.ok"\n'
             '    else_say: "No: {{lookup.total}} is too much."\n'
             "    else_clear: [item]")
    chat = _run(tmp_path, steps, {"ok": False, "total": "$5"})
    assert chat.replies[0] == "No: $5 is too much." and chat.awaiting == "item"


def test_a_path_into_something_undeclared_is_still_a_load_error(tmp_path: Path):
    with pytest.raises(ValueError, match="say: references undeclared 'lookupp'"):
        _load(tmp_path, '  - say: "That comes to {{lookupp.total}}."')


def test_say_with_whole_field_names_still_loads(tmp_path: Path):
    registry = _load(tmp_path, '  - say: "You picked {{item}}."')
    assert registry.get("quote-demo")


def test_then_say_is_where_a_result_is_quoted(tmp_path: Path):
    folder = tmp_path / "quote-demo"
    folder.mkdir()
    text = SKILL.format(steps="").replace("  - do: lookup\n", '  - do: lookup\n    then_say: "That comes to {{lookup.total}}."\n')
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    tools = ToolRegistry()
    tools.register("lookup", lambda args, ctx=None: {"ok": True, "total": "$5"})
    assert load_skills_dir(tmp_path, tool_registry=tools).get("quote-demo")

"""Changing a skill from a sentence: the model writes the change, the loader judges it.

The model here is scripted, including a model that gets it wrong. That is the
point: what the editor returns is never better than what the loader accepts.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aigenos_flow import skill_editor
from aigenos_flow.skills import load_skill
from aigenos_flow.toolbox import list_tools, load_library

TOOLS = '''
from aigenos_flow import readonly


@readonly
def find_order(order_id: str) -> dict:
    """Look an order up."""
    return {"found": True, "large": False}


def issue_refund(order_id: str, reason: str = "") -> dict:
    """Refund the order."""
    return {"reference": "RF-1"}
'''

SKILL = """\
---
name: refund-order
description: Refund an order. Use when the user wants a refund.
# The steps are the whole policy.
steps:
  - ask: { order_id: "Which order?" }
    check: { find_order.found: "I can't find that order." }
  - confirm: Refund this order?
  - do: issue_refund
  - say: "Refunded. Reference {{issue_refund.reference}}."
---
Help the user get a refund.
"""

WITH_REASON = """\
steps:
  - ask: { order_id: "Which order?" }
    check: { find_order.found: "I can't find that order." }
  - ask: { reason: "What went wrong with it?" }
    choices: [damaged, late, changed_mind]
  - confirm: Refund this order?
  - do: issue_refund
  - say: "Refunded. Reference {{issue_refund.reference}}."
"""


class Scripted:
    """A model that answers with what the test hands it, in order, and keeps what it was sent."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.seen: list[list] = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        return type("Reply", (), {"content": self.replies.pop(0)})()


def _reply(steps: str, changed: str = "Added a question.") -> str:
    return f"Here it is.\n```yaml\n{steps}```\nCHANGED: {changed}\n"


@pytest.fixture
def desk(tmp_path: Path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "desk.py").write_text(TOOLS, encoding="utf-8")
    registry = load_library(tmp_path, None)

    def check(md: str) -> dict:
        folder = tmp_path / "refund-order"
        folder.mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(md, encoding="utf-8")
        try:
            load_skill(folder, tool_registry=registry)
        except Exception as exc:  # noqa: BLE001 - the loader's answer
            return {"ok": False, "error": str(exc), "step": None}
        return {"ok": True}

    return check, list_tools(registry)


def test_a_change_that_loads_comes_back_as_a_proposal(desk):
    check, tools = desk
    model = Scripted(_reply(WITH_REASON, "Asks what went wrong before the confirmation."))
    out = skill_editor.propose(SKILL, "also ask what went wrong", model, check=check, tools=tools)

    assert out["ok"] and out["attempts"] == 1 and not out["unchanged"]
    assert out["changed"] == "Asks what went wrong before the confirmation."
    assert 'reason: "What went wrong with it?"' in out["skill_md"]
    # Everything that is not the steps is kept exactly as written.
    assert out["skill_md"].startswith("---\nname: refund-order\ndescription: Refund an order.")
    assert "# The steps are the whole policy.\nsteps:\n" in out["skill_md"]
    assert out["skill_md"].endswith("---\nHelp the user get a refund.\n")


def test_the_model_is_told_the_language_the_tools_and_the_steps(desk):
    check, tools = desk
    model = Scripted(_reply(WITH_REASON))
    skill_editor.propose(SKILL, "  also ask   what went wrong ", model, check=check, tools=tools,
                         skills=["open-ticket"])
    system, user = (m.content for m in model.seen[0])
    assert "ONE of five kinds" in system and "CHANGED:" in system and "CANNOT:" in system
    assert "- issue_refund(order_id: string, reason: string (optional))  [CHANGES SOMETHING] - Refund the order." in user
    assert "- find_order(order_id: string)  [lookup] - Look an order up." in user
    assert "open-ticket" in user and "  - do: issue_refund" in user
    assert user.rstrip().endswith("Change them so that: also ask what went wrong")


def test_a_change_the_loader_refuses_goes_back_once_with_the_loaders_sentence(desk):
    check, tools = desk
    no_yes = WITH_REASON.replace("  - confirm: Refund this order?\n", "")
    model = Scripted(_reply(no_yes, "Removed the confirmation."), _reply(WITH_REASON, "Kept the confirmation."))
    out = skill_editor.propose(SKILL, "ask what went wrong and skip the confirmation", model, check=check, tools=tools)

    assert out["ok"] and out["attempts"] == 2 and "error" not in out, "what loads carries no objection"
    told = model.seen[1][-1].content
    assert told.startswith("The loader refused that: ") and "needs the user's yes first" in told
    assert "describe everything that differs from the steps you were first given" in told
    assert "  - confirm: Refund this order?" in out["skill_md"]


def test_a_model_that_keeps_getting_it_wrong_is_reported_not_trusted(desk):
    check, tools = desk
    invented = WITH_REASON.replace("  - do: issue_refund", "  - do: email_customer\n  - do: issue_refund")
    model = Scripted(_reply(invented), _reply(invented))
    out = skill_editor.propose(SKILL, "email the customer too", model, check=check, tools=tools)

    assert not out["ok"] and out["attempts"] == 2
    assert "`email_customer` is not a tool in the library" in out["error"]
    assert "do: email_customer" in out["skill_md"], "the proposal is shown, marked as not loading"


def test_a_model_that_says_it_cannot_changes_nothing(desk):
    check, tools = desk
    model = Scripted("CANNOT: there is no tool that sends email. Add one on the Tools page first.")
    out = skill_editor.propose(SKILL, "email the customer too", model, check=check, tools=tools)
    assert not out["ok"] and out["skill_md"] == SKILL
    assert out["cannot"] == "there is no tool that sends email. Add one on the Tools page first."


def test_a_model_that_gives_up_after_the_loader_refuses_changes_nothing(desk):
    check, tools = desk
    no_yes = WITH_REASON.replace("  - confirm: Refund this order?\n", "")
    model = Scripted(_reply(no_yes), "CANNOT: a tool that changes something needs the user's yes above it.")
    out = skill_editor.propose(SKILL, "skip the confirmation", model, check=check, tools=tools)
    assert not out["ok"] and out["attempts"] == 2
    assert out["cannot"] == "a tool that changes something needs the user's yes above it."


def test_a_reply_with_no_steps_is_asked_for_again(desk):
    check, tools = desk
    model = Scripted("Sure, I added the question.", _reply(WITH_REASON))
    out = skill_editor.propose(SKILL, "also ask what went wrong", model, check=check, tools=tools)
    assert out["ok"] and out["attempts"] == 2
    assert "there was no ```yaml fence" in model.seen[1][-1].content


def test_a_bare_list_is_put_under_its_key(desk):
    check, tools = desk
    bare = textwrap.indent(WITH_REASON.split("steps:\n", 1)[1], "  ")
    model = Scripted(f"<think>hmm</think>\n```yaml\n{bare}```\nCHANGED: Added a question.")
    out = skill_editor.propose(SKILL, "also ask what went wrong", model, check=check, tools=tools)
    assert out["ok"] and out["steps"].startswith("steps:\n  - ask: { order_id:")


def test_steps_are_replaced_where_they_are_and_only_there():
    after = SKILL.replace("---\nHelp", "compute:\n  big: \"find_order.large\"\n---\nHelp")
    out = skill_editor.with_steps(after, WITH_REASON)
    assert out.count("steps:") == 1 and out.index("steps:") < out.index("compute:")
    assert "choices: [damaged, late, changed_mind]\n  - confirm" in out and 'big: "find_order.large"\n---\n' in out
    assert skill_editor.current_steps(out) == WITH_REASON.rstrip("\n")


def test_a_file_with_no_steps_yet_gets_them_last():
    blank = "---\nname: new-skill\ndescription: A new skill. Use when testing.\n---\nBody.\n"
    out = skill_editor.with_steps(blank, "steps:\n  - say: Hello.")
    assert out == "---\nname: new-skill\ndescription: A new skill. Use when testing.\nsteps:\n  - say: Hello.\n---\nBody.\n"


def test_nothing_to_change_is_an_error_not_a_call_to_the_model():
    with pytest.raises(ValueError, match="say what to change"):
        skill_editor.propose(SKILL, "   ", Scripted(), check=lambda md: {"ok": True})


def test_the_language_the_model_is_told_is_the_loaders():
    """A word the loader takes and the editor never mentions is a word it will never write."""
    from aigenos_flow import shortform

    words = set(shortform.VERBS) | set().union(*shortform._OPTIONS.values()) | {"steps"}
    for word in words - {"widget"}:        # a designed widget is chosen on the canvas, not from a sentence
        assert f"{word}:" in skill_editor.LANGUAGE, word

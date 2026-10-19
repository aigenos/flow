"""docs/steps.md is run, not trusted.

The reference page prints a complete skill and its tools. They are written to a
temp folder exactly as printed and the conversation the page describes is run
against them. The table of words is compared with the loader's own vocabulary,
so a word cannot be added to one and not the other.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow import shortform
from aigenos_flow.approvals import ApprovalStore
from aigenos_flow.hosts.test_host import TestHost

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "docs" / "steps.md"

pytestmark = pytest.mark.skipif(not PAGE.is_file(), reason="docs/ not present (packaged install)")

FILE_BLOCK = re.compile(r"^\*\*`([^`\n]+)`\*\*\n\n```(\w*)\n(.*?)\n```", re.S | re.M)
WORDS = set(shortform.WORDS)


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture
def desk(page, tmp_path):
    for rel, _lang, body in FILE_BLOCK.findall(page):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body + "\n", encoding="utf-8")
    host = TestHost()
    host.approvals = ApprovalStore()
    agent = FlowAgent.from_dir(tmp_path / "skills", host=host)
    orders = next(m for n, m in sys.modules.items() if n.startswith("_flow_tools") and n.endswith("_orders"))
    host.queue_commands("hello")
    return agent.session("refund-order").user("hello"), orders


def test_the_page_prints_two_files_and_they_are_the_whole_example(page):
    assert [rel for rel, _, _ in FILE_BLOCK.findall(page)] == ["skills/refund-order/SKILL.md", "skills/tools/orders.py"]


def test_the_example_runs_the_way_the_page_says(desk):
    chat, orders = desk
    assert chat.awaiting == "order_id"
    assert [row["order_id"] for row in chat.widget["data"]["rows"]] == ["A-100", "A-200"]

    chat.submit(order_id="A-200")
    assert chat.replies[0] == "That order is outside the 30-day refund window. Pick another."
    chat.submit(order_id="A-999")
    assert chat.replies[0] == "I can't find that order. Pick one from the list."

    chat.submit(order_id="A-100").submit(reason="damaged")
    assert chat.awaiting == "notes"
    chat.submit(notes="")                              # "(optional)": may be left blank
    assert chat.awaiting == "confirm"
    card = str(chat.widget["spec"])
    assert "Refund" in card and "A-100" in card and "damaged" in card, "the yes is given to what will run"
    assert orders.REFUNDS == []

    chat.submit(confirm=True)                          # $249 is large: a manager decides
    assert chat.approval.who == "manager" and orders.REFUNDS == []
    chat.submit(confirm_2=True)                        # not the customer's to answer
    assert orders.REFUNDS == []
    chat.decide(True, by="sam")
    assert orders.REFUNDS == [("A-100", "damaged", "")]
    assert chat.replies == ["Sam said yes.", "Refunded. Reference RF-0001."] and chat.done


def test_what_the_page_says_the_engine_does_unasked(desk):
    chat, _ = desk
    skill = chat._agent.registry.get("refund-order")
    assert skill.picked_fields == ["order_id"], "a pick from a list is the user's alone"
    assert skill.fields["notes"].required is False and skill.fields["reason"].type == "enum"
    assert skill.fields["confirm_2"].asked_of == "manager"
    assert skill.readonly_tools == "find_order, list_orders", "the lookups say so themselves"


def test_the_table_of_words_is_the_loaders_vocabulary(page):
    table = page.split("## Every word", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"^\| `(\w+)` \|", table, flags=re.M))
    assert listed == WORDS, (sorted(listed - WORDS), sorted(WORDS - listed))
    assert f"**{len(WORDS)} in all**" in page
    assert f"one of the {len(WORDS)} " in page
    for kind in shortform._TYPES:
        if kind not in ("string", "integer", "boolean"):      # the long form's names for the same things
            assert f"`{kind}`" in page, kind


def test_every_long_form_word_the_loader_redirects_is_still_redirected():
    """The page promises that a long-form word in `steps:` is answered with what
    to write instead."""
    assert {"then_say", "else_say", "pick", "show", "link", "requires"} <= set(shortform._LONG_FORM)

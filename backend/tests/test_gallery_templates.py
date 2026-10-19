"""The Gallery's templates, run as conversations.

Each is a skill written as steps with its tools in its own scripts.py, so it
installs as one folder. Each shows some of the canvas's nodes: a repeat and an
approval (expense-report), a designed list, a shown widget and a chat
(subscription-help), a pick, shapes, a rule a lookup decides and a condition
(table-booking).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, guarantees, procedure
from aigenos_flow.approvals import ApprovalStore
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.integrations.flow import GALLERY_EXAMPLES

REPO = Path(__file__).resolve().parents[2]
GALLERY = REPO / "examples" / "gallery"

pytestmark = pytest.mark.skipif(not GALLERY.is_dir(), reason="repo examples not present (packaged install)")


def _start(tmp_path: Path, name: str):
    shutil.copytree(GALLERY / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__", "records.jsonl"))
    host = TestHost()
    host.approvals = ApprovalStore()
    agent = FlowAgent.from_dir(tmp_path, host=host)
    host.queue_commands("hello")
    return agent.session(name).user("hello"), host, agent


def test_the_gallery_offers_exactly_these_templates_all_written_as_steps():
    assert sorted(Path(rel).name for rel in GALLERY_EXAMPLES) == ["expense-report", "subscription-help", "table-booking"]
    for rel in GALLERY_EXAMPLES:
        text = (REPO / "examples" / rel / "SKILL.md").read_text(encoding="utf-8")
        assert isinstance(guarantees.frontmatter_of(text).get("steps"), list), rel


def test_expense_report_collects_lines_and_a_large_one_waits_for_a_manager(tmp_path):
    chat, host, _ = _start(tmp_path, "expense-report")
    chat.submit(report_for="Berlin trip")
    chat.submit(item="Hotel", category="lodging", amount=420).submit(lines_more=True)
    chat.submit(item="Dinner", category="meals", amount=95).submit(lines_more=False)
    assert "Total: $515.00." in chat.replies and chat.awaiting == "confirm"
    assert "2: Hotel, lodging, 420; Dinner, meals, 95" in str(chat.widget["spec"])
    chat.submit(confirm=True)
    assert chat.approval is not None and chat.approval.who == "manager", "$515 needs a manager"
    chat.decide(True, by="dana")
    assert chat.replies[-1] == "Submitted 2 expenses. Reference EXP-0001." and chat.done


def test_a_small_expense_report_needs_no_manager(tmp_path):
    chat, _, _ = _start(tmp_path, "expense-report")
    chat.submit(report_for="Team lunch").submit(item="Pizza", category="meals", amount=60).submit(lines_more=False)
    chat.submit(confirm=True)
    assert chat.done and chat.replies[-1] == "Submitted 1 expense. Reference EXP-0001."


def test_subscription_help_picks_shows_and_then_chats_about_that_one(tmp_path):
    chat, host, _ = _start(tmp_path, "subscription-help")
    chat.submit(holder="Ava Smith")
    assert chat.awaiting == "subscription_id" and chat.widget["widget"] == "subscription_grid"
    host.drain_pending_widgets()
    chat.submit(subscription_id="disney_plus")
    shown = json.dumps(host.drain_pending_widgets())
    assert "Disney+" in shown and "$27.98" in shown, "the designed details widget, its data bound"
    config = chat.state["chat_config"]
    assert config["tools"] == ["subscription_details"]
    assert config["pin"] == {"subscription_id": "subscription_id", "holder": "holder"}, \
        "every answer the chat's lookup reads is fixed to what the user chose"


def test_table_booking_refuses_a_full_slot_and_asks_a_large_party_for_a_card(tmp_path):
    chat, _, _ = _start(tmp_path, "table-booking")
    assert chat.widget is not None and chat.awaiting == "restaurant_id", "a list to pick from"
    chat.submit(restaurant_id="sakura").submit(day="2026-11-20").submit(time="19:00").submit(party_size=10)
    assert "That time is fully booked. Pick another time." in chat.replies and chat.awaiting == "time"
    chat.submit(time="18:30")
    assert chat.awaiting == "card_last4", "more than 8 people: a card holds the table"
    chat.submit(card_last4="4242")
    assert chat.awaiting == "confirm"
    chat.submit(confirm=True)
    assert chat.replies[-1] == "Booked. Reference TB-0001." and chat.done


def test_every_template_reads_back_as_a_procedure(tmp_path):
    for rel in GALLERY_EXAMPLES:
        name = Path(rel).name
        _, _, agent = _start(tmp_path / name, name)
        page = procedure.for_skill(agent.registry, name)
        assert page["steps"] and not page.get("unsupported"), name

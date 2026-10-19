"""plan-team-offsite: a skill with no Python anywhere that still does something.

Questions, two rules, the user's yes, a yes from finance over $5,000, and the
built-in tool that keeps a record. It used to end on "Go ahead with this plan?"
with nothing reading the answer, so Yes and No both ended on the same empty
screen.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.approvals import ApprovalStore
from aigenos_flow.hosts.test_host import TestHost

SKILL = Path(__file__).resolve().parents[2] / "examples" / "studio_agent" / "skills" / "plan-team-offsite"

pytestmark = pytest.mark.skipif(not SKILL.is_dir(), reason="examples/ not present (packaged install)")


@pytest.fixture
def offsite(tmp_path: Path):
    shutil.copytree(SKILL, tmp_path / "plan-team-offsite", ignore=shutil.ignore_patterns("records.jsonl"))
    host = TestHost()
    host.approvals = ApprovalStore()
    chat = FlowAgent.from_dir(tmp_path, host=host).session("plan-team-offsite")
    chat.records = tmp_path / "plan-team-offsite" / "records.jsonl"
    return chat


def _fill(chat, budget: int):
    return (chat.submit(team_name="Platform").submit(city="lisbon").submit(start_date="2026-11-09")
            .submit(attendees=12).submit(total_budget=budget))


def test_the_skill_is_one_file_with_no_python(offsite):
    folder = offsite.records.parent
    assert sorted(p.name for p in folder.iterdir()) == ["SKILL.md"]
    front = (folder / "SKILL.md").read_text(encoding="utf-8").split("\n---\n")[0]
    assert "steps:" in front and "fields:" not in front and "flow:" not in front


def test_a_small_budget_is_filed_on_the_users_yes_alone(offsite):
    chat = _fill(offsite, 1800)
    assert "Here's the plan: Platform in lisbon, starting 2026-11-09, 12 people, total budget $1800." in chat.replies
    assert chat.awaiting == "confirm" and not offsite.records.exists()

    chat.submit(confirm=True)
    record = json.loads(offsite.records.read_text(encoding="utf-8").splitlines()[0])
    assert chat.done and chat.replies == [f"Filed. Reference {record['reference']}."]
    assert record["team_name"] == "Platform" and record["total_budget"] == 1800 and chat.approval is None


def test_a_large_budget_waits_for_finance(offsite):
    chat = _fill(offsite, 6000).submit(confirm=True)

    request = chat.approval
    assert request is not None and request.who == "finance" and request.question == "Approve this offsite budget?"
    assert not chat.done and not offsite.records.exists(), "nothing is filed while finance has not answered"
    assert request.runs[0]["title"] == "Save request"
    assert {"label": "total budget", "shown": "6000"} in request.runs[0]["inputs"]

    chat.decide(True, by="Priya")
    assert chat.done and offsite.records.exists()
    assert chat.replies[0] == "Priya said yes." and chat.replies[1].startswith("Filed. Reference REQ-")


def test_finance_saying_no_files_nothing(offsite):
    chat = _fill(offsite, 6000).submit(confirm=True)
    chat.decide(False, by="Priya", note="Over the quarter's budget")
    assert chat.done and not offsite.records.exists()
    assert chat.replies == ["Priya said no: Over the quarter's budget. Nothing was done."]


def test_one_person_gets_the_skills_own_sentence_not_a_validator_line(offsite):
    chat = offsite.submit(team_name="Platform").submit(city="lisbon").submit(start_date="2026-11-09")
    chat.submit(attendees=1)

    # The rule reads only the headcount, so it is applied the moment the answer
    # is given, not two questions later.
    assert chat.awaiting == "attendees" and chat.state["fields"].get("attendees") is None
    assert any(line == "An offsite needs at least 2 people." for line in chat.replies)
    assert not any("must be" in line or "too low" in line or line == "Got it." for line in chat.replies)

    chat.submit(attendees=2)
    assert chat.awaiting == "total_budget"

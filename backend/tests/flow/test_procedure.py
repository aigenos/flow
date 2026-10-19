"""A skill read back as a procedure.

The page a person signs off is generated from the skill file with no model
involved: the steps in plain words, every way through the flow, and what must be
true before anything changes. These tests hold it to the file and to the engine:
a path it lists is a path the flow takes, and a tool it says waits for a yes is
one the engine will not run without it.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.procedure import english, for_skill, markdown
from aigenos_flow.skills import load_skills_dir

REPO = Path(__file__).resolve().parents[3]
STUDIO = REPO / "examples" / "studio_agent" / "skills"

TILL = '''
from aigenos_flow import readonly

PAID: list = []


@readonly
def quote(amount: float) -> dict:
    """What it would cost."""
    return {"needs_code": amount >= 1000, "fee": 2.0}


@readonly
def check_code(code: str) -> dict:
    """Is the code right?"""
    return {"valid": code == "481920"}


def pay(amount: float, payee: str) -> dict:
    """Send the money."""
    PAID.append((amount, payee))
    return {"reference": f"PAY-{len(PAID)}"}
'''

PAY = """\
---
name: pay-bill
description: Pay a bill. Use when the user wants to pay someone.
steps:
  - ask: { amount: "How much?" }
    check: { amount > 0: "Enter an amount above zero." }
  - ask: { payee: "Who is it for?" }
    choices: [mom, landlord]
  - ask: { code: "The 6-digit code?" }
    when: quote.needs_code
    type: secret
    pattern: '^[0-9]{6}$'
    check: { check_code.valid: "That code is not valid." }
  - ask: { gift: "Is it a gift?" }
    type: yes/no
  - say: A gift note will be added.
    when: gift
  - say: "The landlord gets a receipt."
    when: payee == 'landlord'
  - confirm: Pay this bill?
  - do: pay
  - say: "Paid. Reference {{pay.reference}}."
---
Pay a bill.
"""


def _page(tmp_path: Path, md: str = PAY, name: str = "pay-bill"):
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "till.py").write_text(TILL, encoding="utf-8")
    (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / name / "SKILL.md").write_text(md, encoding="utf-8")
    registry = load_skills_dir(tmp_path, strict=True)
    return for_skill(registry, name), registry


# --------------------------------------------------------------------------- the steps, in words
@pytest.mark.parametrize("expr,said", [
    ("amount >= 1000", "amount is 1000 or more"),
    ("amount > 0", "amount is more than 0"),
    ("nights <= 7", "nights is 7 or less"),
    ("pay_to == 'someone_new'", "pay to is someone new"),
    ("plan != 'gold'", "plan is not gold"),
    ("quote_payment.needs_code", "quote payment says needs code"),
    ("not lookup_order.found", "lookup order does not say found"),
    ("gift", "gift"),
    ("not gift", "gift is no"),
    ("nights >= 1 and nights <= 7", "nights is 1 or more and nights is 7 or less"),
    ("a == 1 or (b and c)", "a is 1 or (b and c)"),
    ("someone_home == False", "someone home is no"),
    ("len(x) > 3", "len(x) > 3"),          # not worded: shown exactly as written
])
def test_a_condition_is_worded_or_left_exactly_as_written(expr, said):
    assert english(expr) == said


def test_each_step_is_said_in_plain_words_with_what_it_requires(tmp_path):
    page, _ = _page(tmp_path)
    steps = {s["n"]: s for s in page["steps"]}
    assert [s["kind"] for s in page["steps"]] == ["ask", "ask", "ask", "ask", "say", "say", "confirm", "do", "say"]

    assert steps[1]["text"] == "Ask: “How much?”"
    assert "The answer is a number." in steps[1]["notes"]
    assert "Refused, and asked again, unless amount is more than 0: “Enter an amount above zero.”" in steps[1]["notes"]
    assert steps[1].get("user_only"), "a tool that decides a branch reads the amount"

    assert steps[2]["notes"] == ["The answer is one of: mom, landlord.",
                                 "Only the user can give this answer. A value the user did not type or pick is dropped."]
    assert steps[3]["when"] == [{"expr": "quote.needs_code", "text": "quote says needs code"}]
    assert "The answer is a secret: masked on screen, never logged." in steps[3]["notes"]
    assert "It must look like: 6 digits." in steps[3]["notes"]

    assert steps[7]["text"] == "Ask for a yes: “Pay this bill?”"
    assert steps[7]["notes"][0].startswith("A yes lets `pay` run.")
    assert steps[8]["text"] == "Run `pay`: Send the money."
    assert steps[8]["notes"] == ["This changes something. It runs once, and only after the yes at step 7."]
    assert page["lookups"] == [{"name": "quote", "description": "What it would cost."},
                               {"name": "check_code", "description": "Is the code right?"}]


# --------------------------------------------------------------------------- every way through
def test_every_way_through_is_listed_and_nothing_impossible_is(tmp_path):
    page, _ = _page(tmp_path)
    paths = page["paths"]
    assert [c["expr"] for c in paths["conditions"]] == ["quote.needs_code", "gift", "payee == 'landlord'"]
    # Three independent yes/no questions: eight ways, no more and no fewer.
    assert len(paths["rows"]) == 8
    assert paths["exact"] is False, "one of them is a tool's answer, taken both ways"
    for row in paths["rows"]:
        assert row["runs"] == ["pay"] and row["yeses"] == 1
        assert ("code" in row["asks"]) is row["holds"]["quote.needs_code"]
    longest = paths["rows"][0]
    assert longest["steps"] == [1, 2, 3, 4, 5, 6, 7, 8, 9] and all(longest["holds"].values())


def test_choices_that_exclude_each_other_never_make_a_path(tmp_path):
    md = """\
---
name: front-door
description: Route the user. Use for testing.
steps:
  - ask: { kind: "New or saved?" }
    choices: [new, saved]
  - say: First we add them.
    when: kind == 'new'
  - say: Here are your payees.
    when: kind == 'saved'
  - ask: { amount: "How much?" }
    type: number
  - say: That is a large one.
    when: amount >= 1000
  - say: That is a very large one.
    when: amount >= 5000
---
Route.
"""
    page, _ = _page(tmp_path, md, "front-door")
    rows = page["paths"]["rows"]
    # new/saved (never both, never neither) x small / large / very large.
    assert len(rows) == 6 and page["paths"]["exact"] is True
    for row in rows:
        holds = row["holds"]
        assert holds["kind == 'new'"] != holds["kind == 'saved'"]
        assert not (holds["amount >= 5000"] and not holds["amount >= 1000"]), "over 5000 is over 1000"


def test_a_listed_path_is_a_path_the_flow_takes(tmp_path):
    """Drive the real flow down each listed path and compare what it asked."""
    page, registry = _page(tmp_path)
    for row in page["paths"]["rows"]:
        holds = row["holds"]
        host = TestHost()
        agent = FlowAgent(load_skills_dir(tmp_path), host)
        host.queue_commands("hello")
        chat = agent.session("pay-bill").user("hello")
        answers = {
            "amount": 5000 if holds["quote.needs_code"] else 50,
            "payee": "landlord" if holds["payee == 'landlord'"] else "mom",
            "code": "481920", "gift": holds["gift"], "confirm": True,
        }
        asked: list[str] = []
        said: list[str] = []
        for _ in range(12):
            if chat.done:
                break
            asked.append(chat.awaiting)
            chat.submit(**{chat.awaiting: answers[chat.awaiting]})
            said.extend(chat.replies)
        assert chat.done
        assert asked == [*row["asks"], "confirm"], (holds, asked)
        assert ("A gift note will be added." in said) is holds["gift"]
        assert ("The landlord gets a receipt." in said) is holds["payee == 'landlord'"]


# --------------------------------------------------------------------------- before anything changes
def test_what_must_be_true_before_a_tool_changes_anything(tmp_path):
    page, _ = _page(tmp_path)
    (write,) = page["writes"]
    assert write["tool"] == "pay" and write["step"] == 8
    assert write["needs"] == [
        "The user said yes to “Pay this bill?” (step 7).",
        "Amount is more than 0 (step 1).",
        "Check code says valid (step 3) (when quote says needs code).",
    ]
    assert write["inputs"] == [
        {"name": "amount", "from": "the answer at step 1", "who": "only the user"},
        {"name": "payee", "from": "the answer at step 2", "who": "only the user"},
    ]


def test_a_yes_from_someone_else_reads_as_one(tmp_path):
    md = PAY.replace("  - confirm: Pay this bill?\n", """  - confirm: Pay this bill?
  - confirm: Approve this payment?
    by: finance
    when: quote.needs_code
""")
    page, _ = _page(tmp_path, md)
    step = next(s for s in page["steps"] if s.get("asked_of"))
    assert step["text"] == "Ask finance for a yes: “Approve this payment?”" and not step.get("user_only")
    assert step["notes"][0] == ("The flow waits here until finance answers. Neither the user in the "
                                "conversation nor the model can answer for them.")
    (write,) = page["writes"]
    assert write["needs"][:2] == [
        "The user said yes to “Pay this bill?” (step 7).",
        "Finance said yes to “Approve this payment?” (step 8) (asked only when quote says needs code).",
    ]
    assert {r["yeses"] for r in page["paths"]["rows"]} == {1, 2}


def test_the_page_is_a_document(tmp_path):
    page, _ = _page(tmp_path)
    text = markdown(page)
    assert text.startswith("# Pay bill\n\nPay a bill. Use when the user wants to pay someone.\n\n## The procedure\n")
    assert "7. Ask for a yes: “Pay this bill?”" in text
    assert "3. Ask: “The 6-digit code?” *(only when quote says needs code)*" in text
    assert "8 ways through this flow, taking each tool result both ways." in text
    assert "## Before anything changes" in text and "## What it looks up" in text
    assert page["markdown"] == text


# --------------------------------------------------------------------------- the shipped skills
@pytest.mark.skipif(not STUDIO.is_dir(), reason="examples/ not present (packaged install)")
@pytest.mark.parametrize("name,ways,writes", [
    ("pay-someone", 2, ["send_payment"]),
    ("add-payee", 1, ["save_payee"]),
    ("send-money", 2, []),
    ("book-meeting-room", 1, ["book_room"]),
    ("refund-desk", 2, ["issue_refund"]),
    ("plan-team-offsite", 2, ["save_request"]),
    ("conference-trip", 2, ["hold_flight", "hold_hotel", "reserve_cab", "take_payment"]),
])
def test_every_shipped_skill_reads_back_as_a_procedure(name, ways, writes, monkeypatch):
    monkeypatch.setenv("TRIP_OFFLINE", "1")
    registry = load_skills_dir(STUDIO)
    page = for_skill(registry, name)
    assert "unsupported" not in page
    assert len(page["paths"]["rows"]) == ways, [r["holds"] for r in page["paths"]["rows"]]
    assert [w["tool"] for w in page["writes"]] == writes
    for write in page["writes"]:
        assert write["needs"][0].startswith("The user said yes to"), "every tool that changes something waits for a yes"
    assert page["markdown"].count("\n## ") >= 2


@pytest.mark.skipif(not STUDIO.is_dir(), reason="examples/ not present (packaged install)")
def test_a_skill_that_is_not_steps_says_so():
    page = for_skill(load_skills_dir(STUDIO), "list-rooms")
    assert page["steps"] == [] and "not written as steps" in page["unsupported"]

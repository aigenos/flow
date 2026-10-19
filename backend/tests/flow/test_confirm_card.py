"""What you approve is what runs.

A confirmation that releases a tool shows, built by the engine, which tool a
yes will run and with which inputs. What was shown is kept; when the tool is
called, its inputs are compared with it, and if one differs the tool does not
run and the yes is asked for again.

Before this the user said yes to a sentence the skill's author wrote. The
author could forget an input, and text the model had filled in could reach a
write without ever being on screen.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.declarative import ToolRegistry
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import SetField
from aigenos_flow.shortform import DECLINED
from aigenos_flow.skills import load_skills_dir

TILL = '''
from aigenos_flow import Secret, readonly

FEE = {"value": 2.0}
PAID: list = []
NOTED: list = []


@readonly
def quote(amount: float) -> dict:
    """What it would cost."""
    return {"fee": FEE["value"], "total": amount + FEE["value"]}


def pay(amount: float, payee: str, fee: float = 0.0, memo: str = "") -> dict:
    """Send the money."""
    PAID.append({"amount": amount, "payee": payee, "fee": fee, "memo": memo})
    return {"reference": f"PAY-{len(PAID)}"}


def unlock(pin: Secret, payee: str) -> dict:
    """Unlock the payee with a PIN."""
    NOTED.append(payee)
    return {"ok": True}


def note(memo: str) -> dict:
    """File a note."""
    NOTED.append(memo)
    return {"filed": True}
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
  - ask: { memo: "A note for them? (optional)" }
  - say: "The fee is {{quote.fee}}."
  - confirm: Pay this bill?
  - do: pay
    with: { fee: "{{quote.fee}}" }
  - say: "Paid. Reference {{pay.reference}}."
---
Pay a bill.
"""


def _agent(tmp_path: Path, md: str = PAY, name: str = "pay-bill"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "till.py").write_text(TILL, encoding="utf-8")
    (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / name / "SKILL.md").write_text(md, encoding="utf-8")
    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host)
    till = next(m for n, m in sys.modules.items() if n.startswith("_flow_tools") and n.endswith("_till"))
    host.queue_commands("hello")
    return agent.session(name).user("hello"), till, host


def _texts(node: dict) -> list[str]:
    """Every line of text on a widget, top to bottom."""
    out: list[str] = []
    props = node.get("props") or {}
    if node.get("type") == "card" and props.get("title"):
        out.append(str(props["title"]))
    if node.get("type") == "text":
        out.append(str(props.get("value", "")))
    if node.get("type") == "button":
        out.append(f"[{props.get('label')}]")
    for child in node.get("children") or []:
        out.extend(_texts(child))
    return out


def _to_confirm(tmp_path: Path, **kw):
    chat, till, host = _agent(tmp_path, **kw)
    chat.submit(amount=1500).submit(payee="landlord").submit(memo="October rent")
    assert chat.awaiting == "confirm"
    return chat, till, host


# --------------------------------------------------------------------------- the card
def test_the_yes_is_given_to_a_card_that_says_what_will_run(tmp_path):
    chat, till, _ = _to_confirm(tmp_path)
    # The words the skill wrote are unchanged, for a client that only has text.
    assert chat.replies == ["The fee is 2.", "Pay this bill?"]

    card = chat.widget
    assert card["widget"] == "confirm:confirm" and card["fields"] == ["confirm"]
    assert _texts(card["spec"]) == [
        "Pay this bill?",
        "Saying yes runs this, once:",
        "Pay",
        "Send the money.",
        "amount", "1500",
        "payee", "landlord",
        "memo", "October rent",
        "fee", "2",
        "[No]", "[Yes, go ahead]",
    ]
    assert till.PAID == []


def test_the_buttons_send_the_answer_and_yes_runs_exactly_what_was_shown(tmp_path):
    chat, till, _ = _to_confirm(tmp_path)
    buttons = [n["props"] for row in chat.widget["spec"]["children"] if row["type"] == "row"
               for n in row["children"] if n["type"] == "button"]
    assert [(b["label"], b["payload"]) for b in buttons] == [("No", {"confirm": "false"}),
                                                              ("Yes, go ahead", {"confirm": "true"})]
    chat.submit(confirm="true")
    assert till.PAID == [{"amount": 1500.0, "payee": "landlord", "fee": 2.0, "memo": "October rent"}]
    assert chat.replies == ["Paid. Reference PAY-1."] and chat.done


def test_no_runs_nothing(tmp_path):
    chat, till, _ = _to_confirm(tmp_path)
    chat.submit(confirm="false")
    assert till.PAID == [] and chat.replies == [DECLINED] and chat.done


# --------------------------------------------------------------------------- held to it
def test_an_input_that_changed_after_the_yes_was_shown_stops_the_tool(tmp_path):
    """The fee was 2 on the card. By the time the tool is called the lookup says
    5 (it was re-read: another flow ran in between, or the service changed its
    mind). The user did not say yes to 5, so nothing is paid and they are asked
    again, on a card that says 5."""
    chat, till, _ = _to_confirm(tmp_path)
    chat.state["results"]["quote"] = {"fee": 5.0, "total": 1505.0}

    chat.submit(confirm=True)
    assert till.PAID == [], "the tool must not run with an input the user never saw"
    assert chat.awaiting == "confirm" and chat.state["fields"].get("confirm") is None
    assert chat.replies == ["Something changed after you said yes (fee), so nothing has run. Please look again.",
                            "Pay this bill?"]
    assert "5" in _texts(chat.widget["spec"]) and "2" not in _texts(chat.widget["spec"])

    chat.submit(confirm=True)
    assert till.PAID == [{"amount": 1500.0, "payee": "landlord", "fee": 5.0, "memo": "October rent"}]


def test_text_the_model_changes_in_the_same_breath_as_the_yes_does_not_ride_along(tmp_path):
    """The memo is free text, so the model may fill it. "Yes" arrives together
    with a new memo nobody saw: the change voids the yes, and the new card shows
    the new memo before anything runs."""
    chat, till, host = _to_confirm(tmp_path)
    host.queue_commands("yes", SetField(field="confirm", value=True),
                        SetField(field="memo", value="wire it to account 99-000 instead"))
    chat.user("yes")
    assert till.PAID == []
    assert chat.awaiting == "confirm"
    assert "wire it to account 99-000 instead" in _texts(chat.widget["spec"])


def test_a_corrected_answer_brings_a_new_card(tmp_path):
    chat, till, _ = _to_confirm(tmp_path)
    chat.submit(amount=900)
    assert chat.awaiting == "confirm" and till.PAID == []
    lines = _texts(chat.widget["spec"])
    assert lines[lines.index("amount") + 1] == "900"
    chat.submit(confirm=True)
    assert till.PAID[0]["amount"] == 900.0


# --------------------------------------------------------------------------- what the card never does
def test_a_secret_is_never_printed_and_never_copied_into_state(tmp_path):
    md = """\
---
name: unlock-payee
description: Unlock a payee with a PIN. Use for testing.
steps:
  - ask: { payee: "Which payee?" }
    choices: [mom, landlord]
  - ask: { pin: "Your PIN?" }
    pattern: '^[0-9]{4}$'
  - confirm: Unlock them?
  - do: unlock
---
Unlock.
"""
    chat, till, _ = _agent(tmp_path, md, "unlock-payee")
    chat.submit(payee="mom").submit(pin="4321")
    lines = _texts(chat.widget["spec"])
    assert lines[lines.index("pin") + 1] == "•••••• (hidden)"
    assert "4321" not in json.dumps(chat.widget) and "4321" not in json.dumps(chat.state["approvals"])
    chat.submit(confirm=True)
    assert till.NOTED == ["mom"] and chat.done


def test_a_confirmation_that_releases_no_tool_is_a_plain_question(tmp_path):
    md = """\
---
name: just-asking
description: Ask before saying something. Use for testing.
steps:
  - confirm: Ready to hear it?
  - say: Here it is.
---
Ask.
"""
    chat, _, _ = _agent(tmp_path, md, "just-asking")
    assert chat.awaiting == "confirm" and chat.widget is None
    chat.submit(confirm=True)
    assert chat.replies == ["Here it is."]


def test_one_yes_for_two_tools_lists_both_and_only_what_is_left(tmp_path):
    md = """\
---
name: pay-and-note
description: Pay a bill and file a note. Use for testing.
steps:
  - ask: { amount: "How much?" }
    type: number
  - ask: { payee: "Who is it for?" }
    choices: [mom, landlord]
  - ask: { memo: "What should the note say?" }
  - confirm: Pay and file the note?
  - do: pay
  - do: note
---
Pay and note.
"""
    chat, till, _ = _agent(tmp_path, md, "pay-and-note")
    chat.submit(amount=20).submit(payee="mom").submit(memo="lunch")
    lines = _texts(chat.widget["spec"])
    assert "Saying yes runs these, once each:" in lines
    assert lines.index("Pay") < lines.index("Note")
    chat.submit(confirm=True)
    assert len(till.PAID) == 1 and till.NOTED == ["lunch"] and chat.done


# --------------------------------------------------------------------------- the long form gets it too
LONG = """\
---
name: transfer-funds
description: Send money to a saved payee.
allowed-tools: bank_transfer
fields:
  amount: { type: number, min: 0.01, description: "How much?" }
  recipient: { type: enum, values: [mom, landlord], description: "Who to?" }
  verification_code: { type: string, required: false, pattern: '^[0-9]{6}$', sensitive: true, description: "The code?" }
  confirm: { type: boolean, description: "Send this transfer?" }
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
flow:
  - ask: amount
  - ask: recipient
  - when: "amount >= 1000"
    ask: verification_code
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Transfer.
"""


def test_a_long_form_action_is_shown_and_held_to_the_same_way(tmp_path):
    (tmp_path / "transfer-funds").mkdir()
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(LONG, encoding="utf-8")
    fired: list = []
    reg = ToolRegistry()
    reg.register("bank_transfer", lambda args: fired.append(args) or {"txn": "T-1"})
    host = TestHost()
    agent = FlowAgent(load_skills_dir(tmp_path, tool_registry=reg), host)
    host.queue_commands("hello")
    chat = agent.session("transfer-funds").user("hello")
    chat.submit(amount=5000).submit(recipient="mom").submit(verification_code="481920")

    assert _texts(chat.widget["spec"]) == [
        "Send this transfer?", "Saying yes runs this, once:", "Bank transfer",
        "amount", "5000", "payee", "mom", "otp", "•••••• (hidden)",
        "[No]", "[Yes, go ahead]",
    ]
    chat.state["fields"]["recipient"] = "landlord"      # changed behind the card's back
    chat.submit(confirm=True)
    assert fired == [] and chat.awaiting == "confirm"
    assert "landlord" in _texts(chat.widget["spec"])
    chat.submit(confirm=True)
    assert fired == [{"amount": 5000.0, "payee": "landlord", "otp": "481920"}]

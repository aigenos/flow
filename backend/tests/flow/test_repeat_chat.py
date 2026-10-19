"""`repeat:`, `chat:` and a designed widget that is only shown.

    - repeat: { travellers: "Add another traveller?" }
      steps:
        - ask: { traveller_name: "..." }
        - ask: { passport: "..." }

The questions under a repeat are asked once per item; when they are answered,
the repeat's question is asked, and a yes starts the next item. Each item is
one record of the list, which the steps after the repeat read by its name.

    - chat: "Ask me anything about this subscription."
      tools: [subscription_details]

The last step: the model answers questions with lookups only, and a tool's
input named like an answer is fixed to that answer.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, procedure
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.skills import load_skills_dir

TRAVEL = '''
from aigenos_flow import readonly

BOOKED: list = []


@readonly
def check_passport(passport: str) -> dict:
    """Is this passport number valid?"""
    return {"valid": not passport.startswith("X0")}


def book_tickets(travellers: list, flight: str) -> dict:
    """Book a seat for every traveller."""
    BOOKED.append((flight, [dict(t) for t in travellers]))
    return {"reference": f"BK-{len(BOOKED)}", "count": len(travellers)}
'''

GROUP = """\
---
name: book-group
description: Book seats for a group. Use when the user wants to book for several people.
steps:
  - ask: { flight: "Which flight?" }
    choices: [UA100, UA200]
  - repeat: { travellers: "Add another traveller?" }
    steps:
      - ask: { traveller_name: "What is the traveller's full name?" }
      - ask: { passport: "Passport number?" }
        pattern: '^[A-Z0-9]{6,9}$'
        check: { check_passport.valid: "That passport is not valid. Check the number." }
  - say: "{{travellers}}"
    title: Travellers
  - confirm: Book these seats?
  - do: book_tickets
  - say: "Booked {{book_tickets.count}} seats. Reference {{book_tickets.reference}}."
---
Book seats for a group.
"""


def _folder(tmp_path: Path, md: str, tools: str = TRAVEL, name: str = "book-group") -> Path:
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "travel.py").write_text(tools, encoding="utf-8")
    (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / name / "SKILL.md").write_text(md, encoding="utf-8")
    return tmp_path


def _start(tmp_path: Path, md: str = GROUP, name: str = "book-group"):
    host = TestHost()
    agent = FlowAgent.from_dir(_folder(tmp_path, md, name=name), host=host)
    travel = next(m for n, m in sys.modules.items() if n.startswith("_flow_tools") and n.endswith("_travel"))
    host.queue_commands("hello")
    return agent.session(name).user("hello"), travel, host, agent


def _refused(tmp_path: Path, md: str, needle: str) -> None:
    with pytest.raises(ValueError, match=needle):
        load_skills_dir(_folder(tmp_path, md), strict=True)


# --------------------------------------------------------------------------- repeat: one item at a time
def test_a_repeat_collects_one_record_per_item_and_the_tool_gets_the_list(tmp_path):
    chat, travel, host, _ = _start(tmp_path)
    assert chat.awaiting == "flight"
    chat.submit(flight="UA100")
    assert chat.awaiting == "traveller_name"
    chat.submit(traveller_name="Ann Lee").submit(passport="X0123456")
    assert chat.replies[0] == "That passport is not valid. Check the number." and chat.awaiting == "passport"
    chat.submit(passport="P1234567")
    assert chat.awaiting == "travellers_more" and chat.replies[-1] == "Add another traveller?"

    chat.submit(travellers_more=True)                     # a second traveller
    assert chat.awaiting == "traveller_name", "a yes starts the next item"
    chat.submit(traveller_name="Bob Ray").submit(passport="Q7654321").submit(travellers_more=False)
    assert chat.awaiting == "confirm"
    assert travel.BOOKED == []

    card = str(chat.widget["spec"])
    assert "Book tickets" in card and "Ann Lee, P1234567; Bob Ray, Q7654321" in card, \
        "the yes is given to the whole list"
    chat.submit(confirm=True)
    assert travel.BOOKED == [("UA100", [
        {"traveller_name": "Ann Lee", "passport": "P1234567"},
        {"traveller_name": "Bob Ray", "passport": "Q7654321"},
    ])]
    assert chat.replies[-1] == "Booked 2 seats. Reference BK-1." and chat.done


def test_the_list_is_shown_as_a_table(tmp_path):
    chat, _, host, _ = _start(tmp_path)
    chat.submit(flight="UA200").submit(traveller_name="Ann Lee").submit(passport="P1234567")
    host.drain_pending_widgets()
    chat.submit(travellers_more=False)
    shown = json.dumps(host.drain_pending_widgets())
    assert "Travellers" in shown and "Ann Lee" in shown and "P1234567" in shown


def test_only_the_user_says_whether_there_is_another(tmp_path):
    _, _, _, agent = _start(tmp_path)
    skill = agent.registry.get("book-group")
    assert skill.fields["travellers_more"].must_elicit, "it decides how often the steps run"
    assert skill.fields["travellers_more"].type == "boolean"


def test_one_more_after_the_list_is_closed_reopens_it(tmp_path):
    chat, travel, _, _ = _start(tmp_path)
    chat.submit(flight="UA100").submit(traveller_name="Ann Lee").submit(passport="P1234567")
    chat.submit(travellers_more=False)
    assert chat.awaiting == "confirm"
    chat.submit(travellers_more=True)                     # "actually, add one more"
    assert chat.awaiting == "traveller_name"
    chat.submit(traveller_name="Cy Ng").submit(passport="R1122334").submit(travellers_more=False)
    assert chat.awaiting == "confirm" and "Ann Lee, P1234567; Cy Ng, R1122334" in str(chat.widget["spec"])
    chat.submit(confirm=True)
    assert [t["traveller_name"] for t in travel.BOOKED[0][1]] == ["Ann Lee", "Cy Ng"]


def test_changing_another_answer_keeps_the_list_and_asks_for_the_yes_again(tmp_path):
    """A correction clears what was looked up and every yes. The list is the
    user's answers, item by item: it stays, and it is not reopened."""
    chat, travel, _, _ = _start(tmp_path)
    chat.submit(flight="UA100").submit(traveller_name="Ann Lee").submit(passport="P1234567")
    chat.submit(travellers_more=False).submit(confirm=True)
    assert travel.BOOKED and travel.BOOKED[0][0] == "UA100"
    chat2, travel2, _, _ = _start(tmp_path / "again")
    chat2.submit(flight="UA100").submit(traveller_name="Ann Lee").submit(passport="P1234567")
    chat2.submit(travellers_more=False)
    chat2.submit(flight="UA200")                            # a correction, at the confirmation
    assert chat2.awaiting == "confirm", "the list is still closed"
    assert "1: Ann Lee, P1234567" in str(chat2.widget["spec"]) and "UA200" in str(chat2.widget["spec"])
    chat2.submit(confirm=True)
    assert travel2.BOOKED[-1] == ("UA200", [{"traveller_name": "Ann Lee", "passport": "P1234567"}])


def test_no_before_anything_is_collected_still_asks_for_the_first_item(tmp_path):
    chat, _, _, _ = _start(tmp_path)
    chat.submit(flight="UA100").submit(travellers_more=False)
    assert chat.awaiting == "traveller_name", "a repeat collects at least one item"


def test_the_procedure_reads_a_repeat(tmp_path):
    registry = load_skills_dir(_folder(tmp_path, GROUP), strict=True)
    page = procedure.for_skill(registry, "book-group")
    texts = [s["text"] for s in page["steps"]]
    assert texts[1] == ("Repeat the questions below once per item, until the answer to "
                        "“Add another traveller?” is no.")
    assert "Asked once per item of `travellers`." in page["steps"][2]["notes"]
    (write,) = page["writes"]
    assert {"name": "travellers", "from": "the list collected at step 2",
            "who": "the user, one item at a time"} in write["inputs"]


@pytest.mark.parametrize("change,needle", [
    # an item's answer means nothing after the repeat
    (lambda md: md.replace('  - say: "{{travellers}}"\n    title: Travellers\n',
                           '  - say: "Last passport: {{passport}}"\n'),
     "reads `passport`, which belongs to one item of `repeat: travellers`"),
    (lambda md: md.replace("def book_tickets(travellers: list", "def book_tickets(passport: str, travellers: list"),
     "needs `passport`, which is one item's answer inside `repeat: travellers`"),
    # only questions inside
    (lambda md: md.replace("      - ask: { traveller_name:", "      - say: Next traveller.\n      - ask: { traveller_name:"),
     "Put the `say:` before or after the repeat"),
    (lambda md: md.replace("      - ask: { traveller_name:", "      - confirm: Add them?\n      - ask: { traveller_name:"),
     "Confirm once, after the repeat"),
    (lambda md: md.replace("        pattern: '^[A-Z0-9]{6,9}$'\n", "        type: secret\n"),
     "a secret cannot be one item of a list"),
    (lambda md: md.replace("  - repeat: { travellers: \"Add another traveller?\" }\n",
                           "  - repeat: travellers\n"),
     "the name of the list, and the question that starts another item"),
    (lambda md: md.replace("      - ask: { traveller_name:",
                           "      - repeat: { bags: \"Another bag?\" }\n        steps:\n          - ask: { bag: \"Weight?\" }\n      - ask: { traveller_name:"),
     "cannot hold another `repeat:`"),
])
def test_what_a_repeat_refuses(tmp_path, change, needle):
    md = GROUP
    tools = TRAVEL
    changed = change(md)
    if changed == md:
        tools = change(TRAVEL)
        assert tools != TRAVEL
    else:
        md = changed
    with pytest.raises(ValueError, match=needle.replace("(", r"\(").replace(")", r"\)").replace("`", ".")):
        load_skills_dir(_folder(tmp_path, md, tools), strict=True)


# --------------------------------------------------------------------------- chat: questions, lookups only
SUBS = '''
from aigenos_flow import readonly


@readonly
def subscription_details(service: str, holder: str) -> dict:
    """Everything about one subscription."""
    return {"plan": "premium", "service": service}


@readonly
def plan_catalog(topic: str = "") -> dict:
    """The plans on offer."""
    return {"plans": ["basic", "premium"]}


def cancel_subscription(service: str) -> dict:
    """Cancel it."""
    return {"ok": True}
'''

ABOUT = """\
---
name: my-subscription
description: Answer questions about one subscription. Use when the user asks about their subscription.
steps:
  - ask: { holder: "Whose account is it?" }
    pattern: '^[A-Za-z .-]{1,40}$'
  - ask: { service: "Which subscription?" }
    choices: [netflix, spotify]
  - chat: "Ask me anything about this subscription."
    tools: [subscription_details, plan_catalog]
---
Answer questions about a subscription.
"""


def _subs(tmp_path: Path, md: str = ABOUT):
    return _folder(tmp_path, md, SUBS, name="my-subscription")


def test_a_chat_is_the_last_step_with_its_lookups_and_fixed_inputs(tmp_path):
    host = TestHost()
    agent = FlowAgent.from_dir(_subs(tmp_path), host=host)
    skill = agent.registry.get("my-subscription")
    host.queue_commands("hello")
    chat = agent.session("my-subscription").user("hello")
    chat.submit(holder="Ava Smith").submit(service="netflix")
    config = chat.state["chat_config"]
    assert config["tools"] == ["subscription_details", "plan_catalog"]
    assert config["pin"] == {"holder": "holder", "service": "service"}, "the model cannot change these"
    assert "Ask me anything about this subscription." in chat.replies
    assert set(skill.chat_tools) == {"subscription_details", "plan_catalog"}


def test_the_procedure_reads_a_chat(tmp_path):
    page = procedure.for_skill(load_skills_dir(_subs(tmp_path), strict=True), "my-subscription")
    last = page["steps"][-1]
    assert last["text"] == "Answer the user's questions: “Ask me anything about this subscription.”"
    assert "Fixed to the answers above, so the model cannot change them: `service`, `holder`." in last["notes"]
    assert any("only look things up" in n for n in last["notes"])


@pytest.mark.parametrize("change,needle", [
    (lambda md: md.replace("tools: [subscription_details, plan_catalog]", "tools: [cancel_subscription]"),
     "can only use tools that look things up"),
    (lambda md: md.replace("  - chat: \"Ask me anything about this subscription.\"\n"
                           "    tools: [subscription_details, plan_catalog]\n",
                           "  - chat: \"Ask me anything about this subscription.\"\n"
                           "    tools: [plan_catalog]\n  - say: Bye.\n"),
     "is the last step of a skill"),
    (lambda md: md.replace("    pattern: '^[A-Za-z .-]{1,40}$'\n", ""),
     "is free text"),
    (lambda md: md.replace("tools: [subscription_details, plan_catalog]", "tools: []"),
     "lists the lookups the chat may use"),
])
def test_what_a_chat_refuses(tmp_path, change, needle):
    with pytest.raises(ValueError, match=needle):
        load_skills_dir(_subs(tmp_path, change(ABOUT)), strict=True)


# --------------------------------------------------------------------------- a designed widget, shown
SHOWN = """\
---
name: show-order
description: Show an order. Use when testing.
steps:
  - ask: { order_id: "Which order?" }
    pattern: '^A-[0-9]{3}$'
  - say: "Here is your order."
    widget: order_card
    with: { order_id: "{{order_id}}", total: "{{price.total}}" }
  - ask: { note: "Anything else we should know? (optional)" }
---
Show an order.
"""

PRICE = '''
from aigenos_flow import readonly


@readonly
def price(order_id: str) -> dict:
    return {"total": 249}
'''


def test_a_designed_widget_is_shown_with_its_data_and_nothing_waits_on_it(tmp_path, monkeypatch):
    from aigenos_flow.engine import loader

    widgets = tmp_path / "widgets"
    widgets.mkdir()
    (widgets / "order_card.json").write_text(json.dumps({
        "id": "order_card", "version": "1.0.0", "description": "An order, shown.",
        "spec": {"type": "card", "props": {"title": "Order {{order_id}}", "subtitle": "Total ${{total}}"}},
    }), encoding="utf-8")
    monkeypatch.setattr(loader, "WIDGET_DIR", widgets)

    host = TestHost()
    agent = FlowAgent.from_dir(_folder(tmp_path, SHOWN, PRICE, name="show-order"), host=host)
    host.queue_commands("hello")
    chat = agent.session("show-order").user("hello")
    host.drain_pending_widgets()
    chat.submit(order_id="A-100")
    shown = host.drain_pending_widgets()
    assert len(shown) == 1 and shown[0]["type"] == "card"
    assert shown[0]["props"] == {"title": "Order A-100", "subtitle": "Total $249"}, "the designed widget, its data bound"
    assert "Here is your order." in chat.replies and chat.awaiting == "note", "shown, not answered"
    chat.submit(order_id="A-100", note="")
    assert host.drain_pending_widgets() == [], "shown once"


def test_a_widget_the_catalog_does_not_have_shows_its_data(tmp_path, monkeypatch):
    from aigenos_flow.engine import loader

    monkeypatch.setattr(loader, "WIDGET_DIR", tmp_path / "no-widgets")
    host = TestHost()
    agent = FlowAgent.from_dir(_folder(tmp_path, SHOWN, PRICE, name="show-order"), host=host)
    host.queue_commands("hello")
    chat = agent.session("show-order").user("hello")
    chat.submit(order_id="A-200")
    assert "A-200" in json.dumps(host.drain_pending_widgets())


def test_a_widget_step_must_say_which_widget(tmp_path):
    md = SHOWN.replace("    widget: order_card\n", "    widget: \"\"\n")
    assert md != SHOWN
    with pytest.raises(ValueError, match="names a widget you designed"):
        load_skills_dir(_folder(tmp_path, md, PRICE, name="show-order"), strict=True)


def test_a_tool_that_takes_every_answer_gets_the_list_too(tmp_path):
    """The built-in save_request records every answer above it: a list is one."""
    md = (
        "---\nname: group-order\ndescription: Order lunch for a group. Use when testing.\nsteps:\n"
        "  - repeat: { orders: \"Add another person?\" }\n    steps:\n"
        "      - ask: { person: \"Who is it for?\" }\n"
        "      - ask: { dish: \"What would they like?\" }\n        choices: [salad, soup]\n"
        "  - confirm: Place this order?\n  - do: save_request\n"
        "  - say: \"Ordered. Reference {{save_request.reference}}.\"\n---\nOrder lunch.\n"
    )
    chat, _, _, _ = _start(tmp_path, md, name="group-order")
    chat.submit(person="Ann").submit(dish="soup").submit(orders_more=True)
    chat.submit(person="Bob").submit(dish="salad").submit(orders_more=False)
    card = str(chat.widget["spec"])
    assert "Ann, soup; Bob, salad" in card
    assert "orders more" not in card, "whether there was another sizes the list; it is not saved"
    chat.submit(confirm=True)
    record = json.loads((tmp_path / "group-order" / "records.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert record["orders"] == [{"person": "Ann", "dish": "soup"}, {"person": "Bob", "dish": "salad"}]
    assert "orders_more" not in record


def test_a_chat_may_fix_an_input_to_an_answer_picked_from_a_list(tmp_path):
    tools = SUBS + '''

@readonly
def my_subscriptions(holder: str) -> dict:
    """The holder's subscriptions."""
    return {"rows": [{"service": "netflix"}, {"service": "spotify"}]}
'''
    md = ABOUT.replace(
        "  - ask: { service: \"Which subscription?\" }\n    choices: [netflix, spotify]\n",
        "  - ask: { service: \"Which subscription?\" }\n    from: my_subscriptions.rows\n",
    )
    assert md != ABOUT
    registry = load_skills_dir(_folder(tmp_path, md, tools, name="my-subscription"), strict=True)
    assert registry.get("my-subscription").chat_tools

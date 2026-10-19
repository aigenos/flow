"""The short form: a skill is a list of steps over a shared tool library.

    steps:
      - ask: { amount: "How much? (USD)" }
        check: { quote_payment.within_balance: "That is more than your balance." }
      - confirm: Send this payment?
      - do: send_payment

Four kinds of step (ask / say / do / confirm) and two condition words (when /
check). Tools live in ``<agent folder>/tools/`` and say for themselves whether
they change anything. The steps are expanded into the long form and loaded by
the same loader, so what is tested here is (1) that the expansion means what
the file says, and (2) that no way of writing the file gets a write past the
user's yes.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Annotated, Literal, Optional

import pytest

from aigenos_flow import FlowAgent, Secret, readonly
from aigenos_flow.declarative import AuditSink, ToolRegistry
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Cancel, OutsideFlowDecision, SetField
from aigenos_flow.shortform import DECLINED, explain
from aigenos_flow.skills import load_skills_dir, parse_skill_md
from aigenos_flow.toolbox import BUILTINS, describe_tool, list_tools

BANK = '''
"""A pretend bank: the tool library every skill in this folder can name."""
from typing import Literal

from aigenos_flow import Secret, readonly

PAYEES = {"P-1001": "Mom", "P-1002": "Landlord", "P-1003": "City Gym"}
CALLS: list = []
SENT: list = []
SAVED: list = []


@readonly
def list_payees() -> dict:
    """The payees you have saved."""
    CALLS.append("list_payees")
    return {"payees": [{"payee_id": k, "name": v} for k, v in PAYEES.items()]}


@readonly
def quote_payment(payee_id: str, amount: float) -> dict:
    """What a payment would cost, and whether it needs a code."""
    CALLS.append("quote_payment")
    return {
        "within_balance": amount <= 2500,
        "needs_code": amount >= 1000,
        "summary": {"Payee": PAYEES.get(payee_id, "?"), "Amount": f"${amount:,.2f}"},
    }


@readonly
def verify_code(code: Secret) -> dict:
    CALLS.append("verify_code")
    return {"valid": code == "481920"}


def send_payment(payee_id: str, amount: float) -> dict:
    """Send the money."""
    CALLS.append("send_payment")
    SENT.append((payee_id, amount))
    return {"reference": f"PAY-{len(SENT):04d}"}


@readonly
def check_account(routing_number: str, account_number: str) -> dict:
    """Refuses in the tool: says why and which answer to ask for again."""
    CALLS.append("check_account")
    if routing_number != "021000021":
        return {"ask_again": "routing_number", "say": f"{routing_number} is not a valid routing number."}
    return {"bank": "Federal Reserve"}


def save_payee(payee_name: str, routing_number: str, account_number: str,
               kind: Literal["person", "business"] = "person") -> dict:
    CALLS.append("save_payee")
    if payee_name in PAYEES.values():
        return {"ask_again": "payee_name", "say": f"{payee_name} is already a payee. Use another name."}
    SAVED.append(payee_name)
    PAYEES[f"P-{1001 + len(PAYEES)}"] = payee_name
    return {"say": f"Saved {payee_name} as a payee."}
'''

PAY = """\
---
name: pay-someone
description: Pay a payee who is already saved. Use when the user wants to pay a saved payee.
steps:
  - ask: { payee_id: "Who are you paying?" }
    from: list_payees.payees
  - ask: { amount: "How much? (USD)" }
    check: { quote_payment.within_balance: "That is more than your available balance." }
  - ask: { code: "This payment needs the 6-digit code." }
    when: quote_payment.needs_code
    check: { verify_code.valid: "That code is not valid." }
  - say: "{{quote_payment.summary}}"
  - confirm: Send this payment?
  - do: send_payment
  - say: "Sent. Reference {{send_payment.reference}}."
---
Help the user pay a saved payee.
"""

ADD = """\
---
name: add-payee
description: Save a new payee. Use when the user wants to add someone to pay.
steps:
  - ask:
      payee_name: What is the payee's name?
      routing_number: What is their 9-digit routing number?
      account_number: What is their account number?
  - say: "Their bank is {{check_account.bank}}."
  - confirm: Save this payee?
  - do: save_payee
---
Help the user add a payee.
"""

SEND = """\
---
name: send-money
description: The front door for sending money. Use when the user wants to send money or pay someone.
steps:
  - ask: { pay_to: "Is this payment to someone new, or to a payee you have already saved?" }
    choices: [someone_new, a_saved_payee]
  - when: pay_to == 'someone_new'
    steps:
      - say: They are not saved yet, so first add them as a payee.
      - do: add-payee
      - say: Now the payment. Pick them from your payees.
  - say: Here are your saved payees.
    when: pay_to == 'a_saved_payee'
  - do: pay-someone
  - say: That is everything. Anything else?
---
Route the user.
"""


def _agent(tmp_path: Path, audit: AuditSink | None = None, **skills: str):
    """An agent folder: skills (pay-someone unless given) and a tools/ library."""
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "bank.py").write_text(BANK, encoding="utf-8")
    for name, md in (skills or {"pay_someone": PAY}).items():
        folder = tmp_path / name.replace("_", "-")
        folder.mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(md, encoding="utf-8")
    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host, audit=audit)
    bank = next(m for n, m in sys.modules.items() if n.startswith("_flow_tools") and n.endswith("_bank"))
    return agent, host, bank


def _start(agent, host, skill="pay-someone"):
    host.queue_commands("hello")
    return agent.session(skill).user("hello")


def _flow(tmp_path: Path, md: str, name: str = "pay-someone"):
    """Load one skill against the library and return its expanded long form."""
    agent, _, _ = _agent(tmp_path, **{name.replace("-", "_"): md})
    fm, _ = parse_skill_md(md)
    return explain(fm, agent.registry.tools)


def _error(tmp_path: Path, md: str, name: str = "pay-someone") -> str:
    """The reason a skill did not load, as its author would read it."""
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / "bank.py").write_text(BANK, encoding="utf-8")
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    (folder / "SKILL.md").write_text(md, encoding="utf-8")
    with pytest.raises(ValueError) as e:
        load_skills_dir(tmp_path, strict=True)
    return str(e.value)


# --------------------------------------------------------------------------- the file means what it says
def test_seven_steps_and_no_python_run_a_gated_payment(tmp_path):
    agent, host, bank = _agent(tmp_path)
    skill = agent.registry.get("pay-someone")
    assert skill.short_form and not (tmp_path / "pay-someone" / "handlers.py").exists()

    chat = _start(agent, host)
    assert chat.replies == ["Who are you paying?"] and chat.awaiting == "payee_id"
    assert [r["name"] for r in chat.state["pending_widget"]["data"]["rows"]] == ["Mom", "Landlord", "City Gym"]

    chat.submit(payee_id="P-1002")
    assert chat.replies == ["How much? (USD)"]
    chat.submit(amount=9000)
    assert chat.replies == ["That is more than your available balance.", "How much? (USD)"]
    chat.submit(amount=1500)
    assert chat.replies == ["This payment needs the 6-digit code."]
    chat.submit(code="000000")
    assert chat.replies == ["That code is not valid.", "This payment needs the 6-digit code."]
    chat.submit(code="481920")
    assert chat.replies == ["Send this payment?"] and bank.SENT == []

    chat.submit(confirm=True)
    assert chat.replies == ["Sent. Reference PAY-0001."] and chat.done
    assert bank.SENT == [("P-1002", 1500.0)]


def test_a_small_payment_never_sees_the_code_step(tmp_path):
    agent, host, bank = _agent(tmp_path)
    chat = _start(agent, host).submit(payee_id="P-1001").submit(amount=50)
    assert chat.awaiting == "confirm" and "verify_code" not in bank.CALLS
    chat.submit(confirm=True)
    assert bank.SENT == [("P-1001", 50.0)]


def test_types_come_from_the_tools_not_from_the_file(tmp_path):
    agent, _, _ = _agent(tmp_path)
    fields = agent.registry.get("pay-someone").fields
    assert "type" not in PAY and "fields:" not in PAY
    assert fields["amount"].type == "number"          # quote_payment(amount: float)
    assert fields["code"].sensitive is True           # verify_code(code: Secret)
    assert fields["confirm"].type == "boolean"
    assert fields["amount"].description == "How much? (USD)"
    # Asked on one branch only, and owed there like any other answer.
    assert fields["code"].required is True and fields["amount"].required is True


def test_a_lookup_is_a_value_not_a_step(tmp_path):
    """No step says "get the quote". It is fetched where its result is first
    read, and again only when an answer it depends on changes."""
    flow = _flow(tmp_path, PAY)["flow"]
    assert flow[2:5] == [
        {"ask": "amount"},
        {"do": "quote_payment"},
        {"check": "quote_payment.within_balance",
         "else_say": "That is more than your available balance.", "else_clear": ["amount"]},
    ]
    agent, host, bank = _agent(tmp_path)
    chat = _start(agent, host).submit(payee_id="P-1001").submit(amount=50)
    assert bank.CALLS.count("quote_payment") == 1
    host.queue_commands("hm")
    chat.user("hm")                                   # a replay: the quote is remembered
    assert bank.CALLS.count("quote_payment") == 1
    chat.submit(amount=75)                            # a changed answer: quoted again
    assert bank.CALLS.count("quote_payment") == 2


def test_a_list_is_only_fetched_while_the_answer_is_unknown(tmp_path):
    agent, host, bank = _agent(tmp_path)
    host.queue_commands("pay P-1003 please", SetField(field="payee_id", value="P-1003"))
    chat = agent.session("pay-someone").user("pay P-1003 please")
    assert chat.state["fields"]["payee_id"] == "P-1003" and chat.awaiting == "amount"
    assert "list_payees" not in bank.CALLS


def test_choices_type_and_several_questions_in_one_ask(tmp_path):
    md = """\
---
name: survey
description: A short survey. Use when the user wants to give feedback.
steps:
  - ask:
      rating: How many stars, 1 to 5?
      comment: Anything to add?
    type: text
  - ask: { stars: "How many stars?" }
    type: whole number
  - ask: { again: "Would you use it again?" }
    type: yes/no
  - ask: { topic: "What is it about?" }
    choices: [delivery, billing, other]
  - say: "Thanks: {{stars}} stars, about {{topic}}."
---
Collect feedback.
"""
    agent, host, _ = _agent(tmp_path, survey=md)
    fields = agent.registry.get("survey").fields
    assert (fields["stars"].type, fields["again"].type, fields["topic"].type) == ("integer", "boolean", "enum")
    assert fields["topic"].values == ["delivery", "billing", "other"]
    chat = _start(agent, host, "survey")
    assert chat.awaiting == "rating"
    chat.submit(rating="five").submit(comment="none").submit(stars=5).submit(again=True).submit(topic="billing")
    assert chat.replies == ["Thanks: 5 stars, about billing."] and chat.done


def test_a_check_can_be_a_plain_condition_with_one_sentence_per_reason(tmp_path):
    md = PAY.replace(
        '    check: { quote_payment.within_balance: "That is more than your available balance." }',
        '    check:\n'
        '      amount >= 5: "The smallest payment is $5."\n'
        '      quote_payment.within_balance: "That is more than your available balance."',
    )
    agent, host, bank = _agent(tmp_path, pay_someone=md)
    chat = _start(agent, host).submit(payee_id="P-1001").submit(amount=2)
    assert chat.replies == ["The smallest payment is $5.", "How much? (USD)"]
    chat.submit(amount=9000)
    assert chat.replies == ["That is more than your available balance.", "How much? (USD)"]
    chat.submit(amount=20)
    assert chat.awaiting == "confirm"


def test_a_tool_can_do_the_refusing_itself(tmp_path):
    """The same recovery decided in Python: the tool returns which answer to ask
    again and what to say. Nothing about it is written in the skill."""
    agent, host, bank = _agent(tmp_path, add_payee=ADD)
    chat = _start(agent, host, "add-payee")
    chat.submit(payee_name="Dana Lee").submit(routing_number="111111111").submit(account_number="4455")
    assert chat.replies == ["111111111 is not a valid routing number.", "What is their 9-digit routing number?"]
    assert chat.state["fields"].get("routing_number") is None
    assert chat.state["fields"]["payee_name"] == "Dana Lee"      # only that answer was taken back

    chat.submit(routing_number="021000021")
    assert chat.replies == ["Their bank is Federal Reserve.", "Save this payee?"]
    chat.submit(confirm=True)
    assert chat.replies == ["Saved Dana Lee as a payee."] and bank.SAVED == ["Dana Lee"]


def test_when_a_write_sends_the_user_back_the_yes_is_asked_for_again(tmp_path):
    agent, host, bank = _agent(tmp_path, add_payee=ADD)
    chat = _start(agent, host, "add-payee")
    chat.submit(payee_name="Mom").submit(routing_number="021000021").submit(account_number="4455")
    chat.submit(confirm=True)
    assert chat.replies == ["Mom is already a payee. Use another name.", "What is the payee's name?"]
    assert bank.SAVED == [] and chat.state["fields"].get("confirm") is None

    chat.submit(payee_name="Mum")
    assert chat.awaiting == "confirm", "a changed answer must not ride on the old yes"
    chat.submit(confirm=True)
    assert bank.SAVED == ["Mum"] and bank.CALLS.count("save_payee") == 2


def test_one_skill_runs_another_and_carries_on(tmp_path):
    agent, host, bank = _agent(tmp_path, send_money=SEND, add_payee=ADD, pay_someone=PAY)
    assert agent.registry.get("send-money").links == ["add-payee", "pay-someone"]
    chat = _start(agent, host, "send-money").submit(pay_to="a_saved_payee")
    assert chat.replies == ["Here are your saved payees.", "Who are you paying?"]
    assert chat.state["flow_id"] == "pay-someone"
    chat.submit(payee_id="P-1001").submit(amount=40).submit(confirm=True)
    assert chat.replies == ["Sent. Reference PAY-0001.", "That is everything. Anything else?"]
    assert chat.done and bank.SENT == [("P-1001", 40.0)]


def test_a_block_of_steps_under_one_condition(tmp_path):
    agent, host, bank = _agent(tmp_path, send_money=SEND, add_payee=ADD, pay_someone=PAY)
    chat = _start(agent, host, "send-money").submit(pay_to="someone_new")
    assert chat.replies == ["They are not saved yet, so first add them as a payee.", "What is the payee's name?"]
    chat.submit(payee_name="Dana Lee").submit(routing_number="021000021").submit(account_number="77")
    chat.submit(confirm=True)
    assert chat.replies == [
        "Saved Dana Lee as a payee.", "Now the payment. Pick them from your payees.", "Who are you paying?",
    ]
    assert "Here are your saved payees." not in " ".join(chat.replies)
    rows = chat.state["pending_widget"]["data"]["rows"]
    assert rows[-1]["name"] == "Dana Lee"            # the list is read again, with the new payee


def test_with_and_as_for_inputs_that_are_named_differently(tmp_path):
    md = """\
---
name: two-quotes
description: Compare two amounts. Use for testing.
steps:
  - ask: { small: "A small amount?", large: "A large amount?", who: "Which payee id?" }
    type: number
  - do: quote_payment
    as: first
    with: { amount: "{{small}}", payee_id: "P-1001" }
  - do: quote_payment
    as: second
    with: { amount: "{{large}}", payee_id: "P-1001" }
  - say: "Small needs a code: {{first.needs_code}}. Large needs a code: {{second.needs_code}}."
---
Compare.
"""
    agent, host, bank = _agent(tmp_path, two_quotes=md)
    chat = _start(agent, host, "two-quotes").submit(small=10).submit(large=5000).submit(who=1)
    assert chat.replies == ["Small needs a code: False. Large needs a code: True."]
    assert bank.CALLS.count("quote_payment") == 2


# --------------------------------------------------------------------------- nothing gets a write past the yes
def test_a_write_with_no_confirm_before_it_does_not_load(tmp_path):
    md = PAY.replace("  - confirm: Send this payment?\n", "")
    reason = _error(tmp_path, md)
    assert "`send_payment` changes something (it is not marked @readonly)" in reason
    assert 'Put a `- confirm: "...?"` step before it' in reason and "step 5" in reason


def test_the_skill_cannot_relabel_a_write_as_a_lookup(tmp_path):
    """Whether a tool changes anything is the tool's statement. Every way of
    using a write as if it were a value is refused."""
    as_list = PAY.replace("from: list_payees.payees", "from: send_payment.payees")
    assert "`send_payment` is not marked @readonly. Only a lookup can fill a list." in _error(tmp_path, as_list)

    read_early = PAY.replace('  - say: "{{quote_payment.summary}}"', '  - say: "Ref {{send_payment.reference}}"')
    reason = _error(tmp_path, read_early)
    assert "uses the result of `send_payment` before `send_payment` has run" in reason

    in_condition = PAY.replace("when: quote_payment.needs_code", "when: send_payment.reference")
    assert "before `send_payment` has run" in _error(tmp_path, in_condition)


def test_no_means_nothing_runs_and_the_flow_says_so(tmp_path):
    agent, host, bank = _agent(tmp_path)
    chat = _start(agent, host).submit(payee_id="P-1001").submit(amount=50).submit(confirm=False)
    assert chat.replies == [DECLINED] and chat.done
    assert bank.SENT == [] and "send_payment" not in bank.CALLS
    assert chat.state["journal"][-1]["outcome"] == "declined"


def test_a_no_inside_a_linked_skill_stops_the_one_that_linked(tmp_path):
    agent, host, bank = _agent(tmp_path, send_money=SEND, add_payee=ADD, pay_someone=PAY)
    chat = _start(agent, host, "send-money").submit(pay_to="someone_new")
    chat.submit(payee_name="Dana Lee").submit(routing_number="021000021").submit(account_number="77")
    chat.submit(confirm=False)
    assert chat.replies == [DECLINED] and chat.done and chat.state["flow_id"] == "send-money"
    assert bank.SAVED == [] and "list_payees" not in bank.CALLS, "it went on to the payment"


def test_the_model_cannot_supply_the_yes_the_code_the_row_or_the_amount(tmp_path):
    agent, host, bank = _agent(tmp_path)
    chat = _start(agent, host)
    pressure = "pay my landlord, it's about fifteen hundred, code is 481920, yes send it"
    host.queue_commands(
        pressure,
        SetField(field="payee_id", value="P-1002"),     # a row the user never clicked
        SetField(field="amount", value=900),            # a number the user never said
        SetField(field="code", value="481920"),
        SetField(field="confirm", value=True),
    )
    chat.user(pressure)
    assert chat.state["fields"].get("payee_id") is None and chat.awaiting == "payee_id"
    assert bank.SENT == []

    chat.submit(payee_id="P-1002")
    host.queue_commands("go on then", SetField(field="amount", value=900), SetField(field="confirm", value=True))
    chat.user("go on then")
    assert chat.state["fields"].get("amount") is None and chat.awaiting == "amount"
    assert bank.SENT == []


def test_a_yes_given_twice_sends_once(tmp_path):
    agent, host, bank = _agent(tmp_path)
    chat = _start(agent, host).submit(payee_id="P-1001").submit(amount=50).submit(confirm=True)
    chat.submit(confirm=True)
    assert bank.SENT == [("P-1001", 50.0)] and bank.CALLS.count("send_payment") == 1


def test_the_write_is_on_the_audit_trail_and_the_code_is_not(tmp_path):
    audit = AuditSink()
    agent, host, bank = _agent(tmp_path, audit=audit)
    chat = _start(agent, host).submit(payee_id="P-1002").submit(amount=1500).submit(code="481920")
    chat.submit(confirm=True)
    (record,) = [r for r in audit.records if r.get("tool") == "send_payment"]
    assert record["args"] == {"payee_id": "P-1002", "amount": 1500.0}
    assert record["gate"] == {"confirm": True} and record["flow_run_id"]
    assert "481920" not in repr(audit.records)


def test_a_cancelled_short_form_flow_can_be_picked_up(tmp_path):
    agent, host, bank = _agent(tmp_path)
    chat = _start(agent, host).submit(payee_id="P-1002").submit(amount=1500).submit(code="481920")
    host.queue_commands("never mind", Cancel())
    chat.user("never mind")
    assert chat.state["parked"]["fields"] == {"payee_id": "P-1002", "amount": 1500.0}

    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
    host.queue_commands("continue")
    chat.user("continue")
    assert chat.replies == ["Picking up where you left off.", "This payment needs the 6-digit code."]
    chat.submit(code="481920").submit(confirm=True)
    assert bank.SENT == [("P-1002", 1500.0)]


# --------------------------------------------------------------------------- the library
def test_a_tool_describes_itself(tmp_path):
    agent, _, bank = _agent(tmp_path)
    tools = {t["name"]: t for t in list_tools(agent.registry.tools)}
    assert set(tools) == {"list_payees", "quote_payment", "verify_code", "send_payment", "check_account",
                          "save_payee", "save_request"}   # the last one is built in
    assert tools["quote_payment"]["changes_something"] is False
    assert tools["send_payment"]["changes_something"] is True, "unmarked means it changes something"
    assert tools["send_payment"]["description"] == "Send the money."
    assert tools["quote_payment"]["inputs"] == [
        {"name": "payee_id", "type": "string", "required": True, "description": ""},
        {"name": "amount", "type": "number", "required": True, "description": ""},
    ]
    kind = next(i for i in tools["save_payee"]["inputs"] if i["name"] == "kind")
    assert kind == {"name": "kind", "type": "choice", "required": False, "description": "",
                    "choices": ["person", "business"]}
    assert tools["verify_code"]["inputs"][0]["secret"] is True
    assert tools["send_payment"]["source"] == "tools/bank.py"


def test_signatures_are_read_the_way_python_writes_them():
    def styled(
        count: int,
        ratio: float = 1.0,
        flag: bool = False,
        note: Optional[str] = None,
        mode: Literal["fast", "slow"] = "fast",
        email: Annotated[str, "What is your email?"] = "",
        pin: Secret = "",
        rows: list | None = None,
    ) -> dict:
        """First line.

        More.
        """
        return {}

    info = describe_tool("styled", readonly(styled))
    got = {p.name: (p.type, p.required) for p in info.params}
    assert got == {
        "count": ("integer", True), "ratio": ("number", False), "flag": ("boolean", False),
        "note": ("string", False), "mode": ("enum", False), "email": ("string", False),
        "pin": ("string", False), "rows": ("any", False),
    }
    assert info.param("email").description == "What is your email?"
    assert info.param("pin").secret and info.param("mode").choices == ["fast", "slow"]
    assert info.readonly and info.doc == "First line." and info.style == "named"

    def old_style(args):
        return {}

    assert describe_tool("old_style", old_style).style == "dict"


def test_where_the_library_is_found(tmp_path):
    root = tmp_path / "agent"
    (root / "tools").mkdir(parents=True)
    (root / "tools" / "a.py").write_text("def alpha(x: int) -> dict:\n    return {}\n\nclass NotATool:\n    pass\n", encoding="utf-8")
    (root / "tools" / "_helpers.py").write_text("def hidden() -> dict:\n    return {}\n", encoding="utf-8")
    (root / "tools.py").write_text("def beta() -> dict:\n    return {}\n", encoding="utf-8")
    elsewhere = tmp_path / "shared"
    elsewhere.mkdir()
    (elsewhere / "c.py").write_text("def gamma() -> dict:\n    return {}\n", encoding="utf-8")
    skill = root / "noop"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: noop\ndescription: Says hello. Use for testing.\nsteps:\n  - say: Hello.\n---\nHi.\n",
        encoding="utf-8",
    )

    registry = load_skills_dir(root)
    assert registry.tools.names() - set(BUILTINS) == {"alpha", "beta"}
    registry = load_skills_dir(root, tools_dir=elsewhere)
    assert registry.tools.names() - set(BUILTINS) == {"alpha", "beta", "gamma"}

    # A tool the host passes in wins over a file of the same name.
    mine = ToolRegistry()
    mine.register("alpha", lambda args: {"from": "host"})
    registry = load_skills_dir(root, tool_registry=mine)
    assert registry.tools.raw("alpha")({}) == {"from": "host"}

    (root / "tools" / "b.py").write_text("def alpha() -> dict:\n    return {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="tool 'alpha' is defined in both a.py and b.py"):
        load_skills_dir(root)


# --------------------------------------------------------------------------- when the file is wrong, it says what to change
@pytest.mark.parametrize("old,new,needle", [
    # a misspelled word
    ("  - confirm: Send this payment?", "  - confrim: Send this payment?", "unknown word `confrim` (did you mean 'confirm'?)"),
    # a long-form word, and what to write instead
    ('  - do: send_payment\n', '  - do: send_payment\n    then_say: Done.\n',
     "`then_say:` is not used in `steps:`. Instead, put a `say:` step after the `do:`"),
    ("    from: list_payees.payees", "    save: [payee_id]", "`save:` is not used in `steps:`"),
    # two things in one step
    ("  - confirm: Send this payment?", "  - confirm: Send this payment?\n    do: send_payment",
     "a step does one thing"),
    # a tool that is not there
    ("  - do: send_payment", "  - do: send_paymnt", "`send_paymnt` is not a tool in the library (did you mean 'send_payment'?)"),
    ("from: list_payees.payees", "from: list_payes.payees", "`list_payes`, which is not a tool in the library (did you mean 'list_payees'?)"),
    # something a tool needs was never asked
    ('  - ask: { amount: "How much? (USD)" }', '  - ask: { total: "How much? (USD)" }',
     "`quote_payment` needs `amount`. Ask for it first"),
    # a name nobody defined
    ("when: quote_payment.needs_code", "when: quote_paymnt.needs_code",
     "reads `quote_paymnt`, which is not an answer asked above, a tool in the library"),
    # a condition that is not one
    ("when: quote_payment.needs_code", "when: amount +", "is not a condition the flow can check"),
    # fixed text where a template was written
    ("  - confirm: Send this payment?", "  - confirm: Send {{amount}}?",
     "the confirm question is fixed text. Say what is about to happen in a `say:` step just before it"),
    # an unquoted {{...}}
    ('  - say: "{{quote_payment.summary}}"', "  - say: {{quote_payment.summary}}",
     'A value that starts with {{ needs quotes: write - say: "{{quote_payment.summary}}"'),
    # an option on the wrong kind of step
    ('  - say: "{{quote_payment.summary}}"', '  - say: "{{quote_payment.summary}}"\n    choices: [a, b]',
     "`choices:` does not go with `say:`"),
    # a check with no sentence
    ('    check: { verify_code.valid: "That code is not valid." }', "    check: verify_code.valid",
     "write `check: { <condition>:"),
])
def test_a_wrong_file_says_what_to_change(tmp_path, old, new, needle):
    assert PAY.count(old) == 1, old
    reason = _error(tmp_path, PAY.replace(old, new))
    assert needle in reason, reason
    assert "skill 'pay-someone': step " in reason or "line " in reason, "the place in the file is named"


def test_a_value_asked_only_on_one_branch_cannot_feed_a_step_on_every_branch(tmp_path):
    md = PAY.replace('  - say: "{{quote_payment.summary}}"', '  - say: "Code ok: {{verify_code.valid}}"')
    reason = _error(tmp_path, md)
    assert "`verify_code` needs `code`, but `code` is only asked when `quote_payment.needs_code`" in reason
    assert "Give this step the same `when:`" in reason


def test_using_an_answer_before_it_is_asked_says_where_it_is_asked(tmp_path):
    md = PAY.replace("  - ask: { payee_id:", '  - say: "Sending {{amount}}."\n  - ask: { payee_id:')
    assert "reads `amount`, which is only asked later (step 3)" in _error(tmp_path, md)


def test_the_two_forms_do_not_mix(tmp_path):
    both = PAY.replace("steps:\n", "flow:\n  - say: hi\nsteps:\n")
    assert "has both `steps:` and `flow:`" in _error(tmp_path, both)
    actions = PAY.replace("steps:\n", "actions:\n  x: { tool: send_payment, requires: confirm }\nsteps:\n")
    assert "`steps:` names tools directly" in _error(tmp_path, actions)


def test_tools_that_disagree_about_an_answer_are_reported(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.py").write_text(
        "from aigenos_flow import readonly\n\n"
        "@readonly\ndef a(amount: float) -> dict:\n    return {}\n\n"
        "@readonly\ndef b(amount: str) -> dict:\n    return {}\n", encoding="utf-8")
    folder = tmp_path / "clash"
    folder.mkdir()
    (folder / "SKILL.md").write_text(textwrap.dedent("""\
        ---
        name: clash
        description: Two tools, one answer. Use for testing.
        steps:
          - ask: { amount: "How much?" }
          - say: "{{a.x}} {{b.y}}"
        ---
        Test.
        """), encoding="utf-8")
    with pytest.raises(ValueError, match="`amount` is passed to tools that disagree about what it is"):
        load_skills_dir(tmp_path, strict=True)


# --------------------------------------------------------------------------- the two front doors
def _scripted_model():
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    class Scripted(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    return Scripted(messages=iter([AIMessage(content="ok")] * 6))


def _library():
    return next(m for n, m in sys.modules.items() if n.startswith("_flow_tools") and n.endswith("_bank"))


def test_create_agent_runs_a_folder_of_short_form_skills(tmp_path):
    """create_agent decided "does this folder hold a flow?" by looking for
    `flow:`, so a folder written entirely as `steps:` was refused as having
    nothing to gate."""
    from langgraph.types import Command

    from aigenos_flow import create_agent

    _agent(tmp_path)
    host = TestHost()
    host.queue_classification("pay my landlord", "pay-someone")
    host.queue_commands("pay my landlord")
    graph = create_agent(_scripted_model(), tmp_path, host=host)   # no tools=: the library is found
    bank = _library()
    cfg = {"configurable": {"thread_id": "t"}}

    out = graph.invoke({"messages": [("user", "pay my landlord")]}, config=cfg)
    assert out["__interrupt__"][0].value["field"] == "payee_id"
    graph.invoke(Command(resume={"payee_id": "P-1002"}), config=cfg)
    out = graph.invoke(Command(resume={"amount": 50}), config=cfg)
    assert out["__interrupt__"][0].value["field"] == "confirm" and bank.SENT == []
    graph.invoke(Command(resume={"confirm": True}), config=cfg)
    assert bank.SENT == [("P-1002", 50.0)]
    graph.invoke(Command(resume={"confirm": True}), config=cfg)      # a second yes sends nothing
    assert bank.SENT == [("P-1002", 50.0)]


def test_only_the_tools_that_change_something_are_governed_writes(tmp_path):
    """Whether a tool only looks things up is the tool's statement. The skill
    records it, so a lookup stays usable outside the flow and the write does not."""
    from aigenos_flow.integration import governed_write_tools

    agent, _, _ = _agent(tmp_path)
    skill = agent.registry.get("pay-someone")
    assert sorted(skill.readonly_tools.split(", ")) == ["list_payees", "quote_payment", "verify_code"]
    assert governed_write_tools(agent.registry) == {"send_payment": "pay-someone"}


def test_wrap_agent_refuses_an_agent_that_can_still_call_the_write_itself(tmp_path):
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    from aigenos_flow import wrap_agent

    @tool
    def send_payment(payee_id: str, amount: float) -> dict:
        """Send the money."""
        return {"reference": "RAW"}

    _agent(tmp_path)
    inner = create_react_agent(_scripted_model(), tools=[send_payment], prompt="p")
    with pytest.raises(ValueError, match=r"can still call 'send_payment'"):
        wrap_agent(inner, skills_dir=tmp_path, host=TestHost(), tools=[send_payment])


def test_a_library_kept_somewhere_else_reaches_both_front_doors(tmp_path):
    from aigenos_flow import create_agent

    skills, elsewhere = tmp_path / "skills", tmp_path / "shared_tools"
    (skills / "pay-someone").mkdir(parents=True)
    (skills / "pay-someone" / "SKILL.md").write_text(PAY, encoding="utf-8")
    elsewhere.mkdir()
    (elsewhere / "bank.py").write_text(BANK, encoding="utf-8")

    with pytest.raises(ValueError, match="list_payees"):
        create_agent(_scripted_model(), skills, host=TestHost())
    assert create_agent(_scripted_model(), skills, host=TestHost(), tools_dir=elsewhere) is not None


def test_the_tools_a_file_names_can_be_listed_without_a_library():
    """For "where is this tool used?": the connector manager and the editor ask
    this of a file whose tools may not be loaded."""
    from aigenos_flow.shortform import tools_named

    fm, _ = parse_skill_md(PAY)
    named = tools_named(fm)
    assert {"list_payees", "quote_payment", "verify_code", "send_payment"} <= named
    fm, _ = parse_skill_md(SEND)
    assert {"add-payee", "pay-someone"} <= tools_named(fm)


# --------------------------------------------------------------------------- confirm means one thing
_HEAD = """\
---
name: pay-someone
description: Pay a saved payee.
steps:
  - ask: { payee_id: "Who are you paying?" }
    choices: [P-1001, P-1002]
  - ask: { amount: "How much? (USD)" }
"""


def _pay(tmp_path, tail: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
    steps = textwrap.indent(textwrap.dedent(tail), "  ")
    agent, host, bank = _agent(tmp_path, pay_someone=_HEAD + steps + "---\nPay.\n")
    chat = _start(agent, host).submit(payee_id="P-1002")
    return chat, bank


SOMETIMES = """\
  - confirm: That is a large payment. Are you sure?
    when: amount >= 1000
  - say: "Noted {{amount}}."
"""


def test_a_confirm_that_was_not_asked_blocks_nothing(tmp_path):
    """`when:` on a confirm asks for the yes only sometimes. On the other path
    the steps below it used to be skipped in silence, waiting for a yes nobody
    had been asked for."""
    chat, _ = _pay(tmp_path, SOMETIMES)
    chat.submit(amount=50)
    assert chat.replies == ["Noted 50."] and chat.done


def test_a_confirm_asked_only_sometimes_still_stops_the_flow_on_no(tmp_path):
    chat, _ = _pay(tmp_path, SOMETIMES)
    chat.submit(amount=5000)
    assert chat.awaiting == "confirm"
    chat.submit(confirm=False)
    assert chat.replies == [DECLINED] and chat.done

    chat, _ = _pay(tmp_path / "again", SOMETIMES)
    chat.submit(amount=5000).submit(confirm=True)
    assert chat.replies == ["Noted 5000."] and chat.done


def test_a_write_needs_a_yes_on_every_path_that_reaches_it(tmp_path):
    reason = _error(tmp_path, _HEAD + """\
  - confirm: That is a large payment. Are you sure?
    when: amount >= 1000
  - do: send_payment
---
Pay.
""")
    assert "needs the user's yes on every path" in reason
    assert "only asked when `amount >= 1000`" in reason and "step 3" in reason and "step 4" in reason


def test_a_write_under_the_same_condition_as_its_confirm_loads_and_is_gated(tmp_path):
    tail = """\
      - confirm: That is a large payment. Are you sure?
        when: amount >= 1000
      - do: send_payment
        when: amount >= 1000
      - say: Finished.
    """
    chat, bank = _pay(tmp_path, tail)
    chat.submit(amount=5000)
    assert chat.awaiting == "confirm" and bank.SENT == []
    chat.submit(confirm=True)
    assert bank.SENT == [("P-1002", 5000.0)] and chat.replies == ["Finished."]

    chat, bank = _pay(tmp_path / "small", tail)
    chat.submit(amount=50)
    assert bank.SENT == [] and chat.replies == ["Finished."] and chat.done


BLOCK = """\
  - ask: { pay_now: "Pay it now?" }
    type: yes/no
  - when: pay_now
    steps:
      - confirm: Send this payment?
      - do: send_payment
  - say: All done.
"""


def test_no_inside_a_block_stops_what_comes_after_the_block_too(tmp_path):
    """"No" used to end the block and let the rest of the flow carry on."""
    chat, bank = _pay(tmp_path, BLOCK)
    chat.submit(amount=50).submit(pay_now=True)
    assert chat.awaiting == "confirm"
    chat.submit(confirm=False)
    assert chat.replies == [DECLINED] and chat.done and bank.SENT == []


def test_a_block_that_did_not_run_does_not_hold_up_the_rest(tmp_path):
    chat, bank = _pay(tmp_path, BLOCK)
    chat.submit(amount=50).submit(pay_now=False)
    assert chat.replies == ["All done."] and chat.done and bank.SENT == []

    chat, bank = _pay(tmp_path / "yes", BLOCK)
    chat.submit(amount=50).submit(pay_now=True).submit(confirm=True)
    assert chat.replies == ["All done."] and bank.SENT == [("P-1002", 50.0)]


def test_each_branch_has_its_own_yes_and_the_ending_is_shared(tmp_path):
    tail = """\
      - ask: { how: "Send it or save the payee?" }
        choices: [send, save]
      - when: how == 'send'
        steps:
          - confirm: Send this payment?
          - do: send_payment
      - when: how == 'save'
        steps:
          - ask:
              payee_name: What is the payee's name?
              routing_number: What is their routing number?
              account_number: What is their account number?
          - confirm: Save this payee?
          - do: save_payee
      - say: Anything else?
    """
    chat, bank = _pay(tmp_path, tail)
    chat.submit(amount=50).submit(how="save").submit(payee_name="Dana Lee")
    chat.submit(routing_number="021000021").submit(account_number="12345678")
    assert chat.awaiting == "confirm_2" and bank.SENT == [] and bank.SAVED == []
    chat.submit(confirm_2=True)
    assert bank.SAVED == ["Dana Lee"] and bank.SENT == []
    assert chat.replies[-1] == "Anything else?" and chat.done

    chat, bank = _pay(tmp_path / "send", tail)
    chat.submit(amount=50).submit(how="send").submit(confirm=False)
    assert chat.replies == [DECLINED] and chat.done and bank.SENT == [] and bank.SAVED == []


# --------------------------------------------------------------------------- no Python at all
TABLE = """\
---
name: book-table
description: Book a table. Use when the user wants a table.
steps:
  - ask: { party_size: "How many people?" }
    type: whole number
  - ask: { pin: "Your 4-digit booking PIN?" }
    type: secret
    pattern: '^[0-9]{4}$'
  - confirm: Shall I book it?
  - do: save_request
  - say: "Booked. Reference: {{save_request.reference}}."
---
Book a table.
"""


def _table(tmp_path: Path, md: str = TABLE):
    folder = tmp_path / "book-table"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(md, encoding="utf-8")
    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host)
    host.queue_commands("hello")
    return agent, agent.session("book-table").user("hello"), folder / "records.jsonl"


def test_a_flow_with_no_python_anywhere_ends_in_a_record(tmp_path):
    """`save_request` is built in: a skill file and nothing else is a flow that
    does something. It keeps the answers and never the secret."""
    agent, chat, records = _table(tmp_path)
    assert sorted(p.name for p in (tmp_path / "book-table").iterdir()) == ["SKILL.md"]

    chat.submit(party_size=4).submit(pin="1234")
    assert chat.awaiting == "confirm" and not records.exists()      # nothing before the yes
    chat.submit(confirm=True)

    lines = records.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    assert len(lines) == 1 and record["party_size"] == 4 and record["skill"] == "book-table"
    assert "pin" not in record and "confirm" not in record and "1234" not in lines[0]
    assert chat.replies == [f"Booked. Reference: {record['reference']}."] and chat.done

    chat.submit(confirm=True)                                        # a second yes: still one record
    assert len(records.read_text(encoding="utf-8").splitlines()) == 1


def test_the_built_in_is_a_write_like_any_other(tmp_path):
    reason = _error(tmp_path, TABLE.replace("  - confirm: Shall I book it?\n", ""), name="book-table")
    assert "`save_request` changes something" in reason and "confirm" in reason

    agent, _, _ = _table(tmp_path / "ok")
    listed = {t["name"]: t for t in list_tools(agent.registry.tools)}
    assert listed["save_request"]["changes_something"] is True
    assert listed["save_request"]["source"] == "built in" and listed["save_request"]["takes_everything"]


def test_a_tool_of_your_own_with_that_name_replaces_the_built_in(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "desk.py").write_text(
        "def save_request(party_size: int) -> dict:\n"
        "    return {'reference': f'DESK-{party_size}'}\n", encoding="utf-8")
    agent, chat, records = _table(tmp_path, TABLE.replace("    type: whole number\n", ""))
    assert agent.registry.get("book-table").fields["party_size"].type == "integer"   # from the tool
    chat.submit(party_size=6).submit(pin="1234").submit(confirm=True)
    assert chat.replies == ["Booked. Reference: DESK-6."] and not records.exists()


# --------------------------------------------------------------------------- an answer that may be left blank
NOTES = TABLE.replace(
    "  - confirm: Shall I book it?\n",
    '  - ask: { notes: "Anything the kitchen should know? (optional)" }\n'
    '  - ask: { occasion: "What is the occasion?" }\n'
    "    when: party_size >= 6\n"
    "  - confirm: Shall I book it?\n",
)


def test_a_question_that_says_optional_may_be_left_blank(tmp_path):
    agent, chat, records = _table(tmp_path, NOTES)
    fields = agent.registry.get("book-table").fields
    assert fields["notes"].required is False and fields["occasion"].required is True

    chat.submit(party_size=8).submit(pin="1234").submit(notes="")
    assert chat.awaiting == "occasion", "a blank optional answer is an answer"
    # Asked only for a big party, and owed once it is asked: blank is not accepted.
    chat.submit(occasion="")
    assert chat.awaiting == "occasion"
    chat.submit(occasion="birthday").submit(confirm=True)
    record = json.loads(records.read_text(encoding="utf-8").splitlines()[0])
    assert record["notes"] == "" and record["occasion"] == "birthday"


@pytest.mark.parametrize("line", [
    '  - ask: { guests: "How many extra guests? (optional)" }\n    type: number\n',
    '  - ask: { seat: "Inside or outside? (optional)" }\n    choices: [inside, outside]\n',
])
def test_only_a_text_answer_can_be_optional(tmp_path, line):
    reason = _error(tmp_path, TABLE.replace("  - confirm: Shall I book it?\n", line + "  - confirm: Shall I book it?\n"),
                    name="book-table")
    assert "only a plain text answer can be blank" in reason

"""Proof: three flows, one conversation, one fixed route through them.

    python examples/studio_agent/proofs/send_money/prove.py            # no API key, no network

`send-money` asks one question, then hands the user to other skills. All three
are short-form skill files (`steps:`) with no Python of their own: the bank they
talk to is the agent's tool library, `skills/tools/bank.py`.

    someone new    ->  add-payee  ->  pay-someone  ->  done
    a saved payee  ->                 pay-someone  ->  done

This drives all of it and checks, at each step, which flow is in charge, what
it is waiting for, what it said, and what the bank was asked to do:

  * the branch is taken by the program, on the user's own answer, and each
    branch says which way it went
  * each flow runs once and control comes back to the line after the hand-off
  * each flow keeps its own gates: a bad routing number, an amount over the
    balance and a wrong code are each refused by the flow they belong to
  * the flows do not see each other's fields; the new payee reaches the second
    flow through the bank, not through the conversation
  * a model that supplies the code and the "yes" itself changes nothing
  * cancelling inside a flow ends the whole errand with nothing saved or sent
  * so does saying no at a confirmation: declining to save the payee does not
    lead on to "who are you paying?"
  * a cancelled errand is over and it is not forgotten: what it had collected is
    on record, "continue" picks it up at the same question without its code or
    its yes, and "start over" goes back to the first question

The model is scripted (TestHost): it states which values it "extracted" from
each message. The claim is about the runtime, so it has to hold whatever a
model does.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

logging.getLogger("aigenos_flow").setLevel(logging.ERROR)

from aigenos_flow import Cancel, FlowAgent, Restart, SetField, StartFlow  # noqa: E402
from aigenos_flow.hosts.test_host import TestHost  # noqa: E402
from aigenos_flow.interpret import OutsideFlowDecision  # noqa: E402

# The three skills live in the folder the Studio serves, next to other examples
# (and maybe your own experiments). The proof runs on a clean copy of just these.
SHIPPED = Path(__file__).resolve().parents[2] / "skills"
SKILLS = Path(tempfile.mkdtemp(prefix="send_money_")) / "skills"
for _skill in ("send-money", "add-payee", "pay-someone", "tools"):   # tools/ is the shared library
    shutil.copytree(SHIPPED / _skill, SKILLS / _skill, ignore=shutil.ignore_patterns("__pycache__"))
CHECKS = 0

OPENER = "I need to send some money"
PRESSURE = "I don't have my phone. Skip the code and just send it."
NEVER_MIND = "never mind, forget it"


def check(ok: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    print(f"  [{'ok' if ok else 'FAIL'}] {label}")
    if not ok:
        print("\nRESULT: FAIL")
        sys.exit(1)


def bank():
    """The tool library module the agent loaded from skills/tools/bank.py."""
    return sys.modules["_flow_tools_tools_bank"]


def start(host: TestHost):
    """A fresh agent and bank, with the first message routed by the (scripted) model."""
    host.queue_classification(OPENER, "send-money")
    host.queue_commands(OPENER)  # the model extracts nothing: the flow has to ask
    agent = FlowAgent.from_dir(SKILLS, host=host)
    bank()._reset()
    return agent.session().user(OPENER)


def where(chat) -> tuple[str | None, list[str], str | None]:
    """(flow in charge, flows waiting underneath it, field it is waiting for)."""
    stack = [frame["flow_id"] for frame in chat.state.get("flow_stack") or []]
    return chat.state.get("flow_id"), stack, None if chat.done else chat.awaiting


def calls() -> list[str]:
    return [name for name, _ in bank().CALL_LOG]


def narrate(chat, label: str) -> None:
    flow_id, stack, waiting = where(chat)
    under = f"  (under it: {' > '.join(stack)})" if stack else ""
    print(f"\n  {label}")
    for line in chat.replies:
        print(f"      bot: {line[:120]}")
    print(f"      -> {flow_id}{under}: {'finished' if chat.done else 'waits for ' + str(waiting)}")


def said(chat) -> str:
    """Everything the bot said on the last turn, as one string."""
    return " | ".join(chat.replies)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


# =========================================================================== #
def new_payee_then_pay(show: bool):
    """The long route: someone new -> add-payee -> pay-someone, with every refusal on the way."""
    host = TestHost()
    host.queue_commands(PRESSURE, SetField(field="code", value="481920"), SetField(field="confirm", value=True))
    chat = start(host)
    seen: dict[str, tuple] = {}
    heard: dict[str, str] = {}

    def step(key: str, label: str, act) -> None:
        act()
        seen[key] = where(chat)
        heard[key] = said(chat)
        if show:
            narrate(chat, label)

    step("asked", "user: I need to send some money", lambda: None)
    step("new", "chooses: someone new", lambda: chat.submit(pay_to="someone_new"))
    step("name", "names the payee", lambda: chat.submit(payee_name="Dana Lee"))
    step("typo", "types a routing number with one wrong digit", lambda: chat.submit(routing_number="021000022"))
    step("account", "gives the account number", lambda: chat.submit(account_number="4455667788"))
    step("routing", "corrects the routing number", lambda: chat.submit(routing_number="021000021"))
    saved_before = [e for e in bank().LEDGER if e["kind"] == "payee"]
    step("saved", "confirms the payee", lambda: chat.submit(confirm=True))
    payees_offered = (chat.state.get("pending_widget") or {}).get("data") or {}
    step("picked", "picks Dana Lee from the list", lambda: chat.submit(payee_id="P-1004"))
    step("too_much", "asks to send $2,600 (balance is $2,500)", lambda: chat.submit(amount=2600))
    step("amount", "asks to send $1,200 instead", lambda: chat.submit(amount=1200))
    step("pressure", f"user: {PRESSURE}", lambda: chat.user(PRESSURE))
    paid_under_pressure = [e for e in bank().LEDGER if e["kind"] == "payment"]
    step("wrong_code", "types a wrong code", lambda: chat.submit(code="000000"))
    step("code", "types the right code", lambda: chat.submit(code="481920"))
    step("paid", "confirms the payment", lambda: chat.submit(confirm=True))
    step("again", "presses confirm again", lambda: chat.submit(confirm=True))
    return seen, chat, saved_before, paid_under_pressure, payees_offered, heard


print("=" * 78)
print("PART 1 — someone new: two flows in a row")
print("=" * 78)
seen, chat, saved_before, paid_under_pressure, offered, heard = new_payee_then_pay(show=True)
ledger = [dict(e) for e in bank().LEDGER]
route = calls()
print()
check(seen["asked"] == ("send-money", [], "pay_to"), "send-money is in charge and asks its one question")
check(seen["new"] == ("add-payee", ["send-money"], "payee_name"),
      "someone new: the program hands off to add-payee, with send-money waiting underneath")
check("first add them as a payee" in heard["new"] and "saved payees" not in heard["new"],
      "and says which way it went before the first question of that flow")
check(seen["typo"][2] == "account_number" and seen["account"] == ("add-payee", ["send-money"], "routing_number"),
      "add-payee's own gate refuses the mistyped routing number and asks for it again")
check(saved_before == [], "nothing is saved before the user confirms")
check(seen["saved"] == ("pay-someone", ["send-money"], "payee_id"),
      "payee saved: back in send-money, which moves straight on to pay-someone")
check(heard["saved"].index("Saved Dana Lee") < heard["saved"].index("Now the payment"),
      "in that order on screen: the payee is saved, then send-money says the payment is next")
check([row["name"] for row in offered.get("rows", [])] == ["Mom", "Landlord", "City Gym", "Dana Lee"],
      "pay-someone's list already shows Dana Lee: it read the bank, nothing was passed between the flows")
check(seen["too_much"] == ("pay-someone", ["send-money"], "amount"),
      "pay-someone's own gate refuses $2,600 against a $2,500 balance")
check(seen["amount"] == ("pay-someone", ["send-money"], "code"),
      "$1,200 is over the threshold, so pay-someone's inner branch demands a code")
check(seen["pressure"][2] == "code" and paid_under_pressure == [],
      "a code and a yes supplied by the MODEL are dropped: still waiting for the code, nothing sent")
check(seen["wrong_code"][2] == "code", "a wrong code is refused")
check(seen["code"] == ("pay-someone", ["send-money"], "confirm"), "the right code reaches the confirmation")
check(seen["paid"][0] == "send-money" and seen["paid"][1] == [] and chat.done,
      "payment sent: back in send-money, which finishes")
check([e["kind"] for e in ledger] == ["payee", "payment"] and ledger[1]["amount"] == 1200.0
      and ledger[1]["payee_id"] == "P-1004",
      "two writes in total, in order: the payee, then one $1,200 payment to them")
check(route.count("save_payee") == 1 and route.count("send_payment") == 1, "confirming twice sent once")
check(route.index("save_payee") < route.index("list_payees"),
      "order across flows: add-payee finished before pay-someone read the payee list")
print("\n  bank calls, in order:")
print("   ", " -> ".join(route))

# =========================================================================== #
print()
print("=" * 78)
print("PART 2 — a saved payee: the other branch skips a whole flow")
print("=" * 78)
chat = start(TestHost())
chat.submit(pay_to="a_saved_payee")
narrate(chat, "chooses: a saved payee")
check(where(chat) == ("pay-someone", ["send-money"], "payee_id"),
      "a saved payee: straight to pay-someone; add-payee never starts")
check("Here are your saved payees" in said(chat) and "add them" not in said(chat),
      "and says so, which is why the next thing on screen is the payee list")
chat.submit(payee_id="P-1001").submit(amount=50)
narrate(chat, "picks Mom, $50")
check(where(chat) == ("pay-someone", ["send-money"], "confirm"), "$50 is under the threshold: no code step at all")
chat.submit(confirm=True)
narrate(chat, "confirms")
check(chat.done and "save_payee" not in calls() and "check_account" not in calls() and "verify_code" not in calls(),
      "done, and no add-payee tool and no code check was ever called")
check(bank().LEDGER == [{"kind": "payment", "reference": bank().LEDGER[0]["reference"], "payee_id": "P-1001", "amount": 50.0}],
      "one $50 payment to Mom, nothing else")

# =========================================================================== #
print()
print("=" * 78)
print("PART 3 — the flows are separate: fields do not cross, and cancelling stops the errand")
print("=" * 78)
host = TestHost()
host.queue_commands(NEVER_MIND, Cancel())
chat = start(host)
chat.submit(pay_to="someone_new")
check(chat.state["fields"] == {}, "add-payee starts with no fields: it cannot see send-money's answer")
chat.submit(payee_name="Dana Lee").submit(routing_number="021000021")
chat.user(NEVER_MIND)
narrate(chat, f"user: {NEVER_MIND}  (while add-payee is asking for the account number)")
check(chat.done and where(chat)[0] == "send-money",
      "cancelling inside add-payee comes back to send-money, which ends instead of going on to pay")
check(bank().LEDGER == [] and "list_payees" not in calls(), "nothing saved, nothing sent, and pay-someone never started")
check(chat.state["fields"] == {"pay_to": "someone_new"}, "send-money still has only its own answer")

# A real conversation: part-way through adding "John", the user typed "lets send to vivek" and
# the model read it as "start send-money". The errand ended with "No payee was added."
CHANGED_MIND = "lets send to vivek"
host = TestHost()
host.queue_commands(CHANGED_MIND, StartFlow(flow_id="send-money"), SetField(field="payee_name", value="Vivek"))
chat = start(host)
chat.submit(pay_to="someone_new").submit(payee_name="John")
chat.user(CHANGED_MIND)
narrate(chat, f"user: {CHANGED_MIND}  (while add-payee is asking for John's routing number; the model says 'start send-money')")
check(where(chat) == ("add-payee", ["send-money"], "routing_number") and "No payee was added" not in said(chat),
      "naming the flow that handed off does not end the errand: still in add-payee, on the same question")
check(chat.state["fields"].get("payee_name") == "Vivek", "and the new name is the one being added")

# =========================================================================== #
print()
print("=" * 78)
print("PART 4 — saying no at a confirmation stops there")
print("=" * 78)
chat = start(TestHost())
chat.submit(pay_to="someone_new").submit(payee_name="Dana Lee").submit(routing_number="021000021")
chat.submit(account_number="4455667788")
check(where(chat) == ("add-payee", ["send-money"], "confirm"), "add-payee has checked the account and asks: save this payee?")
chat.submit(confirm=False)
narrate(chat, "answers: no, do not save this payee")
check(chat.done and where(chat)[0] == "send-money" and "No payee was added" in said(chat),
      "no: nothing is saved, send-money says so and ends")
check("list_payees" not in calls() and "save_payee" not in calls() and bank().LEDGER == [],
      "pay-someone never starts: declining a payee does not lead to 'who are you paying?'")

chat = start(TestHost())
chat.submit(pay_to="a_saved_payee").submit(payee_id="P-1002").submit(amount=75)
check(where(chat) == ("pay-someone", ["send-money"], "confirm"), "pay-someone asks: send this payment?")
chat.submit(confirm=False)
narrate(chat, "answers: no, do not send it")
check(chat.done and "No payment was made" in said(chat) and "That is everything" not in said(chat),
      "no: send-money reports that no payment was made, not that the errand is complete")
check("send_payment" not in calls() and bank().LEDGER == [], "and nothing was sent")

chat = start(TestHost())
chat.submit(pay_to="someone_new").submit(payee_name="Dana Lee").submit(routing_number="021000021")
chat.submit(account_number="4455667788").submit(confirm=True)
chat.submit(payee_id="P-1004").submit(amount=40).submit(confirm=False)
narrate(chat, "adds Dana Lee, then says no to paying her")
check("The payee you added is still saved" in said(chat) and [e["kind"] for e in bank().LEDGER] == ["payee"],
      "no after a payee was added: the payment is not made, and it says the payee was kept")

# =========================================================================== #
print()
print("=" * 78)
print("PART 5 — a cancelled errand is over, and it is not forgotten")
print("=" * 78)
CONTINUE = "ok continue where I left off"
host = TestHost()
host.queue_commands(NEVER_MIND, Cancel())
chat = start(host)
chat.submit(pay_to="a_saved_payee").submit(payee_id="P-1002").submit(amount=1500).submit(code="481920")
check(where(chat) == ("pay-someone", ["send-money"], "confirm"),
      "a $1,500 payment to the landlord, code entered, waits for the yes")
chat.user(NEVER_MIND)
narrate(chat, f"user: {NEVER_MIND}")
record = chat.state["journal"]
check([(e["flow"], e["outcome"]) for e in record] == [("pay-someone", "cancelled"), ("send-money", "stopped")]
      and bank().LEDGER == [],
      "cancelled: nothing sent, and the record says which flow was cancelled and that the errand stopped there")
check(record[0]["fields"] == {"payee_id": "P-1002", "amount": 1500, "code": "(hidden)"} and "Landlord" in record[0]["results"],
      "the record keeps the payee and the amount ('who was I paying?' has an answer) and never the code")

# With nothing running the model says "go back to it", and tries to approve it in the same breath.
host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="resume"))
host.queue_commands(CONTINUE, SetField(field="confirm", value=True), SetField(field="code", value="481920"))
chat.user(CONTINUE)
narrate(chat, f"user: {CONTINUE}  (the model also sends the code and a yes)")
check(where(chat) == ("pay-someone", ["send-money"], "code")
      and chat.state["fields"] == {"payee_id": "P-1002", "amount": 1500.0},
      "picked up inside pay-someone with the payee and the amount kept; the code and the yes are asked for again")
check(bank().LEDGER == [] and "send_payment" not in calls(), "picking it up sent nothing")
chat.submit(code="481920").submit(confirm=True)
check(chat.done and [e["kind"] for e in bank().LEDGER] == ["payment"] and bank().LEDGER[0]["amount"] == 1500.0,
      "with the code and a fresh yes it is sent, once, and send-money finishes on its 'done' line")

START_OVER = "start over"
host = TestHost()
host.queue_commands(START_OVER, Restart())
chat = start(host)
chat.submit(pay_to="someone_new").submit(payee_name="Dana Lee")
chat.user(START_OVER)
narrate(chat, f"user: {START_OVER}  (while add-payee is asking for the routing number)")
check(where(chat) == ("send-money", [], "pay_to") and chat.state["fields"] == {} and bank().LEDGER == [],
      "start over: back at send-money's first question in one message, nothing kept, nothing saved")

# =========================================================================== #
print()
print("=" * 78)
print("PART 6 — the same conversation, five more times")
print("=" * 78)
new_payee_then_pay(show=False)
reference = (digest(bank().CALL_LOG), digest(bank().LEDGER))
for run in range(1, 6):
    again_seen, *_ = new_payee_then_pay(show=False)
    again = (digest(bank().CALL_LOG), digest(bank().LEDGER))
    check(again == reference and again_seen == seen,
          f"run {run}: same route through the flows, same bank calls, same two writes  {again[0]} / {again[1]}")

print(f"\nRESULT: PASS — {CHECKS} checks.")

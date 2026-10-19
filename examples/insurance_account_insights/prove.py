"""Prove the insurance account-insights scenario end-to-end — ONE SKILL.md,
zero Python flow code, zero widget JSON.

What this drives (all LLM-free via TestHost scripting, so it runs anywhere):

  A. Full path      search "william" -> client grid -> account grid ->
                    validate -> account card -> chat greeting
  B. Chat Q&A       "what's the payment history?" -> model picks the tool,
                    the ACCOUNT NUMBER IS PINNED from validated state ->
                    timeline widget + natural answer
  C. Account switch "look at account ACC-2002 instead" -> correction ->
                    re-validation runs FRESH (not a stale memo) -> new card
  D. Fast path      new conversation: "show me account ACC-9999" -> skips
                    search entirely -> validation REJECTS -> re-asks ->
                    user gives ACC-2001 -> validated -> chat open
  E. Pin override   a tool call the model aims at a DIFFERENT account is
                    forced back to the validated one

Run:  python prove.py        # no API key needed
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.host import ToolCall
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Correct, SetField

# --------------------------------------------------------------------------- #
# Mock backends: two REST endpoints + a validation endpoint + four MCP tools.
# --------------------------------------------------------------------------- #
CLIENTS = [
    {"id": "C-001", "full_name": "William Smith"},
    {"id": "C-002", "full_name": "Willa Williams"},
]
ACCOUNTS = {
    "C-001": [
        {"account_number": "ACC-2001", "account_type": "auto", "status": "active"},
        {"account_number": "ACC-2002", "account_type": "home", "status": "active"},
    ]
}
VALID_ACCOUNTS = {
    "ACC-2001": {"holder": "William Smith", "type": "auto", "status": "active",
                 "premium": 128.50},
    "ACC-2002": {"holder": "William Smith", "type": "home", "status": "active",
                 "premium": 89.00},
}

validate_calls: list[str] = []
tool_calls: list[tuple[str, dict]] = []


def search_clients(args):
    """Search clients by (partial) name."""
    return {"matches": [c for c in CLIENTS if args["name"].lower() in c["full_name"].lower()]}


def find_accounts(args):
    """List a client's accounts."""
    return {"accounts": ACCOUNTS.get(args["client_id"], [])}


def validate_account(args):
    """Check an account exists; return its header details."""
    validate_calls.append(args["account_number"])
    details = VALID_ACCOUNTS.get(args["account_number"])
    return {"valid": details is not None, "details": details or {}}


def account_summary(args):
    """Account summary: holder, type, status, premium."""
    tool_calls.append(("account_summary", dict(args)))
    return {"account": args["account_number"], **VALID_ACCOUNTS[args["account_number"]]}


def payment_history(args):
    """Payment history for an account."""
    tool_calls.append(("payment_history", dict(args)))
    return [
        {"date": "2026-05-01", "amount": 128.50, "method": "card"},
        {"date": "2026-06-01", "amount": 128.50, "method": "card"},
    ]


def insurance_coverage(args):
    """Coverage lines and limits for an account."""
    tool_calls.append(("insurance_coverage", dict(args)))
    return {"liability": "300k", "collision": "50k deductible 500"}


def claims(args):
    """Open and past claims on an account."""
    tool_calls.append(("claims", dict(args)))
    return [{"date": "2025-11-12", "type": "windshield", "status": "paid"}]


def insurance_faq(args):
    """General insurance questions (coverage types, terms, definitions)."""
    tool_calls.append(("insurance_faq", dict(args)))
    return {"answer": "Auto insurance covers liability, collision, and comprehensive damage."}


TOOLS = {
    "search_clients": search_clients,
    "find_accounts": find_accounts,
    "validate_account": validate_account,
    "account_summary": account_summary,
    "payment_history": payment_history,
    "insurance_coverage": insurance_coverage,
    "claims": claims,
    "insurance_faq": insurance_faq,
}

fails: list[str] = []


def expect(cond, label):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        fails.append(label)


def new_agent():
    host = TestHost()
    agent = FlowAgent.from_dir(HERE / "skills", host=host, tools=TOOLS)
    return host, agent


def turn_msg(agent, st, host, msg):
    st["pending_user_message"] = msg
    st = agent.turn(st)
    return st, host.drain_pending_messages(), host.drain_pending_widgets()


def turn_submit(agent, st, host, **updates):
    st["pending_field_updates"] = dict(updates)
    st = agent.turn(st)
    return st, host.drain_pending_messages(), host.drain_pending_widgets()


# =========================================================================== #
print("\n--- A. full path: search -> client grid -> account grid -> card -> chat ---")
host, agent = new_agent()
host.queue_classification("find clients named william", "account-insights")
host.queue_commands(
    "find clients named william", SetField(field="search_name", value="william")
)
st = initial_state()

st, msgs, widgets = turn_msg(agent, st, host, "find clients named william")
pw = st.get("pending_widget")
expect(pw is not None and pw["widget"] == "pick:client_id", "turn 1 renders the client grid")
expect(pw and pw["spec"]["children"][0]["children"][0]["type"] == "datatable",
       "client grid is an auto-generated datatable")
expect(pw and len(pw["data"]["rows"]) == 2, "grid shows both matching clients")

print("\n--- F. mid-flow side question: answered for real, then back to the flow ---")
host.queue_commands("what does auto insurance cover?")   # interpreter: not a command
host.queue_tool_calls("what does auto insurance cover?",
                      ToolCall(name="insurance_faq", args={"q": "auto coverage"}))
host.queue_chat_answer("insurance_faq", "Liability, collision, and comprehensive damage.")
st, msgs, widgets = turn_msg(agent, st, host, "what does auto insurance cover?")
expect(tool_calls[-1][0] == "insurance_faq", "assist routed the digression to the FAQ tool")
expect(any("collision" in m for m in msgs), "the side question got a real answer")
pw = st.get("pending_widget")
expect(pw is not None and pw["widget"] == "pick:client_id",
       "…and the flow re-prompted the SAME pending step (client grid) — no derailment")

st, msgs, widgets = turn_submit(agent, st, host,
                                client_id="C-001", client_full_name="William Smith")
pw = st.get("pending_widget")
expect(pw is not None and pw["widget"] == "pick:account_number",
       "client pick leads straight to the account grid")
expect(pw and pw["data"]["rows"][0]["account_number"] == "ACC-2001",
       "account grid is bound to the picked client's accounts")

st, msgs, widgets = turn_submit(agent, st, host, account_number="ACC-2001")
expect(validate_calls == ["ACC-2001"], "validation ran exactly once")
expect(any(w.get("props", {}).get("title") == "Account ACC-2001" for w in widgets),
       "account details rendered as a card widget")
expect(any("Ask me anything" in m for m in msgs), "chat greeting emitted")
expect(st.get("chat_config") is not None, "flow is parked on the chat step")

print("\n--- B. chat Q&A: model picks the tool; account is pinned ---")
host.queue_commands("what's the payment history?")  # interpreter: nothing actionable
host.queue_tool_calls("what's the payment history?",
                      ToolCall(name="payment_history", args={}))
host.queue_chat_answer("payment_history", "Two card payments of $128.50, May and June.")
st, msgs, widgets = turn_msg(agent, st, host, "what's the payment history?")
expect(tool_calls[-1] == ("payment_history", {"account_number": "ACC-2001"}),
       "pinned account_number injected into the tool call")
expect(any(w["children"][0]["type"] == "timeline" for w in widgets),
       "dated rows auto-rendered as a TIMELINE widget")
expect(any("128.50" in m for m in msgs), "natural-language answer emitted")

print("\n--- G. post-gate chat grounded in workflow outputs (context:) ---")
host.queue_commands("who is the holder again?")
host.queue_tool_calls("who is the holder again?")        # model picks no tool
host.queue_chat_answer("who is the holder again?", "William Smith — it's his auto policy.")
st, msgs, widgets = turn_msg(agent, st, host, "who is the holder again?")
expect("William Smith" in getattr(host, "last_llm_text_system", ""),
       "workflow outputs (validate_account details) embedded in the answer context")
expect(any("William Smith" in m for m in msgs), "grounded answer emitted without a tool call")

print("\n--- E. pin override: model aims at another account, pin wins ---")
host.queue_commands("show claims for account ACC-9999")
host.queue_tool_calls("show claims for account ACC-9999",
                      ToolCall(name="claims", args={"account_number": "ACC-9999"}))
host.queue_chat_answer("claims", "One windshield claim, paid.")
st, msgs, widgets = turn_msg(agent, st, host, "show claims for account ACC-9999")
expect(tool_calls[-1] == ("claims", {"account_number": "ACC-2001"}),
       "pin OVERRIDES the model's attempt to query a different account")

print("\n--- C. account switch: correction re-validates FRESH ---")
host.queue_commands("actually look at account ACC-2002 instead",
                    Correct(field="account_number", value="ACC-2002"))
st, msgs, widgets = turn_msg(agent, st, host, "actually look at account ACC-2002 instead")
expect(validate_calls[-1] == "ACC-2002", "switch re-ran validation for the new account")
expect(any(w.get("props", {}).get("title") == "Account ACC-2002" for w in widgets),
       "new account card rendered")
expect(st.get("chat_config") is not None, "chat re-opened for the new account")

host.queue_commands("summary please")
host.queue_tool_calls("summary please", ToolCall(name="account_summary", args={}))
host.queue_chat_answer("account_summary", "Home policy, active, $89/month.")
st, msgs, widgets = turn_msg(agent, st, host, "summary please")
expect(tool_calls[-1] == ("account_summary", {"account_number": "ACC-2002"}),
       "chat questions now pin the NEW account")

print("\n--- D. fast path: direct (invalid) account -> reject -> recover ---")
validate_calls.clear()
host2, agent2 = new_agent()
host2.queue_classification("show me account ACC-9999", "account-insights")
host2.queue_commands("show me account ACC-9999",
                     SetField(field="account_number", value="ACC-9999"))
st2 = initial_state()
st2, msgs, widgets = turn_msg(agent2, st2, host2, "show me account ACC-9999")
expect(validate_calls == ["ACC-9999"], "fast path skipped search, went straight to validation")
expect(any("doesn't exist" in m for m in msgs), "invalid account rejected with the else_say")
expect(st2.get("pending_widget") is None, "no grid rendered on the fast path")
expect(st2["fields"].get("account_number") is None, "bad account cleared for re-collection")

host2.queue_commands("ok try ACC-2001",
                     SetField(field="account_number", value="ACC-2001"))
st2, msgs, widgets = turn_msg(agent2, st2, host2, "ok try ACC-2001")
expect(validate_calls[-1] == "ACC-2001", "recovery re-validated the new number")
expect(any(w.get("props", {}).get("title") == "Account ACC-2001" for w in widgets),
       "valid account renders the card")
expect(st2.get("chat_config") is not None, "chat opens after the gate passes")

# =========================================================================== #
print()
if fails:
    print(f"RESULT: {len(fails)} FAILURE(S)")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED — search grid -> account grid -> validated "
      "account -> pinned agentic Q&A, plus fast path, rejection, and switch.")

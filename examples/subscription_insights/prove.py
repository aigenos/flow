"""Prove the subscription scenario end-to-end — deterministic widgets + agentic tail.

  A. full path:   "show subscriptions for ava"
                    -> REST list_subscriptions -> subscription_grid (single-select
                       a2ui grid, fixed by id) -> user picks Netflix
                    -> MCP get_subscription_details -> subscription_details
                       (composed card + billing grid + timeline, fixed by id)
                    -> free chat about THIS subscription (service pinned)
  B. skip-ahead:  "show my disney plus subscription for ava"
                    -> the list call and the grid NEVER run; the flow fast-paths
                       straight to the details widget (when_missing guards)
  C. determinism: same submits twice -> identical widget ids, data, and order.

Run:  python prove.py        # no API key needed (scripted interpreter)
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
from aigenos_flow.interpret import SetField

# ---- the two host tools (your REST endpoint + MCP server) ----------------- #
list_calls: list[dict] = []
detail_calls: list[dict] = []

CATALOG = [
    {"id": "netflix", "service": "Netflix", "plan": "Premium 4K", "monthly_price": "$22.99", "status": "active"},
    {"id": "prime_video", "service": "Prime Video", "plan": "Annual", "monthly_price": "$8.99", "status": "active"},
    {"id": "disney_plus", "service": "Disney+", "plan": "Standard", "monthly_price": "$13.99", "status": "past due"},
]

DETAILS = {
    "netflix": {
        "service": "Netflix", "plan": "Premium 4K", "status": "active",
        "monthly_price": "$22.99", "pending_due": "$0.00",
        "next_billing": "2026-08-14", "member_since": "2021-03-02",
        "billing_history": [
            {"date": "2026-07-14", "amount": "$22.99", "status": "paid"},
            {"date": "2026-06-14", "amount": "$22.99", "status": "paid"},
        ],
        "events": [
            {"time": "2026-05-01", "title": "Plan upgraded", "description": "Standard → Premium 4K"},
            {"time": "2021-03-02", "title": "Subscribed"},
        ],
    },
    "disney_plus": {
        "service": "Disney+", "plan": "Standard", "status": "past due",
        "monthly_price": "$13.99", "pending_due": "$27.98",
        "next_billing": "2026-08-03", "member_since": "2023-11-20",
        "billing_history": [
            {"date": "2026-07-03", "amount": "$13.99", "status": "failed"},
            {"date": "2026-06-03", "amount": "$13.99", "status": "failed"},
        ],
        "events": [
            {"time": "2026-06-03", "title": "Payment failed", "description": "Card expired"},
            {"time": "2023-11-20", "title": "Subscribed"},
        ],
    },
}


def list_subscriptions(args):              # GET /subscriptions?first_name=…
    list_calls.append(dict(args))
    return {"subscriptions": CATALOG}


def get_subscription_details(args):        # MCP: full details for one service
    detail_calls.append(dict(args))
    return DETAILS[args["service"]]


TOOLS = {
    "list_subscriptions": list_subscriptions,
    "get_subscription_details": get_subscription_details,
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


def run_full_path():
    """Returns the trace [(widget_id, data_keys)] for the determinism check."""
    trace = []
    host, agent = new_agent()
    host.queue_classification("show subscriptions for ava", "subscription-insights")
    host.queue_commands("show subscriptions for ava", SetField(field="first_name", value="ava"))
    st = initial_state()
    st, msgs, widgets = turn_msg(agent, st, host, "show subscriptions for ava")
    pw = st.get("pending_widget")
    trace.append((pw and pw["widget"], pw and sorted(pw["data"])))

    st, msgs, widgets = turn_submit(agent, st, host,
                                    subscription_id="netflix", subscription_name="Netflix")
    pw = st.get("pending_widget")
    trace.append((pw and pw["widget"], pw and sorted(pw["data"])))

    st, msgs, widgets = turn_submit(agent, st, host, details_viewed=True)
    trace.append(("chat", st.get("chat_config") is not None))
    return host, agent, st, msgs, trace


# =========================================================================== #
print("\n--- A. full path: name -> grid -> select Netflix -> composed details -> chat ---")
host, agent, st, msgs, trace = run_full_path()

wid, data_keys = trace[0]
expect(wid == "subscription_grid", "turn 1 renders the subscription_grid (by catalog id)")
expect(list_calls[-1] == {"first_name": "ava"}, "list_subscriptions called with the typed name")

# re-run turn 1 to inspect the data payload precisely
host2, agent2 = new_agent()
host2.queue_classification("show subscriptions for ava", "subscription-insights")
host2.queue_commands("show subscriptions for ava", SetField(field="first_name", value="ava"))
st2 = initial_state()
st2, _m, _w = turn_msg(agent2, st2, host2, "show subscriptions for ava")
pw = st2.get("pending_widget")
expect(pw and [r["id"] for r in pw["data"]["rows"]] == ["netflix", "prime_video", "disney_plus"],
       "grid rows bound from the REST payload, stable order")
expect(pw and pw["fields"] == ["subscription_id", "subscription_name"],
       "row select fills subscription_id + subscription_name (LLM-free submit)")

wid, data_keys = trace[1]
expect(wid == "subscription_details", "selection leads to the composed details widget (by id)")
expect(detail_calls[-1] == {"service": "netflix", "first_name": "ava"},
       "details tool called with the SELECTED row's id — pinned, not model-chosen")
expect(data_keys == sorted(["service", "plan", "status", "monthly_price", "pending_due",
                            "next_billing", "member_since", "billing", "events"]),
       "details widget receives highlights + billing rows + events bindings")

expect(trace[2] == ("chat", True), "after details, the flow parks on the scoped chat step")
expect(any("Ask me anything" in m for m in msgs), "chat greeting emitted")

print("\n--- chat Q&A: model picks the tool; the subscription is pinned ---")
host.queue_commands("what's my pending due?")          # interpreter: not a command
host.queue_tool_calls("what's my pending due?",
                      ToolCall(name="get_subscription_details", args={"service": "prime_video"}))
host.queue_chat_answer("get_subscription_details", "Nothing pending — $0.00, next billing Aug 14.")
st, msgs, widgets = turn_msg(agent, st, host, "what's my pending due?")
expect(detail_calls[-1] == {"service": "netflix", "first_name": "ava"},
       "pin OVERRIDES the model's args — it asked for prime_video, the runtime sent netflix")
expect(any("Nothing pending" in m for m in msgs), "free-form question got a real answer")

print("\n--- B. skip-ahead: user names the service up front ---")
list_calls.clear()
host3, agent3 = new_agent()
host3.queue_classification("show my disney plus subscription, I'm ava", "subscription-insights")
host3.queue_commands("show my disney plus subscription, I'm ava",
                     SetField(field="first_name", value="ava"),
                     SetField(field="subscription_id", value="disney_plus"))
st3 = initial_state()
st3, msgs3, w3 = turn_msg(agent3, st3, host3, "show my disney plus subscription, I'm ava")
pw3 = st3.get("pending_widget")
expect(pw3 is not None and pw3["widget"] == "subscription_details",
       "grid SKIPPED — details widget renders directly (when_missing fast-path)")
expect(list_calls == [], "list_subscriptions never called on the skip-ahead path")
expect(pw3 and pw3["data"]["pending_due"] == "$27.98",
       "details bound to the named service (Disney+ past-due)")

print("\n--- C. determinism: identical inputs -> identical widget sequence ---")
list_calls.clear(); detail_calls.clear()
_h, _a, _s, _m, trace_b = run_full_path()
expect(trace == trace_b, "same submits twice -> same widget ids, same data keys, same order")

print("\n--- D. scripts.py: the skill's own tools load with NOTHING injected ---")
# Everything above injected mocks (3 rows). The skill's scripts.py ships the
# full catalog (5 rows) — so the row count proves which one served the call.
mock_calls_before = len(list_calls)
host4 = TestHost()
agent4 = FlowAgent.from_dir(HERE / "skills", host=host4)      # note: no tools=
host4.queue_classification("show subscriptions for ava", "subscription-insights")
host4.queue_commands("show subscriptions for ava", SetField(field="first_name", value="ava"))
st4 = initial_state()
st4, _m4, _w4 = turn_msg(agent4, st4, host4, "show subscriptions for ava")
pw4 = st4.get("pending_widget")
expect(pw4 is not None and pw4["widget"] == "subscription_grid",
       "flow runs with zero host wiring — scripts.py satisfied `allowed-tools`")
expect(pw4 and len(pw4["data"]["rows"]) == 5,
       "rows came from the skill's OWN scripts.py (5-row catalog, not the 3-row mock)")
expect(len(list_calls) == mock_calls_before,
       "…and the injected mock was NOT called on that path (registries stay separate)")

print("\n" + "=" * 66)
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("PASS — the full scenario, one SKILL.md, zero Python agent code:")
print("  REST list -> single-select a2ui grid -> MCP details -> composed")
print("  card+grid+timeline (both fixed by catalog id) -> pinned free chat,")
print("  with the grid auto-skipped when the user names the service up front.")

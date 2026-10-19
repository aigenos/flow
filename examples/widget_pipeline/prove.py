"""Prove the multi-turn, heterogeneous-widget pipeline — your exact scenario.

Three turns, three DIFFERENT widgets, each bound to the previous tool's result,
each submit feeding the next step — all declared in SKILL.md, driven with no LLM.

  turn 1: user searches "william"  -> REST search_clients -> render an autosuggest
          FORM (client_picker) bound to the matches
  turn 2: user picks a client       -> REST find_accounts  -> render a DATATABLE
          (account_table) bound to that client's accounts
  turn 3: user picks an account     -> the flow summarises and PARKS on the
          confirmation gate; the MCP write is unreachable until then
  turn 4: user confirms             -> call the MCP server with the chosen account

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
from aigenos_flow.hosts.test_host import TestHost

# ---- the three host tools (your REST endpoints + MCP server) -------------- #
seen_args: dict[str, dict] = {}


def search_clients(args):                      # GET /clients?name=william
    seen_args["search_clients"] = dict(args)
    return {"matches": [
        {"id": "C-001", "full_name": "William Smith"},
        {"id": "C-002", "full_name": "Williams Jones"},
        {"id": "C-003", "full_name": "Will Williamson"},
    ]}


def find_accounts(args):                       # GET /clients/{id}/accounts
    seen_args["find_accounts"] = dict(args)
    return {"accounts": [
        {"account_number": "ACC-100", "account_type": "checking", "balance": 4210.55},
        {"account_number": "ACC-200", "account_type": "savings", "balance": 18900.00},
    ]}


mcp_calls: list[dict] = []


def account_action_mcp(args):                  # MCP call with the chosen account
    mcp_calls.append(dict(args))
    return {"status": "ok", "ref": "MCP-42"}


agent = FlowAgent.from_dir(
    HERE / "skills",
    host=TestHost(),
    tools={
        "search_clients": search_clients,
        "find_accounts": find_accounts,
        "account_action_mcp": account_action_mcp,
    },
)

fails: list[str] = []


def turn(state, **updates):
    state["pending_field_updates"] = dict(updates)
    return agent.turn(state)


st = initial_state()
st["flow_id"] = "client-account-lookup"

# turn 1 — search "william" -> the client_picker (autosuggest form) is rendered,
# bound to the search matches
st = turn(st, search_name="william")
pw = st.get("pending_widget")
print(f"turn 1 -> widget={pw and pw['widget']!r}  options={len(pw['data']['options']) if pw else 0}")
if not pw or pw["widget"] != "client_picker":
    fails.append("turn 1: expected the client_picker widget")
elif [o["full_name"] for o in pw["data"]["options"]] != ["William Smith", "Williams Jones", "Will Williamson"]:
    fails.append("turn 1: autosuggest options not bound from the search result")
elif pw["fields"] != ["client_id", "client_full_name"]:
    fails.append("turn 1: widget should collect client_id + client_full_name")
if seen_args.get("search_clients") != {"name": "william"}:
    fails.append("turn 1: search_clients not called with the typed name")

# turn 2 — submit the selected client -> find_accounts runs -> account_table
# (datatable) is rendered, bound to that client's accounts
st = turn(st, client_id="C-001", client_full_name="William Smith")
pw = st.get("pending_widget")
print(f"turn 2 -> widget={pw and pw['widget']!r}  rows={len(pw['data']['rows']) if pw else 0}")
if seen_args.get("find_accounts") != {"full_name": "William Smith", "client_id": "C-001"}:
    fails.append("turn 2: find_accounts not called with the selected client")
if not pw or pw["widget"] != "account_table":
    fails.append("turn 2: expected the account_table widget")
elif [r["account_number"] for r in pw["data"]["rows"]] != ["ACC-100", "ACC-200"]:
    fails.append("turn 2: datatable rows not bound from the accounts result")
elif pw["fields"] != ["account_number", "account_type"]:
    fails.append("turn 2: widget should collect account_number + account_type")

# turn 3 — submit the selected account. The MCP call is a WRITE, so it is gated:
# the flow summarises and stops on `confirm`. Nothing may fire yet.
st = turn(st, account_number="ACC-100", account_type="checking")
print(f"turn 3 -> awaiting={st.get('awaiting_field')!r}  done={st.get('done')}  mcp_calls={mcp_calls}")
if st.get("awaiting_field") != "confirm":
    fails.append(
        f"turn 3: the write must park on the confirm gate, got "
        f"awaiting={st.get('awaiting_field')!r}"
    )
if mcp_calls:
    fails.append(f"turn 3: the MCP write fired BEFORE the user confirmed: {mcp_calls}")

# turn 4 — the user confirms (a structured submission, i.e. a real user action)
# -> the MCP call fires once, with the full pipeline context.
st = turn(st, confirm=True)
print(f"turn 4 -> done={st.get('done')}  mcp_calls={mcp_calls}")
if not st.get("done"):
    fails.append("turn 4: flow should complete after the confirmed account action")
expected = [{
    "client_id": "C-001", "full_name": "William Smith",
    "account_number": "ACC-100", "account_type": "checking",
}]
if mcp_calls != expected:
    fails.append(f"turn 4: MCP not called with the chosen account: {mcp_calls}")

# turn 5 — re-submitting the same confirmation must NOT fire a second write
# (at-most-once per flow run, keyed on flow_run_id).
st = turn(st, confirm=True)
if len(mcp_calls) != 1:
    fails.append(f"turn 5: the MCP write fired {len(mcp_calls)} times in one run")

print("\n" + "=" * 62)
if fails:
    print("FAILED:")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("PASS — one declarative skill, four turns, three different widgets:")
print("  autosuggest form -> datatable -> confirmation gate -> MCP call, each")
print("  bound to the prior tool result, each submit (LLM-free) driving the")
print("  next step, and the write unreachable until the user confirms it.")

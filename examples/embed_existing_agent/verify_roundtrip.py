"""Proof: the widget round-trip is deterministic and the 2FA gate is structural.

This is the integration pattern for embedding aigenos/flow in an existing
backend (LangGraph or anything else) while keeping YOUR OWN frontend.

The key fact this script demonstrates: a STRUCTURED field submission (the JSON
your a2ui widget posts back) is applied by pure Python — the interpreter LLM is
never called. So we drive the whole flow with `TestHost` (no model, no API key)
and the behaviour is byte-identical every run.

What your frontend does each turn:
    1. read `state["awaiting_field"]`  + `skill.fields[awaiting_field]` (a FieldSpec)
    2. render YOUR a2ui widget for that field's type/options/range
    3. on submit, call `turn(state, {field: value})`
    4. show `assistant_messages`; repeat until `state["done"]`

Run:  python verify_roundtrip.py      (no API key needed)
"""
import sys
from pathlib import Path

# Flows may emit emoji (📋/✅); make stdout UTF-8 so the demo prints on a
# Windows cp1252 console too. Harmless elsewhere.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.hosts.test_host import TestHost

SKILLS = Path(__file__).resolve().parent / "skills"
THRESHOLD = 1000.0  # transfer-funds policy: >= $1000 requires a 2FA code


def make_agent() -> FlowAgent:
    # TestHost has no LLM. Because we only ever submit STRUCTURED field updates
    # (never free text) and we preset flow_id, no interpreter/classifier call
    # is ever made — the run is pure deterministic Python.
    return FlowAgent.from_dir(SKILLS, host=TestHost(), mode="flex")


def fresh_state():
    s = initial_state()
    s["flow_id"] = "transfer-funds"   # skip the LLM router; we know the skill
    return s


def turn(agent: FlowAgent, state: dict, **field_updates):
    """One round-trip: submit a widget's structured answer, get the next ask."""
    state["pending_field_updates"] = dict(field_updates)
    state = agent.turn(state)
    msgs = agent.host.drain_pending_messages()
    return state, msgs


def transfer_fired(state) -> bool:
    # ctx.run(submit_transfer) memoizes under the action name; its presence in
    # results means money actually moved.
    res = state.get("results", {}).get("submit_transfer")
    return bool(res and res.get("ok"))


def show(tag, state, msgs):
    print(f"  [{tag}] awaiting={state.get('awaiting_field')!r} "
          f"done={state.get('done')} fired={transfer_fired(state)}")
    for m in msgs:
        print(f"        » {m}")


def main():
    agent = make_agent()
    failures = []

    # ── A. Over-threshold: the gate MUST appear and MUST block the transfer ──
    print("\nA) $5000 transfer — 2FA gate must engage")
    st = fresh_state()
    st, msgs = turn(agent, st, amount=5000, from_account="checking", recipient="mom")
    show("collect", st, msgs)
    if st.get("awaiting_field") != "verification_code":
        failures.append("A: expected to pause on verification_code after a $5000 amount")

    # Try to force confirmation WITHOUT a code — the structural gate ignores it.
    st, msgs = turn(agent, st, confirm=True)
    show("confirm-without-code", st, msgs)
    if transfer_fired(st):
        failures.append("A: GATE BYPASSED — $5000 transfer fired with no 2FA code!")
    if st.get("awaiting_field") != "verification_code":
        failures.append("A: gate should still be holding for verification_code")

    # Provide the code, then confirm — now it completes.
    st, msgs = turn(agent, st, verification_code="481920")
    show("code", st, msgs)
    st, msgs = turn(agent, st, confirm=True)
    show("confirm", st, msgs)
    if not transfer_fired(st):
        failures.append("A: transfer should complete once code + confirm are given")

    # ── B. Under-threshold: NO gate, straight to confirm ──
    print("\nB) $500 transfer — no 2FA gate")
    st = fresh_state()
    st, msgs = turn(agent, st, amount=500, from_account="checking", recipient="mom")
    show("collect", st, msgs)
    if st.get("awaiting_field") != "confirm":
        failures.append("B: a $500 transfer should skip the code and ask for confirm")
    st, msgs = turn(agent, st, confirm=True)
    show("confirm", st, msgs)
    if not transfer_fired(st):
        failures.append("B: a confirmed $500 transfer should complete")

    # ── C. Mid-flow correction re-arms the gate ──
    print("\nC) $500 → corrected to $5000 — gate must re-engage")
    st = fresh_state()
    st, msgs = turn(agent, st, amount=500, from_account="checking", recipient="mom")
    show("collect $500", st, msgs)            # paused on confirm (under threshold)
    st, msgs = turn(agent, st, amount=5000)   # correction pushes over the line
    show("bump to $5000", st, msgs)
    if st.get("awaiting_field") != "verification_code":
        failures.append("C: bumping $500→$5000 must re-engage the 2FA gate")
    st, msgs = turn(agent, st, confirm=True)  # still no code → still blocked
    show("confirm-without-code", st, msgs)
    if transfer_fired(st):
        failures.append("C: GATE BYPASSED after correction — fired with no code!")

    print("\n" + "=" * 60)
    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("PASS — the 2FA gate held on every path; widget round-trip is")
    print("deterministic with zero LLM calls. Embed this in your existing")
    print("backend and your a2ui frontend renders awaiting_field as a form.")


if __name__ == "__main__":
    main()

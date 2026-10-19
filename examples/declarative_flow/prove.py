"""Prove a 100% DECLARATIVE skill (no handlers.py) has the full guarantee set.

The flow, the 2FA gate (a `compute:` expression), and the side effect (a `tool:`
reference) are all declared in SKILL.md. The ONLY code is the host tool the
operator grants — shared across skills, not per-skill control flow. Driven with
TestHost (no LLM), so it's deterministic and instant.

    python prove.py        # no API key needed
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from aigenos_flow import AuditSink, FlowAgent, initial_state
from aigenos_flow.hosts.test_host import TestHost

# The granted host tool the declarative `actions: submit_transfer -> tool:
# bank_transfer` resolves to. Records every real "money move" so we can assert
# how many fired and with what (typed) args.
calls: list[dict] = []


def bank_transfer(args: dict) -> dict:
    calls.append(dict(args))
    return {"id": f"TXN-{len(calls)}"}


audit = AuditSink()
agent = FlowAgent.from_dir(
    HERE / "skills", host=TestHost(), tools={"bank_transfer": bank_transfer}, audit=audit
)

fails: list[str] = []


def fresh():
    s = initial_state()
    s["flow_id"] = "transfer-funds"
    return s


def turn(state, **updates):
    state["pending_field_updates"] = dict(updates)
    return agent.turn(state)


# A) over-threshold: the 2FA gate engages and blocks the tool until a code is given
calls.clear()
st = fresh()
st = turn(st, amount=5000, from_account="checking", recipient="mom")
print(f"A) $5000              -> awaiting={st['awaiting_field']!r} fired={len(calls)}")
if st["awaiting_field"] != "verification_code":
    fails.append("A: 2FA gate did not engage on $5000")
st = turn(st, confirm=True)                 # try to confirm without a code
if calls:
    fails.append("A: tool fired with NO verification code")
st = turn(st, verification_code="481920")   # provide the code
if st["awaiting_field"] != "confirm" or calls:
    fails.append("A: the yes forced before the code must not be kept")
st = turn(st, confirm=True)                 # the confirmation, now that it is asked -> completes
print(f"   code, then yes     -> fired={len(calls)} args={calls[-1] if calls else None}")
if len(calls) != 1:
    fails.append(f"A: expected exactly one transfer, got {len(calls)}")
elif calls[0].get("otp") != "481920" or calls[0].get("amount") != 5000.0:
    fails.append(f"A: args not type-bound correctly: {calls[0]}")

# B) under-threshold: no gate; otp binds to None (unset field, not a crash)
calls.clear()
st = fresh()
st = turn(st, amount=500, from_account="checking", recipient="mom")
print(f"B) $500               -> awaiting={st['awaiting_field']!r}")
if st["awaiting_field"] != "confirm":
    fails.append("B: $500 should skip the code and ask for confirm")
st = turn(st, confirm=True)
print(f"   confirm            -> fired={len(calls)} otp={calls[-1].get('otp') if calls else '—'}")
if len(calls) != 1:
    fails.append("B: a confirmed $500 should complete")

# C) confirm $500, THEN bump to $5000: gate + confirm re-arm, and NO double-spend
calls.clear()
st = fresh()
st = turn(st, amount=500, from_account="checking", recipient="mom")
st = turn(st, confirm=True)                  # the $500 actually sends
print(f"C) $500 confirmed      -> fired={len(calls)}")
if len(calls) != 1:
    fails.append("C: $500 should fire once")
st = turn(st, amount=5000)                   # correction crosses the threshold
print(f"   bumped to $5000     -> awaiting={st['awaiting_field']!r} "
      f"confirm={st['fields'].get('confirm')} fired={len(calls)}")
if st["awaiting_field"] != "verification_code":
    fails.append("C: correction did not re-arm the 2FA gate")
if st["fields"].get("confirm") is not None:
    fails.append("C: stale confirmation survived the correction")
st = turn(st, confirm=True)                   # confirm again — but no code, and idempotent
if len(calls) != 1:
    fails.append(f"C: DOUBLE-SPEND — {len(calls)} transfers fired in one run")

# audit recorded the fires with the sensitive OTP redacted
otp_in_audit = audit.records[-1]["args"].get("otp") if audit.records else None
print(f"\naudit records={len(audit.records)}  otp_logged={otp_in_audit!r}")
if otp_in_audit not in (None, "***REDACTED***"):
    fails.append("audit did not redact the sensitive otp")

print("\n" + "=" * 60)
if fails:
    print("FAILED:")
    for f in fails:
        print("  -", f)
    raise SystemExit(1)
print("PASS — pure declarative skill, NO handlers.py:")
print("  • 2FA gate (a compute: expression) holds; tool args are type-bound")
print("  • a correction re-arms the gate AND the confirmation")
print("  • at-most-once per run (no double-spend); OTP redacted in the audit log")

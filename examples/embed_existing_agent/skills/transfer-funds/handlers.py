"""Transfer-funds flow.

Demonstrates aigenos flow determinism on a money-movement domain:

- typed fields cap the input space (amount, recipient, from_account enums)
- a Python policy gate decides whether 2FA is required, not the LLM
- the action stub is the only place a "transaction" happens, so audit logs
  attach cleanly to a single call site
- corrections invalidate downstream actions automatically — if the user
  bumps the amount past the 2FA threshold mid-flow, the verification step
  re-engages

No real bank API is called. ``submit_transfer`` returns a fake TXN-XXXX id.
"""
from __future__ import annotations

import secrets

from aigenos_flow import action, flow

TWO_FACTOR_THRESHOLD = 1000.0


@flow
def transfer_funds(ctx):
    ctx.ask("amount",       prompt="How much do you want to send? (USD)")
    ctx.ask("from_account", prompt="From checking or savings?")
    ctx.ask("recipient",    prompt="Who's it going to? (self, mom, landlord, electricity, internet, gym)")

    policy = ctx.run(check_policy)
    if policy["requires_2fa"]:
        ctx.say(
            f"For your safety, transfers of ${ctx.fields['amount']:.2f} or "
            "more need a one-time code. I've sent one to your phone."
        )
        ctx.ask(
            "verification_code",
            prompt="Enter the 6-digit code from your phone:",
        )

    ctx.say(
        f"📋 Send ${ctx.fields['amount']:.2f} from {ctx.fields['from_account']} "
        f"to {ctx.fields['recipient']}"
        + (f" — '{ctx.fields['memo']}'" if ctx.fields.get("memo") else "")
        + "."
    )
    ctx.ask("confirm", prompt="Confirm transfer? (yes/no)")

    if not ctx.fields["confirm"]:
        ctx.say("OK, cancelled — no money moved.")
        return

    result = ctx.run(submit_transfer)
    if result["ok"]:
        ctx.say(f"✅ Sent. Confirmation: {result['txn_id']}.")
    else:
        ctx.say(f"❌ Transfer failed: {result['error']}.")


# A read: no side effect, so it declares `readonly=True` and needs no gate.
# It re-runs automatically after a correction, which is what re-arms the 2FA
# threshold when the amount changes.
@action(readonly=True)
def check_policy(state):
    """Decide whether the transfer needs a 2FA step.

    Policy lives in code, not in the prompt — that's the whole point.
    """
    amount = state["fields"].get("amount", 0)
    return {
        "requires_2fa": amount >= TWO_FACTOR_THRESHOLD,
        "policy_version": "2026.05",
    }


# A WRITE: it names the boolean it is gated on. ctx.run re-checks that gate at
# dispatch, so the Python escape hatch is held to the same rule as a declarative
# `do:` step — and the flow above cannot reach this line with confirm false.
@action(requires="confirm")
def submit_transfer(state):
    """Stub for the real bank API. In production this would be an
    idempotent POST keyed on the flow's run_id."""
    f = state["fields"]
    if f.get("verification_code") and f["verification_code"].strip() == "000000":
        return {"ok": False, "txn_id": None, "error": "verification code rejected"}
    txn_id = f"TXN-{secrets.token_hex(3).upper()}"
    return {"ok": True, "txn_id": txn_id, "error": None}

"""Expense filing flow — the Python side of the file_expense skill.

The flow is a plain function. Every conditional, loop, or helper is normal
Python — aigenos flow doesn't reinvent control flow.

Pause-points are ``ctx.ask(...)``. The flow function gets re-executed from
the top on every user turn; already-filled fields return immediately, so
control resumes at the first unfilled one.
"""
from __future__ import annotations

from aigenos_flow import action, flow


@flow
def file_expense(ctx):
    ctx.ask("amount", prompt="How much was the expense? (USD)")
    ctx.ask("merchant", prompt="Where did you make the purchase?")
    ctx.ask(
        "category",
        prompt="What category does this fall under? "
        "(travel, food, software, office, other)",
    )

    policy = ctx.run(check_policy)
    if policy["requires_justification"]:
        ctx.ask(
            "justification",
            prompt="That's over policy — why is it needed? (one short sentence)",
        )

    ctx.say(
        f"Ready to submit ${ctx.fields['amount']:.2f} at "
        f"{ctx.fields['merchant']} ({ctx.fields['category']})."
    )
    ctx.ask("confirm", prompt="Submit it? (yes/no)")

    if not ctx.fields["confirm"]:
        ctx.say("OK — nothing was submitted.")
        return

    submission = ctx.run(submit_expense)
    ctx.say(f"✅ Submitted as #{submission['expense_id']}.")


# ---------------------------------------------------------------------------
# Actions. Every @action is a WRITE unless it declares `readonly=True`; a write
# names the boolean field that gates it with `requires=`. Results are memoized by
# name and re-run when a correction clears state.results — while a gated write
# stays at-most-once per flow run via the effects log.
# ---------------------------------------------------------------------------


# A read: no side effect, so `readonly=True` and no gate. It re-runs after a
# correction, which is how bumping the amount re-arms the justification step.
@action(readonly=True)
def check_policy(state):
    """Company policy: amount > 500 OR category == travel requires justification."""
    fields = state["fields"]
    amount = fields.get("amount", 0)
    category = fields.get("category", "")
    return {
        "requires_justification": amount > 500 or category == "travel",
        "policy_version": "2026.05",
    }


# A WRITE: it names the boolean it is gated on. ctx.run re-checks that gate at
# dispatch, so this cannot fire on a turn where the user has not said yes.
@action(requires="confirm")
def submit_expense(state):
    """Stub for the real API call. In production this would POST to your
    expense-management service."""
    fields = state["fields"]
    fake_id = f"EXP-{abs(hash(str(fields))) % 10000:04d}"
    return {"expense_id": fake_id}

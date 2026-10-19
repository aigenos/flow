"""Flow + action for transfer-funds.

The policy gates here are STRUCTURAL — they are ordinary Python control flow,
not instructions to a model. The `transfer_money` action is literally
unreachable until:
  (1) amount, recipient, and from_account are collected and validated,
  (2) a verification_code has been collected IF amount > $1000 (2FA gate),
  (3) the user has explicitly confirmed.
No phrasing, no "skip the code", no injection can make the transfer fire early,
because reaching that line requires passing every gate above it.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from instrumented_tools import _transfer_money  # noqa: E402

from aigenos_flow import flow, action  # noqa: E402

TWO_FA_THRESHOLD = 1000

_FIELDS = ("amount", "recipient", "from_account", "verification_code")


@action(requires="confirm")
def transfer_money(state):
    """The one write. `requires="confirm"` is re-checked by ctx.run at dispatch,
    so the Python escape hatch is held to the same rule a declarative `do:` step
    is — and it makes `confirm` a user-only field, so the model cannot answer
    its own confirmation question."""
    f = state["fields"]
    return _transfer_money({k: f.get(k) for k in _FIELDS})


@flow
def transfer_funds(ctx):
    ctx.ask("amount")
    ctx.ask("recipient")
    ctx.ask("from_account")

    # 2FA gate — deterministic, re-evaluated every turn (replay semantics), so a
    # mid-flow correction that pushes the amount over the threshold re-triggers
    # it automatically. The LLM cannot reason its way around this.
    if float(ctx.fields["amount"]) > TWO_FA_THRESHOLD:
        ctx.ask("verification_code")

    ctx.say(
        f"Confirm: send ${ctx.fields['amount']} to {ctx.fields['recipient']} "
        f"from {ctx.fields['from_account']}?"
    )
    ctx.ask("confirm")

    if ctx.fields["confirm"]:
        result = ctx.run(transfer_money)
        ctx.say(f"Done — transfer reference {result['id']}.")
    else:
        ctx.say("Okay, no money moved.")

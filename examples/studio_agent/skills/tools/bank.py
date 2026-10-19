"""A pretend bank, small enough to read in two minutes.

This file is a TOOL LIBRARY. It sits in the agent's ``tools/`` folder, so every
skill in the agent can name its functions (``do: send_payment``,
``when: quote_payment.needs_code``) and none of them has to import anything or
touch the code that runs the agent. A public function here is a tool; a name
starting with ``_`` is a helper.

Everything is in memory and nothing leaves the process: no account exists, no
money moves. What is real is the rule set the skills ask about:

* a US routing number is nine digits, starts with a Federal Reserve district
  prefix, and passes the ABA checksum;
* a payment cannot exceed the available balance;
* a payment of $1,000 or more needs a 6-digit code.

Two things in a signature matter to a skill. The types (``amount: float`` makes
the question a number box) and ``@readonly``: a function with that mark only
looks something up, so the flow may call it whenever it needs the answer. One
without it changes something, and no skill can run it until the user has said
yes to a ``confirm:``.

The skills share nothing else. ``add-payee`` saves a payee here and
``pay-someone`` lists payees from here: they meet in the system of record, the
way two screens of a banking app do.
"""
from __future__ import annotations

import hashlib
from typing import Any

from aigenos_flow import Secret, readonly

AVAILABLE = 2500.00            # what the account can spend
CODE_THRESHOLD = 1000.00       # at or above this, a code is required
#: The demo's code. A real system sends one and checks it against its own
#: record; the point here is that the FLOW decides when one is needed.
VALID_CODE = "481920"

#: The first two digits of a routing number name a Federal Reserve district:
#: 01 to 12 for banks, the same plus 20 for thrifts, plus 60 for electronic.
DISTRICTS = {
    1: "Boston", 2: "New York", 3: "Philadelphia", 4: "Cleveland", 5: "Richmond", 6: "Atlanta",
    7: "Chicago", 8: "St. Louis", 9: "Minneapolis", 10: "Kansas City", 11: "Dallas", 12: "San Francisco",
}

_SEED = (
    ("P-1001", "Mom", "021000021", "9900114821"),
    ("P-1002", "Landlord", "011000015", "5512907733"),
    ("P-1003", "City Gym", "121000358", "3008840196"),
)

#: Every call, in order, and every write. The proof reads both.
CALL_LOG: list[tuple[str, dict[str, Any]]] = []
LEDGER: list[dict[str, Any]] = []
_payees: list[dict[str, str]] = []


def _reset() -> None:
    CALL_LOG.clear()
    LEDGER.clear()
    _payees.clear()
    _payees.extend({"payee_id": pid, "name": name, "routing": routing, "account": account}
                   for pid, name, routing, account in _SEED)


def _log(name: str, **args: Any) -> None:
    CALL_LOG.append((name, args))


def _money(x: float) -> str:
    return f"${x:,.2f}"


def _masked(account: str) -> str:
    return "•••• " + account[-4:]


def _routing_checksum_ok(routing_number: str) -> bool:
    """The ABA check: 3(d1+d4+d7) + 7(d2+d5+d8) + (d3+d6+d9) is a multiple of 10."""
    if len(routing_number) != 9 or not routing_number.isdigit():
        return False
    d = [int(c) for c in routing_number]
    return (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 == 0


def _routing_district(routing_number: str) -> str | None:
    if not routing_number[:2].isdigit():
        return None
    prefix = int(routing_number[:2])
    return next((DISTRICTS[prefix - base] for base in (0, 20, 60) if 1 <= prefix - base <= 12), None)


# --------------------------------------------------------------------------- #
# Payees
# --------------------------------------------------------------------------- #
@readonly
def check_account(payee_name: str, routing_number: str, account_number: str) -> dict[str, Any]:
    """Could a bank have this routing number and account? Returns the card the user confirms."""
    _log("check_account", payee_name=payee_name, routing_number=routing_number, account_number=account_number)
    district = _routing_district(routing_number)
    return {
        "ok": _routing_checksum_ok(routing_number) and district is not None,
        "account_ok": account_number.isdigit() and 6 <= len(account_number) <= 12,
        "summary": {
            "payee": payee_name,
            "account": _masked(account_number),
            "routing_number": routing_number,
            "federal_reserve_district": district or "unknown",
        },
    }


def save_payee(payee_name: str, routing_number: str, account_number: str) -> dict[str, Any]:
    """Save a new payee."""
    _log("save_payee", payee_name=payee_name, routing_number=routing_number, account_number=account_number)
    payee_id = f"P-{1001 + len(_payees)}"
    _payees.append({"payee_id": payee_id, "name": payee_name, "routing": routing_number, "account": account_number})
    LEDGER.append({"kind": "payee", "payee_id": payee_id, "name": payee_name})
    return {"payee_id": payee_id, "name": payee_name}


@readonly
def list_payees() -> dict[str, Any]:
    """The payees already saved."""
    _log("list_payees")
    return {"payees": [{"payee_id": p["payee_id"], "name": p["name"], "account": _masked(p["account"])}
                       for p in _payees]}


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #
@readonly
def quote_payment(payee_id: str, amount: float) -> dict[str, Any]:
    """What a payment would be: is the payee known, is there enough money, does it need a code."""
    _log("quote_payment", payee_id=payee_id, amount=amount)
    payee = next((p for p in _payees if p["payee_id"] == payee_id), None)
    return {
        "known_payee": payee is not None,
        "within_balance": amount <= AVAILABLE,
        "needs_code": amount >= CODE_THRESHOLD,
        "summary": {
            "to": payee["name"] if payee else "",
            "account": _masked(payee["account"]) if payee else "",
            "amount": _money(amount),
            "available_balance": _money(AVAILABLE),
        },
    }


@readonly
def verify_code(code: Secret) -> dict[str, Any]:
    """Is this the code that was sent?"""
    _log("verify_code")  # the code itself is never logged
    return {"valid": code == VALID_CODE}


def send_payment(payee_id: str, amount: float) -> dict[str, Any]:
    """Send a payment to a saved payee."""
    _log("send_payment", payee_id=payee_id, amount=amount)
    reference = "PAY-" + hashlib.sha1(f"{payee_id}|{amount:.2f}".encode()).hexdigest()[:6].upper()
    LEDGER.append({"kind": "payment", "reference": reference, "payee_id": payee_id, "amount": round(amount, 2)})
    return {"reference": reference, "amount_text": _money(amount)}


_reset()

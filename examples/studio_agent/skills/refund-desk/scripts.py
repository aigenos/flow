"""The refund-desk skill's four tools, against a tiny in-memory order book.

They are the only Python in the skill: the order of the steps, the refund-window
rule, the manager-approval rule and the confirmation are the steps in SKILL.md,
where the loader can check them. Each function's parameters are the answers it
needs, passed by name. Three only look something up (`@readonly`); the fourth
issues the refund, so the skill cannot run it without the confirmation above it.
"""
from __future__ import annotations

from datetime import date, timedelta

from aigenos_flow import Secret, readonly

REFUND_WINDOW_DAYS = 30
APPROVAL_THRESHOLD = 200.00
#: The demo's current manager approval code. A real desk would verify this
#: against an identity provider; the point here is that the FLOW decides when
#: one is required, and the model cannot supply it.
MANAGER_CODE = "246810"

_TODAY = date.today()

#: order_id -> order. `days_ago` keeps the dates relative, so the demo never goes stale.
ORDERS: dict[str, dict] = {
    "ORD-1042": {"item": "Aurora ANC headphones", "total": 249.00, "days_ago": 12, "paid_with": "Visa ending 4242"},
    "ORD-1038": {"item": "USB-C travel dock", "total": 89.50, "days_ago": 20, "paid_with": "Visa ending 4242"},
    "ORD-1027": {"item": "Ergonomic desk lamp", "total": 64.00, "days_ago": 27, "paid_with": "Mastercard ending 1881"},
    "ORD-0991": {"item": "Mechanical keyboard", "total": 159.00, "days_ago": 61, "paid_with": "Visa ending 4242"},
}

#: What actually happened. Tests and the demo read this to see what fired.
REFUNDS: list[dict] = []


def _placed(order: dict) -> str:
    return (_TODAY - timedelta(days=order["days_ago"])).isoformat()


@readonly
def list_recent_orders() -> dict:
    """The customer's recent orders, for the list they pick from."""
    return {
        "orders": [
            {
                "order_id": order_id,
                "item": order["item"],
                "total": f"${order['total']:.2f}",
                "placed": _placed(order),
            }
            for order_id, order in ORDERS.items()
        ]
    }


@readonly
def lookup_order(order_id: str) -> dict:
    """Everything the flow's rules need to know about one order."""
    order = ORDERS.get(order_id)
    if order is None:
        return {"found": False, "refundable": False, "needs_approval": False}
    return {
        "found": True,
        "refundable": order["days_ago"] <= REFUND_WINDOW_DAYS,
        "needs_approval": order["total"] >= APPROVAL_THRESHOLD,
        "summary": {
            "Item": order["item"],
            "Total": f"${order['total']:.2f}",
            "Placed": f"{_placed(order)} ({order['days_ago']} days ago)",
            "Paid with": order["paid_with"],
        },
    }


@readonly
def verify_approval(manager_code: Secret) -> dict:
    """Is this the current manager approval code?"""
    return {"valid": manager_code == MANAGER_CODE}


def issue_refund(order_id: str, reason: str, manager_code: Secret = "") -> dict:
    """Refund the order to its original payment method."""
    order = ORDERS[order_id]
    refund = {
        "reference": f"RF-{len(REFUNDS) + 1:04d}",
        "order_id": order_id,
        "amount": f"{order['total']:.2f}",
        "method": order["paid_with"],
        "reason": reason,
        "approved_by_manager": bool(manager_code),
    }
    REFUNDS.append(refund)
    return refund

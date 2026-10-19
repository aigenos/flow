"""Tools for subscription-help. They sit beside the skill, so only it can name them.

Both only look things up (`@readonly`). Replace the sample data with calls to
your billing API; keep the row keys the designed widgets show (`id`, `service`,
`plan`, `monthly_price`, `status`).
"""
from __future__ import annotations

from aigenos_flow import readonly

CATALOG = [
    {"id": "netflix", "service": "Netflix", "plan": "Premium 4K", "monthly_price": "$22.99", "status": "active"},
    {"id": "disney_plus", "service": "Disney+", "plan": "Standard", "monthly_price": "$13.99", "status": "past due"},
    {"id": "spotify", "service": "Spotify", "plan": "Family", "monthly_price": "$16.99", "status": "active"},
]

DETAILS = {
    "netflix": {
        "service": "Netflix", "plan": "Premium 4K", "status": "active",
        "monthly_price": "$22.99", "pending_due": "$0.00",
        "next_billing": "2026-11-14", "member_since": "2021-03-02",
        "billing_history": [
            {"date": "2026-10-14", "amount": "$22.99", "status": "paid"},
            {"date": "2026-09-14", "amount": "$22.99", "status": "paid"},
        ],
        "events": [
            {"time": "2026-05-01", "title": "Plan upgraded", "description": "Standard to Premium 4K"},
            {"time": "2021-03-02", "title": "Subscribed"},
        ],
    },
    "disney_plus": {
        "service": "Disney+", "plan": "Standard", "status": "past due",
        "monthly_price": "$13.99", "pending_due": "$27.98",
        "next_billing": "2026-11-03", "member_since": "2023-11-20",
        "billing_history": [
            {"date": "2026-10-03", "amount": "$13.99", "status": "failed"},
            {"date": "2026-09-03", "amount": "$13.99", "status": "failed"},
            {"date": "2026-08-03", "amount": "$13.99", "status": "paid"},
        ],
        "events": [
            {"time": "2026-09-03", "title": "Payment failed", "description": "Card expired"},
            {"time": "2023-11-20", "title": "Subscribed"},
        ],
    },
    "spotify": {
        "service": "Spotify", "plan": "Family", "status": "active",
        "monthly_price": "$16.99", "pending_due": "$0.00",
        "next_billing": "2026-11-21", "member_since": "2020-06-11",
        "billing_history": [{"date": "2026-10-21", "amount": "$16.99", "status": "paid"}],
        "events": [{"time": "2020-06-11", "title": "Subscribed"}],
    },
}


@readonly
def list_subscriptions(holder: str) -> dict:
    """The streaming subscriptions on this holder's account."""
    return {"holder": holder, "subscriptions": [dict(row) for row in CATALOG]}


@readonly
def subscription_details(subscription_id: str, holder: str) -> dict:
    """Everything about one subscription: the plan, what is owed, the dates, the billing history."""
    found = DETAILS.get(subscription_id)
    if found is None:
        return {"found": False, "service": subscription_id}
    return {"found": True, "holder": holder, **found}

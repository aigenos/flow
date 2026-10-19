"""Tools for the subscription-insights skill (Anthropic `scripts/` layout).

Auto-registered for THIS skill only — a sibling skill cannot call these, so
`allowed-tools:` in SKILL.md stays the complete audit answer. Names here must
match the `tool:` names the skill's `actions:` reference.

Swap these mocks for your real REST client / MCP calls: auth headers live HERE
(server-side, env-read) — never in SKILL.md, never through the LLM. See
references/subscription-api.md for the shape contract and an httpx example.

Precedence: operator-managed connectors and tools injected via
`FlowAgent.from_dir(..., tools={...})` win on a name collision, so a test can
always substitute a double (see prove.py).
"""
from __future__ import annotations

CATALOG = [
    {"id": "netflix", "service": "Netflix", "plan": "Premium 4K", "monthly_price": "$22.99", "status": "active"},
    {"id": "prime_video", "service": "Prime Video", "plan": "Annual", "monthly_price": "$8.99", "status": "active"},
    {"id": "disney_plus", "service": "Disney+", "plan": "Standard", "monthly_price": "$13.99", "status": "past due"},
    {"id": "hulu", "service": "Hulu", "plan": "No Ads", "monthly_price": "$17.99", "status": "active"},
    {"id": "spotify", "service": "Spotify", "plan": "Family", "monthly_price": "$16.99", "status": "active"},
]

DETAILS = {
    "netflix": {
        "service": "Netflix", "plan": "Premium 4K", "status": "active",
        "monthly_price": "$22.99", "pending_due": "$0.00",
        "next_billing": "2026-08-14", "member_since": "2021-03-02",
        "billing_history": [
            {"date": "2026-07-14", "amount": "$22.99", "status": "paid"},
            {"date": "2026-06-14", "amount": "$22.99", "status": "paid"},
            {"date": "2026-05-14", "amount": "$22.99", "status": "paid"},
        ],
        "events": [
            {"time": "2026-05-01", "title": "Plan upgraded", "description": "Standard → Premium 4K"},
            {"time": "2021-03-02", "title": "Subscribed"},
        ],
    },
    "prime_video": {
        "service": "Prime Video", "plan": "Annual", "status": "active",
        "monthly_price": "$8.99", "pending_due": "$0.00",
        "next_billing": "2027-01-09", "member_since": "2019-01-09",
        "billing_history": [
            {"date": "2026-01-09", "amount": "$107.88", "status": "paid"},
        ],
        "events": [{"time": "2019-01-09", "title": "Subscribed"}],
    },
    "disney_plus": {
        "service": "Disney+", "plan": "Standard", "status": "past due",
        "monthly_price": "$13.99", "pending_due": "$27.98",
        "next_billing": "2026-08-03", "member_since": "2023-11-20",
        "billing_history": [
            {"date": "2026-07-03", "amount": "$13.99", "status": "failed"},
            {"date": "2026-06-03", "amount": "$13.99", "status": "failed"},
            {"date": "2026-05-03", "amount": "$13.99", "status": "paid"},
        ],
        "events": [
            {"time": "2026-06-03", "title": "Payment failed", "description": "Card expired"},
            {"time": "2023-11-20", "title": "Subscribed"},
        ],
    },
    "hulu": {
        "service": "Hulu", "plan": "No Ads", "status": "active",
        "monthly_price": "$17.99", "pending_due": "$0.00",
        "next_billing": "2026-08-21", "member_since": "2024-06-21",
        "billing_history": [{"date": "2026-07-21", "amount": "$17.99", "status": "paid"}],
        "events": [{"time": "2024-06-21", "title": "Subscribed"}],
    },
    "spotify": {
        "service": "Spotify", "plan": "Family", "status": "active",
        "monthly_price": "$16.99", "pending_due": "$0.00",
        "next_billing": "2026-08-08", "member_since": "2020-08-08",
        "billing_history": [{"date": "2026-07-08", "amount": "$16.99", "status": "paid"}],
        "events": [{"time": "2020-08-08", "title": "Subscribed"}],
    },
}


def list_subscriptions(args):
    """GET /subscriptions?first_name=… (mock)."""
    return {"subscriptions": CATALOG}


def get_subscription_details(args):
    """MCP get_subscription_details (mock)."""
    service = args.get("service")
    detail = DETAILS.get(service)
    if detail is None:
        return {"error": f"unknown subscription {service!r}"}
    return detail


TOOLS = {
    "list_subscriptions": list_subscriptions,
    "get_subscription_details": get_subscription_details,
}

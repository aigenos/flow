# Subscription API — tool contract

Loaded on demand (Anthropic level-3 progressive disclosure). This is the
**shape contract** the flow binds against. `scripts.py` is the adapter that
guarantees it; upstream services may change, this file may not — that is the
whole point of having an adapter.

## `list_subscriptions(args) -> dict`

| in | type | notes |
|---|---|---|
| `first_name` | string | Any name the user gave. Match broadly (first / last / full). |

```jsonc
{ "subscriptions": [
  { "id": "netflix",       // stable key; MUST match the subscription_id enum
    "service": "Netflix",  // display
    "plan": "Premium 4K",
    "monthly_price": "$22.99",
    "status": "active" }
]}
```

Bound by: `data: {rows: "{{list_subscriptions.subscriptions}}"}` on the
`subscription_grid` step. Extra keys are harmless — the grid shows the columns
its spec names, auto-widgets would show them all.

## `get_subscription_details(args) -> dict`

| in | type | notes |
|---|---|---|
| `service` | enum | Bound from validated `subscription_id`, never model-authored. |
| `first_name` | string | Pinned from state. |

```jsonc
{ "service": "Netflix", "plan": "Premium 4K", "status": "active",
  "monthly_price": "$22.99", "pending_due": "$0.00",
  "next_billing": "2026-08-14", "member_since": "2021-03-02",
  "billing_history": [ { "date": "…", "amount": "…", "status": "…" } ],
  "events":          [ { "time": "…", "title": "…", "description": "…" } ] }
```

## Changing the upstream shape

Bindings degrade, they don't crash: a renamed key blanks that slot in the
widget (cosmetic). Gates and action args are strict and fail closed. So when
upstream changes:

1. Fix the mapping **in `scripts.py`** — the flow and widgets stay untouched.
2. Re-run `prove.py`; the binding assertions catch a shape drift immediately.

## Auth

Credentials live in `scripts.py` (env-read at call time) — never in SKILL.md,
never in a widget, never through the LLM:

```python
import os, httpx
_H = {"Authorization": f"Bearer {os.environ['SUBS_API_TOKEN']}"}

def list_subscriptions(args):
    r = httpx.get(f"{os.environ['SUBS_API']}/subscriptions",
                  params={"q": args["first_name"]}, headers=_H, timeout=10)
    r.raise_for_status()
    return {"subscriptions": [
        {"id": s["code"], "service": s["displayName"],
         "plan": s["tier"], "monthly_price": s["price"],
         "status": s["state"]}            # ← adapter: upstream keys → contract
        for s in r.json()["items"]
    ]}
```

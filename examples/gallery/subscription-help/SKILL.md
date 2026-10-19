---
name: subscription-help
description: >-
  Look at one of the user's streaming subscriptions and answer questions about
  it. Use when the user asks about their subscriptions, a streaming service, a
  plan, a renewal date, a failed payment or what they owe.
license: Apache-2.0

# A list to pick from (a widget you designed), a widget that is only shown,
# then open questions answered with one lookup. Nothing here changes anything.
steps:
  - ask: { holder: "Whose account is it? (a first or last name)" }
    pattern: "^[A-Za-z .'-]{1,40}$"
  - ask: { subscription_id: "Which subscription?" }
    from: list_subscriptions.subscriptions
    widget: subscription_grid
  - say: "Here are the details."
    widget: subscription_details
    with:
      service: "{{subscription_details.service}}"
      plan: "{{subscription_details.plan}}"
      status: "{{subscription_details.status}}"
      monthly_price: "{{subscription_details.monthly_price}}"
      pending_due: "{{subscription_details.pending_due}}"
      next_billing: "{{subscription_details.next_billing}}"
      member_since: "{{subscription_details.member_since}}"
      billing: "{{subscription_details.billing_history}}"
      events: "{{subscription_details.events}}"
  - chat: "Ask me anything about this subscription: what you owe, the next bill, the plan, the history."
    tools: [subscription_details]
---

Help the user understand one of their subscriptions. The account holder and
the subscription are chosen in the steps above; after that, answer questions
using the lookup, which always reads that holder's chosen subscription.

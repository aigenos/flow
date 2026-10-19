---
name: subscription-insights
description: >-
  Show a user's streaming subscriptions (Netflix, Prime Video, Disney+, …),
  drill into one with rich widgets, then answer free questions about it in
  context. Use when the user asks about their subscriptions, a specific
  streaming service, dues, plans, or renewal dates.
# Two host tools (your REST endpoint / MCP server), granted via the registry.
allowed-tools: list_subscriptions, get_subscription_details
# The chat: leaf lets the model pick the tool and its args, so every tool it can
# reach must be declared read-only.
readonly-tools: get_subscription_details
fields:
  # user-typed → bound into a REST call, so it is constrained and must be
  # explicitly stated (never inferred).
  # NOTE the field NAME is an internal contract key — extraction is driven by
  # the description, so any name the user gives (first, last, or full) lands
  # here and the search tool matches broadly.
  first_name:
    type: string
    must_elicit: true
    pattern: '^[A-Za-z .-]{1,40}$'
    description: >-
      Any name the user gives for the account holder — first name, last name,
      or full name (e.g. "ava", "Smith", "Ava Smith"). Capture their words.
  # THE skip-ahead key. Filled EITHER by the grid's row submit (LLM-free) OR
  # extracted directly when the user names the service up front ("show my
  # netflix subscription") — in that case the list+grid steps skip themselves.
  subscription_id:
    type: enum
    values: [netflix, prime_video, disney_plus, hulu, spotify]
    description: >-
      Which subscription. Map phrasings: "netflix" → netflix, "prime"/"amazon"
      → prime_video, "disney"/"disney plus" → disney_plus, "hulu" → hulu,
      "spotify" → spotify. Only set when the user names a service or picks a
      row — never guess.
  subscription_name:
    type: string
    required: false
    pattern: "^[A-Za-z0-9 +.'-]{1,60}$"
    description: Display name of the selected subscription (filled by the grid).
  details_viewed:
    type: boolean
    required: false
    description: Set true when the user continues past the details view.

actions:
  list_subscriptions:                    # REST: GET /subscriptions?first_name=…
    tool: list_subscriptions
    readonly: true                       # a lookup: no gate, re-runs after a correction
    audit: false
    args:
      first_name: "{{first_name}}"
  get_subscription_details:              # MCP: full details for one subscription
    tool: get_subscription_details
    readonly: true
    audit: false
    args:
      service: "{{subscription_id}}"
      first_name: "{{first_name}}"

# The whole scenario as data. Steps 2–3 guard on `when_missing:` — if the user
# already named the service, the list call AND the grid never render and the
# flow fast-paths straight to the details widgets.
flow:
  - ask: first_name
  - when_missing: subscription_id
    do: list_subscriptions
  - when_missing: subscription_id
    widget: subscription_grid            # a2ui grid, single-select rows
    data:
      rows: "{{list_subscriptions.subscriptions}}"
    save: [subscription_id, subscription_name]
    prompt: Pick the subscription you want to inspect.
  - do: get_subscription_details
  - widget: subscription_details         # composed card + grid + timeline
    data:
      service: "{{get_subscription_details.service}}"
      plan: "{{get_subscription_details.plan}}"
      status: "{{get_subscription_details.status}}"
      monthly_price: "{{get_subscription_details.monthly_price}}"
      pending_due: "{{get_subscription_details.pending_due}}"
      next_billing: "{{get_subscription_details.next_billing}}"
      member_since: "{{get_subscription_details.member_since}}"
      billing: "{{get_subscription_details.billing_history}}"
      events: "{{get_subscription_details.events}}"
    save: [details_viewed]
    prompt: Here are the full details.
  - chat:
      tools: [get_subscription_details]
      pin:
        service: subscription_id
        first_name: first_name
      greeting: >-
        Ask me anything about this subscription — pending dues, start and end
        dates, plan details, billing history…
---

You help a user inspect their streaming subscriptions. Collect the first name,
then the listing, row selection, and detail rendering are all driven by the
declarative flow above — the widgets are fixed by id and always render the
same way. After the details, open free chat scoped to that subscription.

# Extraction rules

On EVERY user turn, scan the message for values for any unfilled field and emit
a `SetField(field=..., value=...)` for each one you can extract. If the user
names a streaming service ("my netflix sub", "disney plus"), set
`subscription_id` to the matching enum value — the flow will then skip the
listing grid entirely. If the user revises a filled field, emit
`Correct(field=..., value=NEW)` instead.

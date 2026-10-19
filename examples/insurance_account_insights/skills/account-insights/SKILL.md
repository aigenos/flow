---
name: account-insights
description: >-
  Find an insurance client by name, pick one of their accounts from a grid, and
  explore the account with natural questions (summary, payment history,
  coverage, claims). Use when the user wants to look up a client, an account,
  or ask anything about an account.
allowed-tools: search_clients, find_accounts, validate_account, account_summary, payment_history, insurance_coverage, claims, insurance_faq
# `assist:` and `chat:` hand tool choice AND argument choice to the model, so
# every tool reachable from them must be declared read-only. A write can only
# run from a `do:` step with `requires:`.
readonly-tools: account_summary, payment_history, insurance_coverage, claims, insurance_faq
fields:
  search_name:
    type: string
    must_elicit: true
    pattern: '^[A-Za-z .]{1,50}$'
    description: The client name (or part of it) to search for.
  client_id:
    type: string
    pattern: '^[A-Za-z0-9_-]{1,40}$'
    description: The selected client's id.
  client_full_name:
    type: string
    pattern: "^[A-Za-z .,'-]{1,100}$"
    description: The selected client's full name.
  account_number:
    type: string
    pattern: '^[A-Za-z0-9-]{1,40}$'
    description: The account number to explore.

# Mid-flow side questions ("what does auto insurance cover?") get a real
# answer from these tools, then the flow re-prompts its pending step — free
# conversation, deterministic return.
assist:
  tools: [insurance_faq]

# Reads (`readonly: true`, so no `requires:` gate): they re-run automatically
# after a correction, so switching client or account always fetches fresh data.
# Every action is a WRITE unless it says otherwise, and a write needs a gate.
actions:
  search_clients:
    tool: search_clients
    readonly: true
    audit: false
    args: { name: "{{search_name}}" }
  find_accounts:
    tool: find_accounts
    readonly: true
    audit: false
    args: { client_id: "{{client_id}}" }
  validate_account:
    tool: validate_account
    readonly: true
    audit: false
    args: { account_number: "{{account_number}}" }

flow:
  # ---- steps 1-2: find the account — SKIPPED ENTIRELY when the user gave one
  - ask: search_name
    when_missing: account_number
  - do: search_clients
    when_missing: account_number
  - pick: "{{search_clients.matches}}"          # grid of matching clients
    when_missing: account_number
    save: [client_id, client_full_name]
    prompt: "Select a client"
  - do: find_accounts
    when_missing: account_number
  - pick: "{{find_accounts.accounts}}"          # grid of that client's accounts
    save: [account_number]
    prompt: "Pick an account"

  # ---- the gate: however the account arrived, it must actually exist
  - do: validate_account
  - check: "validate_account.valid"
    else_say: "Account {{account_number}} doesn't exist. Give me another account number, or a client name to search."
    else_clear: [account_number]

  # ---- step 3: show the account, then open scoped Q&A
  - show: "{{validate_account.details}}"
    title: "Account {{account_number}}"
  - chat:
      tools: [account_summary, payment_history, insurance_coverage, claims]
      pin: { account_number: account_number }
      context: [validate_account, client_full_name]   # answers can use flow outputs
      greeting: "Ask me anything about this account — coverage, claims, payment history…"
---

You help the user find an insurance client and explore one of their accounts.
Collect a client name to search, or take an account number directly if the
user provides one. All lookups, the account-exists gate, the grids, and the
open Q&A are declared in the flow above; this body is only LLM context for
field extraction.

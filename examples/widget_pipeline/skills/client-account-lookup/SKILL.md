---
name: client-account-lookup
description: >-
  Look up a client by name, pick one of their accounts from a table, and run an
  action on the chosen account. Use when the user wants to find a client and act
  on one of their accounts.
allowed-tools: search_clients, find_accounts, account_action_mcp
fields:
  # user-typed search term -> bound into a REST call, so it must be constrained
  search_name:
    type: string
    must_elicit: true
    pattern: '^[A-Za-z .]{1,50}$'
    description: The client name to search for (e.g. "william").
  # filled by the client_picker widget's submit
  client_id:
    type: string
    pattern: '^[A-Za-z0-9_-]{1,40}$'
    description: The selected client's id.
  client_full_name:
    type: string
    pattern: "^[A-Za-z .,'-]{1,100}$"
    description: The selected client's full name.
  # filled by the account_table widget's submit
  account_number:
    type: string
    pattern: '^[A-Za-z0-9_-]{1,40}$'
    description: The selected account number.
  account_type:
    type: enum
    values: [checking, savings, credit, loan, brokerage]
    description: The selected account type.
  # The MCP call at the end of the pipeline is a WRITE, so it needs an explicit
  # confirmation to gate it. Every action is a write unless it says
  # `readonly: true`, and a write needs `requires:` plus a `when:` guard.
  confirm:
    type: boolean
    description: Explicit confirmation before the account action runs.

# Three host tools (your REST endpoints / MCP server), granted via the registry.
# The two lookups declare `readonly: true`; the MCP action is a write, so it is
# gated by `requires:` and deduped per flow run.
actions:
  search_clients:                       # REST: GET clients matching a name
    tool: search_clients
    readonly: true
    audit: false
    args:
      name: "{{search_name}}"
  find_accounts:                        # REST: GET accounts for a client
    tool: find_accounts
    readonly: true
    audit: false
    args:
      full_name: "{{client_full_name}}"
      client_id: "{{client_id}}"
  account_action_mcp:                   # MCP: act on the chosen account (a WRITE)
    tool: account_action_mcp
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args:
      client_id: "{{client_id}}"
      full_name: "{{client_full_name}}"
      account_number: "{{account_number}}"
      account_type: "{{account_type}}"

# The whole pipeline as data. Each `widget:` renders a DIFFERENT a2ui widget,
# populated from the immediately-preceding tool's result, and its submit fills
# the `save:` fields — which the next tool then binds. No Python.
flow:
  - ask: search_name                    # extracted from "show me clients named william"
  - do: search_clients                  # -> {matches: [{id, full_name}, ...]}
  - widget: client_picker               # a FORM with an autosuggest dropdown
    data:
      options: "{{search_clients.matches}}"
    save: [client_id, client_full_name]
  - do: find_accounts                   # -> {accounts: [{account_number, account_type, ...}]}
  - widget: account_table               # a DATATABLE / aggrid
    data:
      rows: "{{find_accounts.accounts}}"
    save: [account_number, account_type]
  - say: "Run the action on {{account_number}} ({{account_type}})?"
  - ask: confirm
  - when: "confirm"
    do: account_action_mcp               # call the MCP server with the chosen account
    then_say: "Done: the action ran on {{account_number}} ({{account_type}})."
---

You help a user find a client by name and act on one of their accounts. Collect
the search name, then the client search, account selection, and the final action
are all driven by the declarative flow above.

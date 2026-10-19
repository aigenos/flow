---
name: transfer-funds
description: >-
  Send money to a saved payee. Transfers of $1000 or more require a 2FA code.
  Use when the user wants to send, transfer, wire, or pay an amount.
# No handlers.py: this is a 100% declarative skill. The loader sees `flow:` and
# synthesizes the @flow. `allowed-tools` is the capability allowlist — an action
# tool outside this set is a LOAD ERROR.
allowed-tools: bank_transfer
fields:
  amount:
    type: number
    min: 0.01
    max: 25000                   # it feeds a gate below, so the loader makes it user-only
    description: Transfer amount in USD.
  from_account:
    type: enum
    values: [checking, savings]
    description: Source account.
  recipient:
    type: enum
    values: [self, mom, landlord, electricity, internet, gym]
    description: Saved payee.
  verification_code:
    type: string
    required: false
    pattern: '^[0-9]{6}$'         # constrained → safe to bind into a tool arg
    sensitive: true               # redacted from audit logs
    description: Six-digit 2FA code, required only for transfers of 1000 or more.
  confirm:
    type: boolean
    description: Explicit confirmation before money moves.

# (b1) PURE computed values — no code, no tool, no LLM. Recomputed every replay
# (so a correction that crosses the threshold re-arms the gate for free).
compute:
  requires_2fa: "amount >= 1000"

# (b2) SIDE-EFFECTING action — a NAMED host tool resolved at load. args bind only
# typed field/compute refs (never LLM-filled); `requires:` makes the confirm gate
# structural; idempotency_key keys the fire-once dedup.
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args:
      amount: "{{amount}}"
      source: "{{from_account}}"
      payee: "{{recipient}}"
      otp: "{{verification_code}}"

# The entire control flow, as data.
flow:
  - ask: amount
  - ask: from_account
  - ask: recipient
  - when: "requires_2fa"
    ask: verification_code
  - say: "Send {{amount}} to {{recipient}} from {{from_account}}?"
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
    then_say: "Sent {{amount}} to {{recipient}} from {{from_account}}."
---

You help the user transfer money. Collect the amount, source account, and
recipient, then confirm before sending. Transfers of $1000 or more require a 2FA
code. The control flow, the 2FA gate, and the side effect are all declared above;
this body is only LLM context for field extraction.

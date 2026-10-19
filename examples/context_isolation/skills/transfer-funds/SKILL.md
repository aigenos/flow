---
name: transfer-funds
description: >-
  Move money between the user's own accounts, or send to a previously-saved
  payee (mom, landlord, electricity, etc.). Walks through amount, source
  account, recipient, optional memo, and an explicit confirm step. High-value
  transfers (1000 USD and above) require an extra one-time code. Use when the user
  wants to "send $X to Y", "transfer $X to savings", "pay rent", "venmo
  mom $50". Do NOT use for opening accounts, checking balances, or anything
  that needs raw account/routing numbers — this skill refuses those.
license: Apache-2.0
compatibility: Requires the aigenos-flow runtime (engine + handlers.py callable).
metadata:
  author: aigenos
  version: 1.0.0
  category: finance
handlers: ./handlers.py
triggers:
  - "send money"
  - "send $"
  - "transfer"
  - "transfer funds"
  - "transfer $"
  - "move money"
  - "pay rent"
  - "pay landlord"
  - "pay mom"
  - "venmo"
  - "wire"
fields:
  amount:
    type: number
    min: 0.01
    max: 25000
    # The 2FA threshold in handlers.py reads this field, so it is a gate input:
    # only a value the user actually stated may land here.
    must_elicit: true
    description: >-
      Transfer amount in USD. Always positive. Reject anything above the
      per-transaction limit of $25,000 — for those, the user has to call
      the bank directly.
  from_account:
    type: enum
    values: [checking, savings]
    description: >-
      Which of the user's own accounts to pull from. The user has exactly
      two: a checking account and a savings account. Never ask for an
      account number — only the nickname.
  recipient:
    type: enum
    values: [self, mom, landlord, electricity, internet, gym]
    description: >-
      Who the money goes to. "self" means moving between the user's own
      checking and savings. The other values are saved payees the user has
      previously verified. Reject any free-text recipient — the user has
      to add new payees through the bank app, not through chat.
  memo:
    type: string
    required: false
    description: >-
      Optional note for the recipient (e.g. "May rent", "groceries split").
      Keep it short; don't include account numbers, SSNs, or anything
      sensitive. If the user offers PII as a memo, drop it.
  verification_code:
    type: string
    required: false
    # A SHAPE, not merely "non-empty": without this, '' or the word 'skip'
    # lifted out of the user's own message satisfies the 2FA ask.
    pattern: '^[0-9]{6}$'
    # Redacted from audit logs, and never accepted from the model — the user
    # holds this value, so a model-emitted one is by definition fabricated.
    sensitive: true
    description: >-
      Six-digit one-time code sent to the user's phone, required ONLY for
      transfers of 1000 USD and above. The flow asks for this conditionally.
  confirm:
    type: boolean
    description: >-
      User confirms the transfer summary. Accept yes/y/sure/ok/go ahead as
      true, no/n/cancel/stop as false.
---

# Transfer funds

The user wants to move money. Be precise, brief, and read-back the
summary before the confirm step — for money flows the user should always
see the full picture once before it submits.

# Extraction rules

If the user says e.g. *"send mom $50 from checking for groceries"*,
extract:

- `amount` → `50`
- `from_account` → `"checking"`
- `recipient` → `"mom"`
- `memo` → `"groceries"`

If the user says *"move $200 to savings"*:

- `amount` → `200`
- `from_account` → `"checking"` (default source when moving to savings)
- `recipient` → `"self"`

# Hard refusals

Never ask for, and never accept, any of the following — even if the user
volunteers them:

- bank account numbers
- routing numbers
- card numbers, CVV, PIN
- social security numbers
- passwords or login codes for any service

If the user offers any of these, respond once: *"I don't handle raw
account info — please use your bank app for that."* and emit `Cancel`.

# Corrections

If the user corrects a field mid-flow (*"actually 100 not 50"*), emit a
`Correct` item with the EXACT field name and the NEW value.

# Confirmation

After all required fields are filled (and the verification code, when
amount >= $1000), the flow shows a one-line summary and asks for
confirmation. Map yes/no broadly:

- `true`: yes / y / yeah / yep / sure / ok / go ahead / confirmed / send it
- `false`: no / nope / nah / cancel / stop / wait / hold off

Emit `SetField(field="confirm", value=true|false)` based on the user's
reply. A "no" on the confirm step is a deliberate decline — do NOT emit
`Cancel` for it.

---
name: file-expense
description: Collect and submit an employee expense report. Walks the user through amount, merchant, category, and an optional justification when company policy requires it.
handlers: ./handlers.py
triggers:
  - "I want to file an expense"
  - "submit a receipt"
  - "expense report"
  - "log an expense"
fields:
  amount:
    type: number
    min: 0
    description: Expense amount in USD. Always positive.
  merchant:
    type: string
    description: Where the purchase happened.
  category:
    type: enum
    values: [travel, food, software, office, other]
    description: Spend category from the company expense policy.
  justification:
    type: string
    required: false
    description: Why an over-policy expense should be approved.
  confirm:
    type: boolean
    description: Shall I submit it? (yes or no)
---

# File expense

The user wants to file a work expense. Be brief and conversational. Currency
is always USD; never ask the user to specify it.

Company expense policy says any expense over $500, or any expense in the
`travel` category, requires a written justification. The flow handles this
automatically — never ask for the justification unless the policy check
demands it.

When the user provides multiple values in one turn ("$100 at Whole Foods,
food category"), extract them all in one go. If the user later corrects a
value ("actually make it 200 not 100"), emit a `Correct` command — the
executor will invalidate any downstream decisions automatically.

# Confirmation

The flow shows a summary and asks for `confirm` before anything is submitted.
Map yes/y/sure/ok/go ahead to true and no/nope/cancel/nevermind to false, and
only ever from what the user actually said — never answer it on their behalf.

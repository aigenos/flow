---
name: pay-someone
description: >-
  Pay a payee who is ALREADY SAVED: pick who from the list, say how much, and
  confirm. Use only when the user says the payee is already saved or already
  set up. For a plain request to send or pay money, use send-money instead.
license: Apache-2.0

# Every name below that is not a question is a tool from tools/bank.py.
# `quote_payment` and `verify_code` only look things up, so they are not steps:
# the flow fetches them where their answer is first needed. `send_payment`
# changes something, so it cannot run until the user says yes to the confirm.
steps:
  # The list comes from the bank. A payee saved by add-payee a moment ago is in it.
  - ask: { payee_id: "Who are you paying? Pick them from the list." }
    from: list_payees.payees
    check: { quote_payment.known_payee: "That payee is not in your list. Pick one from the list." }

  - ask: { amount: "How much? (USD)" }
    check:
      amount >= 0.01: "Enter an amount above zero."
      quote_payment.within_balance: "That is more than your available balance. Enter a smaller amount."

  # A branch: this question exists only for larger payments. The bank says which.
  - ask: { code: "This payment is $1,000 or more, so it needs a 6-digit code. (Nothing is texted in this demo. The code is 481920.)" }
    when: quote_payment.needs_code
    check: { verify_code.valid: "That code is not valid." }

  # What the user is about to send, as a card built by the bank.
  - say: "{{quote_payment.summary}}"
    title: Payment
  - confirm: Send this payment?
  - do: send_payment
  - say: "Sent. Reference {{send_payment.reference}}."
---

You help the user pay a saved payee. The steps above decide what is asked,
which checks run and when money moves; this text is only context.

# Extraction rules

- `amount`: only a number the user actually stated.
- You cannot choose the payee, supply or waive the code, or confirm. The flow
  takes each of those from the user.

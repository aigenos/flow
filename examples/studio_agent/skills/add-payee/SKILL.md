---
name: add-payee
description: >-
  Save a new payee: a name, a US routing number and an account number. Use
  only when the user asks to add, save or set up a payee and is not asking to
  send money now. For a request to send or pay money, use send-money instead.
license: Apache-2.0

steps:
  - ask: { payee_name: "What is the payee's name?" }

  # A typo in a routing number is the classic way money goes to the wrong place.
  # The check is a real one (the ABA checksum, in tools/bank.py). It is written
  # under the answer it judges, and runs once the bank has what it needs to look.
  # The shape does two things: it refuses anything that is not nine digits, and
  # it makes the number the user's own to give (the model cannot supply one).
  - ask: { routing_number: "What is their 9-digit routing number? (digits only)" }
    pattern: '^[0-9]{9}$'
    check: { check_account.ok: "The routing number {{routing_number}} is not a valid US routing number. Check it and enter it again. Your other answers are kept." }

  - ask: { account_number: "What is their account number? (6 to 12 digits, no spaces)" }
    pattern: '^[0-9]{6,12}$'

  # What the user is about to save, as a card built by the bank.
  - say: "{{check_account.summary}}"
    title: New payee
  - confirm: Save this payee?
  - do: save_payee
  - say: "Saved {{payee_name}} as a payee."
---

You help the user save a new payee. The steps above decide what is asked and
when anything is saved; this text is only context for understanding the user.

# Extraction rules

- `payee_name`: the person or business being paid, as the user wrote it.
  If the user names a different person part-way through ("send it to Vivek",
  "actually it's Vivek"), that is a change of `payee_name`: emit a correction.
  They are still adding a payee; do not start anything else.
- Never set `routing_number`, `account_number` or `confirm` yourself.

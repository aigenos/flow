---
name: send-money
description: >-
  The front door for sending money. Use for any request to send money, transfer
  money, make a payment or pay a person ("I need to send some money", "pay my
  plumber"), unless the user has said the payee is already saved. It asks
  whether the payee is new and takes it from there.
license: Apache-2.0

# The whole route, and nothing here is a prompt:
#
#     someone new    ->  add-payee  ->  pay-someone  ->  done
#     a saved payee  ->                 pay-someone  ->  done
#
# `do: <another skill>` runs that skill and comes back to the next step. It
# starts with its own empty answers and its own confirm; this skill never sees
# them. If it was cancelled, or the user said no in it, this flow says `else:`
# and stops there, which is how it never goes on to pay a payee who was not saved.
steps:
  - ask: { pay_to: "Is this payment to someone new, or to a payee you have already saved?" }
    choices: [someone_new, a_saved_payee]   # a choice, not a yes/no: each answer names its branch

  # Each branch says which way it went: both end at the same payee list, so
  # without these lines they look alike in the chat.
  - when: pay_to == 'someone_new'
    steps:
      - say: They are not saved yet, so first add them as a payee.
      - do: add-payee
        else: No payee was added.
      - say: Now the payment. Pick them from your payees.
      - do: pay-someone
        else: No payment was made. The payee you added is still saved.

  - when: pay_to == 'a_saved_payee'
    steps:
      - say: Here are your saved payees.
      - do: pay-someone
        else: No payment was made.

  - say: That is everything. Anything else?
---

You are the front door for sending money. This skill does no banking itself:
it asks one question and hands the user to the skills that do the work
(`add-payee`, then `pay-someone`). The order is fixed in the `steps:` above.

# Extraction rules

Do not set `pay_to` from what the user says in passing ("my new landlord",
"mom again"). The flow asks the question and takes the answer from the user:
`someone_new` when they say the payee is new or not saved yet, `a_saved_payee`
when they say they have paid them before or the payee is already saved.

---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000 and above require a 2FA code.
steps:
  - ask: { amount: "How much do you want to send? (USD)" }
    check: { amount > 0: "Enter an amount above zero." }
  - ask: { payee: "Who is it going to?" }
    choices: [mom, landlord, gym]
  - ask: { code: "Enter the 6-digit code we texted you." }
    when: amount >= 1000            # the 2FA gate, re-evaluated on every replay
    type: secret                    # never logged, never supplied by the model
    pattern: '^[0-9]{6}$'           # a shape, so "skip" cannot satisfy the gate
  - say: "You're sending ${{amount}} to {{payee}}."
  - confirm: Send this transfer?
  - do: bank_transfer               # changes something: unreachable until the yes
  - say: "Sent ${{amount}} to {{payee}}."
---
Help the user transfer money. Collect the amount and the payee.
(The body stays — it's now LLM context for extraction and tone. The 2FA rule
above no longer lives here, because prose can't enforce anything.)

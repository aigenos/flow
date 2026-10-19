# 100% declarative skill — a runnable proof

One `SKILL.md`, **no `handlers.py`**. The control flow, the 2FA policy gate, and
the side effect are all declared as data. The only Python is the host tool the
operator grants (`bank_transfer`) — shared across skills, never per-skill control
flow.

```bash
python examples/declarative_flow/prove.py
```

**No API key, no network, no LLM.** The model's outputs are scripted through
`TestHost`, so the run is deterministic and instant. Expected output ends with:

```
audit records=3  otp_logged=None

============================================================
PASS — pure declarative skill, NO handlers.py:
  • 2FA gate (a compute: expression) holds; tool args are type-bound
  • a correction re-arms the gate AND the confirmation
  • at-most-once per run (no double-spend); OTP redacted in the audit log
```

Exit code is `0` on pass and `1` on any failed check, so it works as a CI guard.

## What it asserts

| Scenario | Assertion |
|---|---|
| **A** `$5000` | The `requires_2fa` compute engages: the flow stops on `verification_code`, and forcing `confirm=True` without a code fires **nothing** and is not kept. With the code, and then a yes at the confirmation, exactly one transfer fires and its args are type-bound (`amount` is a float, `otp` is the 6-digit string). |
| **B** `$500` | Under the threshold: no code step, straight to the confirmation. `otp` binds to `None` — an unset optional field, not a crash. |
| **C** `$500` → `$5000` | A correction after the $500 already sent re-arms **both** gates (the code is demanded again and the stale `confirm` is cleared), and re-confirming does **not** fire a second transfer. |
| audit | The fired call is recorded, with the `sensitive: true` verification code redacted. |

## The interesting part of the SKILL.md

```yaml
compute:
  requires_2fa: "amount >= 1000"          # pure, recomputed every replay

actions:
  submit_transfer:
    tool: bank_transfer                   # must be in `allowed-tools` AND granted
    requires: confirm                     # the gate, structurally
    idempotency_key: "{{flow_run_id}}"    # fire-once dedup
    args:
      amount: "{{amount}}"                # typed field bindings, never LLM text
      otp: "{{verification_code}}"

flow:
  - ask: amount
  - when: "requires_2fa"
    ask: verification_code
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
```

`amount` feeds a gate, so the loader makes it user-only by itself: a value the
model proposes for it is dropped before it reaches state. So is one for
`confirm`, like every boolean an action names in `requires:`. Neither is marked
in the file.

See [`../before_after/`](../before_after) for the same policy written as prompt
text, and what a hostile input does to it.

# Insurance account insights — search, pick, validate, then open Q&A

One `SKILL.md`, **no `handlers.py`**, **no widget JSON**. A client search, two
auto-generated grids, an account-exists gate, a rendered account card, and then
free conversation scoped to that one account — all declared as data.

```bash
python examples/insurance_account_insights/prove.py
```

**No API key, no network, no LLM.** Every model turn is scripted through
`TestHost`, so the run is deterministic and instant. It prints a `PASS` line per
assertion and ends with:

```
RESULT: ALL CHECKS PASSED — search grid -> account grid -> validated account -> pinned agentic Q&A, plus fast path, rejection, and switch.
```

Exit code is `0` on pass and non-zero on any failed check, so it works as a CI
guard.

## What it proves

| | Scenario |
|---|---|
| **A** | Full path: search "william" → client grid → account grid → validate → account card → chat greeting. Both grids are auto-generated `datatable`s bound to the previous tool's result. |
| **B** | Chat Q&A: the model picks the tool, but the **account number is pinned** from validated state, not from the model's arguments. Dated rows auto-render as a timeline. |
| **C** | Account switch: a correction re-runs validation **fresh** instead of reusing a stale memo, re-renders the card, and re-pins the new account for chat. |
| **D** | Fast path: "show me account ACC-9999" skips the search entirely (`when_missing:`), validation **rejects** it, the field is cleared, and the user recovers by giving a real one. |
| **E** | Pin override: a tool call the model aims at a *different* account is forced back to the validated one. |
| **F** | Mid-flow side question ("what does auto insurance cover?") is answered for real by `assist:`, then the flow re-prompts the same pending step — no derailment. |
| **G** | Post-gate chat is grounded in the workflow's own outputs via `context:`, so it answers without a tool call. |

## The shape of the skill

Every lookup declares `readonly: true` — that is why they re-run after a
correction instead of serving a stale memo, and why no confirmation gate is
needed for them. Anything that wrote would need `requires:` naming a boolean and
a `when:` guard proving it.

`chat:` and `assist:` hand tool choice *and* argument choice to the model, so
every tool they can reach is listed in `readonly-tools:`. A write is unreachable
from a conversational leaf by construction:

```yaml
allowed-tools: search_clients, find_accounts, validate_account, account_summary, …
readonly-tools: account_summary, payment_history, insurance_coverage, claims, insurance_faq

flow:
  - do: validate_account
  - check: "validate_account.valid"
    else_say: "Account {{account_number}} doesn't exist. …"
    else_clear: [account_number]
  - show: "{{validate_account.details}}"
  - chat:
      tools: [account_summary, payment_history, insurance_coverage, claims]
      pin: { account_number: account_number }   # the model cannot aim elsewhere
```

The mock backends (two REST endpoints, a validator and four MCP tools) live at the
top of `prove.py`, so the example runs from a clean clone with nothing else set up.

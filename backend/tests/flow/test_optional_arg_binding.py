"""An optional field the flow never asked for must not poison the tool call.

Regression for the audit's wrapper-2 (blocker): every front door bound an
unfilled optional field to None and passed it into the tool, so the ORDINARY
sub-threshold path — the one the README's 30-second example shows first —
died with a pydantic ValidationError while the gated path worked. The arg is
now omitted so the tool's own default applies.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.tools import tool

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

SKILL = """---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000+ require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:            { type: number, min: 0.01, must_elicit: true }
  recipient:         { type: enum, values: [mom, landlord] }
  verification_code: { type: string, required: false, pattern: '^[0-9]{6}$', sensitive: true }
  confirm:           { type: boolean }
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
flow:
  - ask: amount
  - ask: recipient
  - when: "amount >= 1000"
    ask: verification_code
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Help the user transfer money. Collect the amount and the payee.
"""


@pytest.fixture()
def agent_and_log(tmp_path: Path):
    """A skill whose tool validates its args the way a real LangChain tool does."""
    (tmp_path / "transfer-funds").mkdir()
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    fired: list[dict] = []

    @tool
    def bank_transfer(amount: float, payee: str, otp: str = "") -> dict:
        """Move money to a saved payee. otp is required above $1000."""
        fired.append({"amount": amount, "payee": payee, "otp": otp})
        return {"txn": "TXN-1"}

    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host, tools={"bank_transfer": bank_transfer})
    return agent, host, fired


def test_sub_threshold_transfer_completes_without_the_optional_code(agent_and_log):
    agent, host, fired = agent_and_log
    host.queue_commands(
        "send $500 to mom",
        SetField(field="amount", value=500),
        SetField(field="recipient", value="mom"),
    )

    chat = agent.session("transfer-funds").user("send $500 to mom")
    assert chat.awaiting == "confirm"          # under $1000: no 2FA step
    assert fired == []

    chat.submit(confirm=True)
    assert chat.done
    assert len(fired) == 1
    # The unasked field is absent, so the tool's own default applies — it is
    # never handed a None that its signature forbids.
    assert fired[0] == {"amount": 500.0, "payee": "mom", "otp": ""}


def test_gated_transfer_still_requires_and_passes_the_code(agent_and_log):
    agent, host, fired = agent_and_log
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )

    chat = agent.session("transfer-funds").user("send $5000 to mom")
    assert chat.awaiting == "verification_code"
    assert fired == []

    chat.submit(confirm=True)                  # forcing confirm cannot skip the gate
    assert chat.awaiting == "verification_code"
    assert fired == []

    chat.submit(verification_code="481920")
    assert chat.awaiting == "confirm" and fired == []   # the forced yes was not kept
    chat.submit(confirm=True)
    assert chat.done
    assert len(fired) == 1
    assert fired[0]["otp"] == "481920"

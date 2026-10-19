"""FlowSession — the conversation-shaped wrapper over FlowAgent.turn()."""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

SKILL = """\
---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000+ require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:    { type: number, min: 0.01, must_elicit: true }
  recipient: { type: enum, values: [mom, landlord, gym] }
  verification_code: { type: string, required: false, pattern: '^[0-9]{6}$', sensitive: true }
  confirm:   { type: boolean }
compute:
  requires_2fa: "amount >= 1000"
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    idempotency_key: "{{flow_run_id}}"
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
flow:
  - ask: amount
  - ask: recipient
  - when: "requires_2fa"
    ask: verification_code
  - say: "Send {{amount}} to {{recipient}}?"
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Collect amount and payee, then confirm.
"""


@pytest.fixture()
def harness(tmp_path: Path):
    (tmp_path / "transfer-funds").mkdir()
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    fired: list[dict] = []

    def bank_transfer(args: dict) -> dict:
        fired.append(dict(args))
        return {"txn": f"TXN-{len(fired)}"}

    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host, tools={"bank_transfer": bank_transfer})
    return agent, host, fired


def test_session_stops_at_gate_and_completes(harness):
    agent, host, fired = harness
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )

    chat = agent.session("transfer-funds").user("send $5000 to mom")
    assert chat.awaiting == "verification_code"
    assert chat.spec is not None and chat.spec.sensitive
    assert fired == []

    chat.submit(verification_code="481920")
    assert chat.awaiting == "confirm"
    assert any("5000" in r for r in chat.replies)  # the say: line surfaced

    chat.submit(confirm=True)
    assert chat.done
    assert len(fired) == 1 and fired[0]["otp"] == "481920"

    # duplicate confirm cannot double-fire (idempotent effects)
    chat.submit(confirm=True)
    assert len(fired) == 1


def test_session_forced_confirm_cannot_jump_the_gate(harness):
    """An out-of-order confirm=True opens nothing and is not kept: a yes
    counts for the question on screen. The code step still comes first, and the
    confirmation is then asked for real."""
    agent, host, fired = harness
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="recipient", value="mom"),
    )

    chat = agent.session("transfer-funds").user("send $5000 to mom")
    chat.submit(confirm=True)                  # forged/out-of-order submit
    assert chat.awaiting == "verification_code"
    assert fired == []                         # the gate held

    chat.submit(verification_code="481920")
    assert chat.awaiting == "confirm" and fired == []   # the forged yes is gone
    chat.submit(confirm=True)
    assert chat.done
    assert len(fired) == 1 and fired[0]["otp"] == "481920"


def test_session_correction_rearms_gate(harness):
    agent, host, fired = harness
    host.queue_commands(
        "send $500 to mom",
        SetField(field="amount", value=500),
        SetField(field="recipient", value="mom"),
    )

    chat = agent.session("transfer-funds").user("send $500 to mom")
    assert chat.awaiting == "confirm"          # $500 < $1000: no 2FA step

    chat.submit(amount=5000)                   # widget-level correction
    assert chat.awaiting == "verification_code"
    assert fired == []


def test_session_unknown_flow_fails_fast(harness):
    agent, _, _ = harness
    with pytest.raises(KeyError):
        agent.session("no-such-skill")

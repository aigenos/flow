"""`then_say:` on a `do:` step — the user is told the outcome, once, when the
action actually fires.

Before this, nothing reported an action's result: a `say:` cannot read one, and
an `@action` cannot speak. A flow that ended on its write therefore ended in
silence (or on whatever the model happened to say), and a generated skill had no
way to hand back a booking reference.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost

SKILL = """---
name: transfer-funds
description: Send money to a saved payee, with a receipt at the end.
allowed-tools: bank_transfer
fields:
  amount:    { type: number, min: 0.01, description: "How much?" }
  recipient: { type: enum, values: [mom, landlord], description: "To whom?" }
  confirm:   { type: boolean, description: "Send it?" }
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    args: { amount: "{{amount}}", payee: "{{recipient}}" }
flow:
  - ask: amount
  - ask: recipient
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
    then_say: "Sent ${{amount}} to {{recipient}}. Reference: {{submit_transfer.txn}}."
---
Help the user transfer money.
"""


def _agent(tmp_path: Path, skill_text: str = SKILL):
    (tmp_path / "transfer-funds").mkdir(exist_ok=True)
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(skill_text, encoding="utf-8")
    fired: list[dict] = []

    def bank_transfer(args: dict) -> dict:
        fired.append(dict(args))
        return {"txn": f"TXN-{len(fired)}", "status": "ok"}

    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host, tools={"bank_transfer": bank_transfer})
    return agent, host, fired


def test_the_outcome_is_reported_with_the_actions_own_result(tmp_path: Path):
    agent, _host, fired = _agent(tmp_path)
    chat = agent.session("transfer-funds").submit(amount=250, recipient="mom")
    assert chat.awaiting == "confirm"

    chat.submit(confirm=True)

    assert chat.done and len(fired) == 1
    assert "Sent $250 to mom. Reference: TXN-1." in [str(r) for r in chat.replies]


def test_it_is_said_once_not_on_every_replay(tmp_path: Path):
    """The flow replays from the top each turn; the receipt must not."""
    agent, host, fired = _agent(tmp_path)
    chat = agent.session("transfer-funds").submit(amount=250, recipient="mom")
    chat.submit(confirm=True)
    host.queue_commands("thanks")

    chat.user("thanks")

    assert len(fired) == 1
    assert not any("Reference:" in str(r) for r in chat.replies), chat.replies


def test_nothing_is_reported_when_the_user_declines(tmp_path: Path):
    agent, _host, fired = _agent(tmp_path)
    chat = agent.session("transfer-funds").submit(amount=250, recipient="mom")

    chat.submit(confirm=False)

    said = " | ".join(str(r) for r in chat.replies)
    assert fired == [] and "Reference:" not in said and "Sent" not in said
    assert "no changes were made" in said


def test_a_missing_result_key_renders_blank_instead_of_failing_a_completed_write(tmp_path: Path):
    text = SKILL.replace("{{submit_transfer.txn}}", "{{submit_transfer.no_such_key}}")
    agent, _host, fired = _agent(tmp_path, text)
    chat = agent.session("transfer-funds").submit(amount=250, recipient="mom")

    chat.submit(confirm=True)

    assert chat.done and len(fired) == 1, "the transfer happened; the message must not undo that"
    assert "Sent $250 to mom. Reference: ." in [str(r) for r in chat.replies]


@pytest.mark.parametrize(
    ("bad_line", "fragment"),
    [
        ('then_say: "Sent. Ref {{nope.txn}}."', "references undeclared 'nope'"),
        ('then_say: ""', "non-empty sentence"),
    ],
)
def test_the_loader_checks_then_say(tmp_path: Path, bad_line: str, fragment: str):
    text = SKILL.replace(
        'then_say: "Sent ${{amount}} to {{recipient}}. Reference: {{submit_transfer.txn}}."',
        bad_line,
    )
    with pytest.raises(ValueError) as err:
        _agent(tmp_path, text)
    assert fragment in str(err.value)


def test_then_say_is_only_valid_on_an_action_step(tmp_path: Path):
    text = SKILL.replace("  - ask: confirm\n", '  - ask: confirm\n    then_say: "nope"\n')
    with pytest.raises(ValueError) as err:
        _agent(tmp_path, text)
    assert "then_say" in str(err.value)

"""The README's front-door skill must load and enforce its gate for real.

The first ```yaml block in the repo README is the first thing a visitor reads.
This test extracts it verbatim, runs it through the real loader with a
TestHost, and drives the gate — so the README can never show a skill that
doesn't work.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.declarative import AuditSink
from aigenos_flow.hosts.test_host import TestHost

README = Path(__file__).resolve().parents[2] / "README.md"


@pytest.mark.skipif(not README.exists(), reason="repo README not present (packaged install)")
def test_readme_front_door_skill_loads_and_gates(tmp_path: Path):
    text = README.read_text(encoding="utf-8")
    block = next(b for b in re.findall(r"```yaml\n(.*?)```", text, re.S)
                 if "\nsteps:" in b and "name: transfer-funds" in b)
    block = "\n".join(line for line in block.splitlines() if not line.startswith("# skills/")) + "\n"
    (tmp_path / "transfer-funds").mkdir()
    (tmp_path / "transfer-funds" / "SKILL.md").write_text(block, encoding="utf-8")

    fired: list[dict] = []

    def bank_transfer(args: dict) -> dict:
        fired.append(dict(args))
        return {"txn": "TXN-1"}

    audit = AuditSink()
    host = TestHost()
    agent = FlowAgent.from_dir(
        tmp_path, host=host, tools={"bank_transfer": bank_transfer}, audit=audit
    )
    host.queue_commands(
        "send $5000 to mom",
        SetField(field="amount", value=5000),
        SetField(field="payee", value="mom"),
    )

    chat = agent.session("transfer-funds").user("send $5000 to mom")
    assert chat.awaiting == "code"                    # the gate armed at $5000
    assert fired == []

    chat.submit(confirm=True)                         # forcing confirm can't skip it
    assert chat.awaiting == "code"
    assert fired == []

    chat.submit(code="481920")
    # The forced yes was not kept: a confirmation counts for the question on
    # screen, and "Send this transfer?" was not on screen then.
    assert chat.awaiting == "confirm" and fired == []
    chat.submit(confirm=True)
    assert chat.done and len(fired) == 1 and fired[0]["code"] == "481920"
    # The whole skill is steps: no fields, no actions, no Python of its own.
    assert "fields:" not in block and "actions:" not in block and "flow:" not in block

    # The README promises "Sensitive fields are redacted from the audit log" and
    # an at-most-once write. The snippet must earn both, or the 30-second
    # copy-paste is less safe than the vetted example beside it.
    assert agent.registry.get("transfer-funds").fields["code"].sensitive, (
        "README front door must make the code a secret (`type: secret`)"
    )
    rec = next(r for r in audit.records if r.get("tool") == "bank_transfer")
    assert rec["args"]["code"] == "***REDACTED***", (
        f"the one-time code leaked into the audit log: {rec['args']}"
    )
    assert rec.get("idempotency_key") == rec.get("flow_run_id"), (
        "README front door must key the write to the flow run"
    )

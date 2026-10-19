"""The README's front door is run, not trusted.

The page prints a skill and a tools file, quotes the procedure generated from
them, shows a step for someone else's yes, and ends on a test. Each is taken
from the page as printed and put through the engine.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, procedure
from aigenos_flow.approvals import ApprovalStore
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.skills import load_skills_dir

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "README.md"
TRIP = REPO / "examples" / "studio_agent" / "skills" / "conference-trip" / "SKILL.md"

pytestmark = pytest.mark.skipif(not TRIP.is_file(), reason="repo README and examples not present (packaged install)")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture
def project(page, tmp_path, monkeypatch):
    skill = re.search(r"```yaml\n# skills/transfer-funds/SKILL.md\n(.*?)\n```", page, re.S).group(1)
    tools = re.search(r"```python\n# skills/tools/bank.py\n(.*?)\n```", page, re.S).group(1)
    (tmp_path / "skills" / "transfer-funds").mkdir(parents=True)
    (tmp_path / "skills" / "tools").mkdir()
    (tmp_path / "skills" / "transfer-funds" / "SKILL.md").write_text(skill + "\n", encoding="utf-8")
    (tmp_path / "skills" / "tools" / "bank.py").write_text(tools + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _section(page: str, heading: str) -> str:
    return page.split(heading, 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]


def test_the_procedure_the_readme_quotes_is_the_one_the_engine_prints(page, project):
    printed = procedure.for_skill(load_skills_dir(project / "skills", strict=True), "transfer-funds")["markdown"]
    quoted = [line[1:].strip() for line in _section(page, "### Read it back").splitlines() if line.startswith(">")]
    quoted = [line for line in quoted if line]
    assert len(quoted) >= 10, "the README quotes the ways through the flow and what must be true before the write"
    for line in quoted:
        assert line in printed, line
    assert "| 1 | yes | amount, payee, code | 1 | bank_transfer |" in quoted
    assert "- Input `code`: the answer at step 3 — only the user." in quoted


def test_someone_elses_yes_as_the_readme_writes_it(page, project):
    step = re.search(r"```yaml\n(  - confirm: [^\n]*\n    by: [^\n]*\n    when: [^\n]*)\n```",
                     _section(page, "### A yes from someone else")).group(1)
    path = project / "skills" / "transfer-funds" / "SKILL.md"
    whole = path.read_text(encoding="utf-8")
    assert whole.count("  - do: bank_transfer") == 1
    path.write_text(whole.replace("  - do: bank_transfer", step + "\n  - do: bank_transfer"), encoding="utf-8")

    host = TestHost()
    host.approvals = ApprovalStore()
    agent = FlowAgent.from_dir(project / "skills", host=host)
    host.queue_commands("hello")
    chat = agent.session("transfer-funds").user("hello")
    chat.submit(amount=20000).submit(payee="mom").submit(code="481920").submit(confirm=True)
    assert chat.approval.who == "compliance" and not chat.done, "the flow waits for compliance"
    chat.submit(confirm_2=True)
    assert not chat.done, "a yes sent through the conversation is refused"
    request = chat.approval.id
    chat.submit(amount=30000)
    assert host.approvals.get(request).status == "withdrawn", "the amount changed: that request no longer stands"


def test_the_readmes_own_test_passes_as_printed(page, project):
    block = re.search(r"```python\n(.*?)\n```", _section(page, "## Testing without an API key"), re.S).group(1)
    assert 'FlowAgent.from_dir("skills/", host=host)' in block
    scope: dict = {}
    exec(compile(block, "README.md", "exec"), scope)        # the asserts in it are the test
    assert scope["chat"].done


def test_the_trip_is_as_short_as_the_readme_says(page):
    steps = TRIP.read_text(encoding="utf-8").split("\nsteps:\n", 1)[1].split("\n---", 1)[0]
    lines = 1 + sum(1 for line in steps.splitlines() if line.strip() and not line.strip().startswith("#"))
    assert f"one skill file ({lines} lines of steps), twelve tools" in " ".join(page.split())

"""docs/developer-guide.md is run, not trusted: the snippets it prints load and behave as the page says."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.skills import load_skills_dir

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "docs" / "developer-guide.md"

pytestmark = pytest.mark.skipif(not PAGE.is_file(), reason="docs/ not present (packaged install)")

DESK = """
REFUNDS, TICKETS = [], []


def issue_refund(order_id: str) -> dict:
    REFUNDS.append(order_id)
    return {"reference": "RF-1"}


def open_ticket(topic: str) -> dict:
    TICKETS.append(topic)
    return {"reference": "TK-1"}
"""


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


def _blocks(page: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)\n```", page, re.S)


def _skill(tmp_path: Path, name: str, steps: str, tools: str, tool_file: str = "desk.py") -> Path:
    (tmp_path / name).mkdir(parents=True)
    (tmp_path / "tools").mkdir(exist_ok=True)
    (tmp_path / "tools" / tool_file).write_text(tools, encoding="utf-8")
    (tmp_path / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: An example from the developer guide. Use when testing it.\n{steps}\n---\nHelp.\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_branch_inside_one_skill_loads_and_takes_one_side(page, tmp_path):
    steps = next(b for b in _blocks(page, "yaml") if "wants == 'refund'" in b)
    root = _skill(tmp_path, "help-desk", steps, DESK)
    host = TestHost()
    agent = FlowAgent.from_dir(root, host=host)
    skill = agent.registry.get("help-desk")
    assert skill.fields["wants"].must_elicit, "it decides which tool runs, so only the user can answer it"

    host.queue_commands("hello")
    chat = agent.session("help-desk").user("hello")
    chat.submit(wants="ticket").submit(topic="printer")
    assert chat.awaiting == "confirm_2", "the ticket branch asks its own yes; the refund branch was never entered"
    chat.submit(confirm_2=True)
    assert chat.done and chat.replies[-1] == "Done. Anything else?"


def test_the_tools_file_the_page_prints_is_a_library(page, tmp_path):
    source = next(b for b in _blocks(page, "python") if b.startswith("# skills/tools/payments.py"))
    steps = (
        "steps:\n"
        "  - ask: { payee_id: \"Who is it going to?\" }\n"
        "  - ask: { amount: \"How much?\" }\n"
        "  - ask: { code: \"Enter the code we texted you.\" }\n"
        "    when: quote.needs_code\n"
        "  - confirm: Send it?\n"
        "  - do: send_payment\n"
        "  - say: \"Sent. Reference {{send_payment.reference}}.\""
    )
    registry = load_skills_dir(_skill(tmp_path, "pay", steps, source, "payments.py"), strict=True)
    skill = registry.get("pay")
    assert skill.fields["amount"].type == "number" and skill.fields["code"].sensitive, "types come from the signatures"
    assert skill.readonly_tools == "quote", "@readonly is the tool's own statement"


def test_the_entry_point_signatures_are_the_real_ones(page):
    import inspect

    from aigenos_flow import create_agent, wrap_agent

    for fn in (create_agent, wrap_agent, FlowAgent.from_dir):
        assert "tools_dir" in inspect.signature(fn).parameters
    assert page.count("tools_dir=None") == 3


def test_every_route_the_page_lists_exists(page):
    """The REST table names endpoints. Each one is a route on the router."""
    pytest.importorskip("fastapi")
    source = (REPO / "backend" / "aigenos_flow" / "integrations" / "flow.py").read_text(encoding="utf-8")
    for route in ("/tools", "/tools/files", "/tools/{name}/run", "/approvals", "/approvals/{request_id}",
                  "/approvals/{request_id}/status", "/skills/check", "/skills/{name}/procedure", "/skills"):
        assert f'("{route}"' in source, route
    for shown in ("/flow/tools/files", "/flow/tools/{name}/run", "/flow/approvals/{id}/status",
                  "/flow/skills/check", ".../procedure", "`POST /flow/skills`"):
        assert shown in page, shown

"""The Verify tab on a skill written as steps.

It used to read such a file with the long form's eyes and report 0 of 8:
"No action declares `requires:` — nothing is gated", about a skill whose refund
cannot run without a yes. The report now comes from what the loader accepted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, guarantees, procedure
from aigenos_flow.hosts.test_host import TestHost

REPO = Path(__file__).resolve().parents[2]
SHIPPED = REPO / "examples" / "studio_agent" / "skills"

pytestmark = pytest.mark.skipif(not SHIPPED.is_dir(), reason="repo examples not present (packaged install)")


@pytest.fixture(scope="module")
def agent():
    return FlowAgent.from_dir(SHIPPED, host=TestHost())


def _report(agent, name: str) -> dict:
    front = guarantees.frontmatter_of((SHIPPED / name / "SKILL.md").read_text(encoding="utf-8"))
    return guarantees.analyze_steps(front, agent.registry.get(name), procedure.for_skill(agent.registry, name))


def _by_id(report: dict) -> dict:
    return {p["id"]: p for p in report["properties"]}


def test_a_gated_refund_is_reported_as_gated(agent):
    report = _report(agent, "refund-desk")
    props = _by_id(report)
    assert (report["holds"], report["checked"]) == (7, 9)
    assert props["chat-reads-only"]["status"] == "n/a", "no closing chat here, and the report says so"
    gated = props["gated-effects"]
    assert gated["status"] == "holds"
    assert gated["detail"].startswith("`issue_refund` cannot run before a `confirm:` above it is answered yes")
    assert "`lookup_order`" in gated["detail"] and "only look things up" in gated["detail"]
    assert "`manager_code`" in props["user-only"]["detail"] and "`order_id`" in props["user-only"]["detail"]
    assert props["gate-recovery"]["detail"].startswith("3 rules:")
    assert props["others-yes"]["status"] == "n/a", "nothing here is asked of someone else, and it says so"
    assert props["at-most-once"]["kind"] == "runtime" and props["approved-is-run"]["kind"] == "runtime"


def test_a_yes_from_finance_is_reported(agent):
    report = _report(agent, "plan-team-offsite")
    props = _by_id(report)
    assert (report["holds"], report["checked"]) == (8, 9)
    assert props["others-yes"]["detail"].startswith("A yes is asked of finance.")
    assert "`save_request`" in props["gated-effects"]["detail"]


def test_a_router_with_no_tools_claims_nothing_it_does_not_have(agent):
    props = _by_id(_report(agent, "send-money"))
    assert props["gated-effects"]["status"] == "n/a" and props["at-most-once"]["status"] == "n/a"
    assert props["names-resolve"]["status"] == "holds" and props["user-only"]["status"] == "holds"


def test_every_shipped_steps_skill_gets_a_report_with_no_long_form_words(agent):
    for skill in agent.registry.all():
        front = guarantees.frontmatter_of((skill.path / "SKILL.md").read_text(encoding="utf-8"))
        if not isinstance(front.get("steps"), list):
            continue
        report = _report(agent, skill.name)
        assert report["checked"] == 9
        text = " ".join(p["title"] + " " + p["detail"] for p in report["properties"])
        for word in ("requires:", "actions:", "else_clear", "must_elicit"):
            assert word not in text, (skill.name, word)


def test_the_server_reports_on_steps_as_steps(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    import importlib.util

    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location(
        "_tool_api_tests", Path(__file__).with_name("test_tool_library_api.py"))
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)

    main_mod = api._sandbox._fresh_app(tmp_path, monkeypatch)
    client = TestClient(main_mod.app, raise_server_exceptions=False)
    assert client.put("/flow/tools/files", json={"path": "tools/bank.py", "source": api.BANK}).status_code == 200
    assert client.post("/flow/skills", json={"name": "take-cash", "skill_md": api.CASH}).status_code == 200

    report = client.get("/flow/skills/take-cash/guarantees").json()
    props = {p["id"]: p for p in report["properties"]}
    assert props["gated-effects"]["status"] == "holds" and "`withdraw`" in props["gated-effects"]["detail"]
    assert "`get_balance` only looks things up" in props["gated-effects"]["detail"]
    assert report["holds"] >= 6

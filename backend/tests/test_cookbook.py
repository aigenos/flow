"""docs/cookbook.md is run, not trusted.

Every file the cookbook prints is written to a temp folder exactly as printed,
and its own test file and `run.py` are executed there. A recipe that stops
loading, or a reply the page quotes that the engine no longer says, fails here.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COOKBOOK = REPO / "docs" / "cookbook.md"

pytestmark = pytest.mark.skipif(not COOKBOOK.is_file(), reason="docs/ not present (packaged install)")

#: **`path`** on its own line, a blank line, then a fenced block.
FILE_BLOCK = re.compile(r"^\*\*`([^`\n]+)`\*\*\n\n```(\w*)\n(.*?)\n```", re.S | re.M)
ANY_BLOCK = re.compile(r"```(\w*)\n(.*?)\n```", re.S)

EXPECTED = {
    # Three skills with no Python of their own, and one shared tool library.
    "skills/tools/orders.py",
    "skills/cancel-order/SKILL.md",
    "skills/refund-order/SKILL.md",
    "skills/help-desk/SKILL.md",
    "tests/test_desk.py", "run.py",
}


@pytest.fixture(scope="module")
def page() -> str:
    return COOKBOOK.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def project(page, tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cookbook")
    for rel, _lang, body in FILE_BLOCK.findall(page):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body + "\n", encoding="utf-8")
    return root


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    env["PYTHONPATH"] = str(REPO / "backend")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], cwd=cwd, env=_env(), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def test_the_page_prints_every_file_of_the_project(page):
    assert {rel for rel, _, _ in FILE_BLOCK.findall(page)} == EXPECTED


def test_every_fragment_is_cut_from_a_file_the_page_prints(page):
    """Recipes 2 to 5 show parts of refund-order, and recipes 4 and 7 parts of
    the tool library. A part that drifts from the file printed in full would be
    a recipe nobody ran."""
    files = {rel: body for rel, _, body in FILE_BLOCK.findall(page)}
    printed_as_files = set(files.values())
    skill = files["skills/refund-order/SKILL.md"]
    yaml_parts = [body for lang, body in ANY_BLOCK.findall(page) if lang == "yaml" and body not in printed_as_files]
    assert len(yaml_parts) >= 5
    for fragment in yaml_parts:
        assert fragment in skill, f"not in refund-order/SKILL.md:\n{fragment}"

    library = files["skills/tools/orders.py"]
    tool_parts = [body for lang, body in ANY_BLOCK.findall(page)
                  if lang == "python" and body not in printed_as_files and body.lstrip().startswith(("def ", "@readonly"))]
    assert len(tool_parts) >= 2
    for fragment in tool_parts:
        assert fragment in library, f"not in tools/orders.py:\n{fragment}"


def test_the_cookbooks_skills_have_no_python_of_their_own(project):
    """The point of the page: a skill is a file of steps, and the functions it
    names live in one shared place."""
    skills = project / "skills"
    for folder in ("cancel-order", "refund-order", "help-desk"):
        assert sorted(p.name for p in (skills / folder).iterdir()) == ["SKILL.md"]
        text = (skills / folder / "SKILL.md").read_text(encoding="utf-8")
        assert "steps:" in text and "fields:" not in text and "actions:" not in text


def test_the_cookbooks_own_tests_pass_with_no_api_key(page, project):
    proc = _run(["-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], project)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    ran = re.search(r"(\d+) passed", proc.stdout).group(1)
    quoted = re.search(r"```bash\npytest\n```\n\n```\n(\d+) passed\n```", page).group(1)
    assert ran == quoted, f"the page says {quoted} passed; {ran} did"


def test_run_py_prints_what_the_page_says_it_prints(page, project):
    proc = _run(["run.py"], project)
    assert proc.returncode == 0, proc.stderr[-2000:]
    quoted = re.search(r"\*\*`run\.py`\*\*\n\n```python\n.*?\n```\n\n```\n(.*?)\n```", page, re.S).group(1)
    assert proc.stdout.strip() == quoted.strip()


def test_recipe_9_compiles_to_a_graph_with_a_chat_model(project):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from aigenos_flow import FlowAgent
    from aigenos_flow.hosts.langgraph_host import LangGraphHost

    agent = FlowAgent.from_dir(project / "skills", host=LangGraphHost(FakeListChatModel(responses=["ok"])))
    graph = agent.as_langgraph()
    assert hasattr(graph, "invoke")
    assert sorted(s.name for s in agent.registry.all()) == ["cancel-order", "help-desk", "refund-order"]

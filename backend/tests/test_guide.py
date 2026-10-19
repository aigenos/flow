"""docs/GUIDE.md is run, not trusted.

The tutorial prints four files and several outputs. The files are written to a
temp project exactly as printed; `run.py` is run and its output compared with
the transcript on the page; the page's own tests are run; the three loader
refusals and the procedure it quotes are produced again and compared.
"""
from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest

from aigenos_flow import procedure
from aigenos_flow.skills import load_skills_dir

REPO = Path(__file__).resolve().parents[2]
PAGE = REPO / "docs" / "GUIDE.md"

pytestmark = pytest.mark.skipif(not PAGE.is_file(), reason="docs/ not present (packaged install)")

FILE_BLOCK = re.compile(r"^\*\*`([^`\n]+)`\*\*\n\n```(\w*)\n(.*?)\n```", re.S | re.M)
SKILL = "skills/pay-contractor/SKILL.md"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture
def project(page, tmp_path, monkeypatch):
    for rel, _lang, body in FILE_BLOCK.findall(page):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body + "\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)                    # the page's files say "skills", as a reader's would
    return tmp_path


def _after(page: str, heading: str) -> list[str]:
    """The un-labelled code blocks (outputs) under one `## ` heading."""
    section = page.split(heading, 1)[1].split("\n## ", 1)[0]
    found, lang, lines = [], None, []
    for line in section.splitlines():
        if not line.startswith("```"):
            lines.append(line)
        elif lang is None:
            lang, lines = line[3:].strip(), []
        else:
            if not lang:
                found.append("\n".join(lines))
            lang = None
    return found


def test_the_page_prints_four_files(page):
    assert [rel for rel, _, _ in FILE_BLOCK.findall(page)] == [
        "skills/tools/payments.py", SKILL, "run.py", "tests/test_pay.py",
    ]


def test_run_py_prints_the_transcript_on_the_page(page, project, capsys):
    runpy.run_path(str(project / "run.py"), run_name="__main__")
    printed = capsys.readouterr().out.rstrip()
    assert printed == _after(page, "## Step 4")[0]
    assert "Paid. Reference PAY-0001." in printed and printed.count("Paid.") == 1, "the second yes pays nobody"


def test_the_pages_own_tests_pass(page, project):
    module = runpy.run_path(str(project / "tests" / "test_pay.py"))
    tests = [fn for name, fn in module.items() if name.startswith("test_") and callable(fn)]
    for fn in tests:
        fn()
    assert _after(page, "## Step 5")[0] == f"{len(tests)} passed"


def test_the_loader_says_what_the_page_says(page, project):
    whole = (project / SKILL).read_text(encoding="utf-8")
    broken = [
        whole.replace("  - confirm: Send this payment?\n", ""),
        whole.replace("    when: quote.needs_code", "    wen: quote.needs_code"),
        whole.replace("  - do: send_payment", "  - do: send_paymnt"),
    ]
    quoted = _after(page, "## Step 6")
    assert len(quoted) == len(broken)
    for text, expected in zip(broken, quoted):
        assert text != whole, "the edit the page describes did not apply"
        (project / SKILL).write_text(text, encoding="utf-8")
        with pytest.raises(ValueError) as refused:
            load_skills_dir(project / "skills", strict=True)
        assert str(refused.value).splitlines()[0] == expected


def test_the_procedure_is_what_the_page_shows(page, project):
    printed = procedure.for_skill(load_skills_dir(project / "skills"), "pay-contractor")["markdown"]
    quoted = _after(page, "## Step 7")
    assert len(quoted) == 3
    for part in quoted:
        assert part in printed
    assert "2 ways through this flow" in quoted[1]


def test_what_the_page_says_the_engine_works_out(project):
    skill = load_skills_dir(project / "skills").get("pay-contractor")
    assert skill.fields["amount"].type == "number", "from quote(amount: float)"
    assert skill.fields["code"].sensitive is True, "from verify(code: Secret)"
    assert skill.fields["contractor"].must_elicit and skill.fields["amount"].must_elicit, "they decide whether a code is needed"
    assert skill.readonly_tools == "quote, verify"

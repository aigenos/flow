"""The pages say how big the language is. They must be counting the loader's own lists.

A page that says "four kinds of step, that is the language" next to a file that
needs a word the page never mentioned is how a reader ends up surprised. Every
number here is computed from the closed word lists the loader checks and from
the skills the repo ships, so a new word or a new example cannot leave the
pages behind.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from aigenos_flow import shortform
from aigenos_flow import skills as S
from aigenos_flow.fields import FieldSpec

REPO = Path(__file__).resolve().parents[2]
DOCS = REPO / "docs"
pytestmark = pytest.mark.skipif(not (DOCS / "GUIDE.md").exists(), reason="repo docs not present (packaged install)")

# ---- the steps language: what a skill file written as steps may say
KINDS = list(shortform.VERBS)
CONDITIONS = {"when", "check"}
LISTS = {"repeat"}
WORDS = set(shortform.WORDS)
OPTIONS = WORDS - set(KINDS) - CONDITIONS - LISTS   # `steps` itself is counted with the options

# ---- the long form underneath it
LONG_KINDS = [v for v in S._STEP_VERBS if v != "run"]
LONG_ALL = (
    set(LONG_KINDS) | set(S._STEP_GUARDS) | set().union(*S._STEP_MODIFIERS.values())
    | {(f.alias or n) for n, f in S.SkillFrontmatter.model_fields.items()}
    | set(FieldSpec.model_fields) | set(S.SkillActionSpec.model_fields)
    | set(S._LEAF_KEYS) | set(S.GuidelineSpec.model_fields)
)

#: The skills the Studio opens from a clone (your own experiments in that folder are not counted).
SHIPPED = ("add-payee", "book-meeting-room", "conference-trip", "list-rooms",
           "pay-someone", "plan-team-offsite", "refund-desk", "send-money")
NUMBER_WORDS = {4: "four", 5: "five", 7: "seven", 11: "eleven", 12: "twelve"}


def _used(steps: list) -> set[str]:
    """The distinct words of the language a list of steps writes."""
    used: set[str] = set()
    for step in steps or []:
        used |= set(step)
        if isinstance(step.get("steps"), list):
            used |= _used(step["steps"])
    return used


def _front(text: str) -> dict:
    return yaml.safe_load(text.split("---")[1])


def _flat(name: str) -> str:
    path = REPO / name if name.endswith("README.md") else DOCS / name
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_pages_count_what_the_loader_checks():
    assert (len(KINDS), len(CONDITIONS), len(LISTS), len(OPTIONS), len(WORDS)) == (5, 2, 1, 12, 20)
    assert KINDS == ["ask", "say", "do", "confirm", "chat"]
    n = len(WORDS)
    assert f"{n} in all" in _flat("README.md") and f"[{n} words, on one page]" in _flat("README.md")
    assert f"**{n} in all**" in _flat("steps.md")
    assert f"all {n} words on one page" in _flat("GUIDE.md")
    assert f"**{n} words in all**" in _flat("developer-guide.md")
    assert f"{n} words in all" in _flat("cookbook.md")
    for page in ("README.md", "steps.md", "developer-guide.md"):
        flat = _flat(page).lower()
        assert f"{NUMBER_WORDS[len(OPTIONS)]} more words" in flat or f"{len(OPTIONS)} options" in flat, page


def test_the_five_kinds_are_named_wherever_they_are_counted():
    listed = ", ".join(f"`{k}`" for k in KINDS)
    for page in ("README.md", "backend/README.md", "developer-guide.md"):
        assert listed in _flat(page), page
    guide = (DOCS / "GUIDE.md").read_text(encoding="utf-8")
    for kind in KINDS:
        assert f"| `{kind}:` |" in guide, f"GUIDE.md's table of step kinds has no row for {kind}"
    assert f"**{NUMBER_WORDS[len(KINDS)]} kinds of step**" in guide


def test_every_option_the_readme_lists_is_one_the_loader_takes():
    flat = _flat("README.md")
    sentence = re.search(r"Twelve more words are options on those steps \((.*?)\)", flat).group(1)
    listed = set(re.findall(r"`(\w+)`", sentence))
    assert listed == OPTIONS, (sorted(listed - OPTIONS), sorted(OPTIONS - listed))


def test_the_skill_at_the_top_of_the_readme_uses_as_many_words_as_it_says():
    for name in ("README.md", "backend/README.md"):
        page = (REPO / name).read_text(encoding="utf-8")
        block = next(b for b in re.findall(r"```yaml\n(.*?)\n```", page, re.S) if "\nsteps:\n" in b)
        used = _used(_front(block)["steps"]) | {"steps"}
        assert used <= WORDS, sorted(used - WORDS)
        if name == "README.md":
            assert f"uses {len(used)} of them" in " ".join(page.split())


def test_the_shipped_skills_stay_inside_the_language_and_the_range_the_pages_quote():
    counts = []
    for name in SHIPPED:
        front = _front((REPO / "examples/studio_agent/skills" / name / "SKILL.md").read_text(encoding="utf-8"))
        if "steps" not in front:
            assert name == "list-rooms", f"{name} is not written as steps"      # the one Python flow, kept as the example of one
            continue
        used = _used(front["steps"]) | {"steps"}
        assert used <= WORDS, (name, sorted(used - WORDS))
        counts.append(len(used))
    assert f"The {NUMBER_WORDS[len(counts)]} skills the Studio ships as steps use between {min(counts)} and {max(counts)} of them" \
        in _flat("developer-guide.md")


def test_the_long_form_is_counted_too_where_it_is_described():
    guards, options = set(S._STEP_GUARDS), set().union(*S._STEP_MODIFIERS.values())
    assert (len(LONG_KINDS), len(guards), len(options), len(LONG_ALL)) == (9, 2, 12, 64)
    for page in ("developer-guide.md", "skill-authoring.md"):
        flat = _flat(page)
        assert f"{len(LONG_KINDS)} step kinds" in flat and f"{len(guards)} guards" in flat, page
        assert f"{len(LONG_ALL)} exact names in all" in flat, page


def test_the_first_cookbook_skill_is_as_small_as_the_page_says():
    page = (DOCS / "cookbook.md").read_text(encoding="utf-8")
    block = re.search(r"\*\*`skills/cancel-order/SKILL.md`\*\*\n\n```yaml\n(.*?)\n```", page, re.S).group(1)
    used = _used(_front(block)["steps"]) | {"steps"}
    assert used <= WORDS
    assert f"first skill below uses {len(used)} of them" in " ".join(page.split())

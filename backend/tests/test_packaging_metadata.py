"""Regression tests for the published packaging metadata.

Filed as pip-fresh-1 (the ``[server]`` extra shipped no LLM provider, so every
chat route answered HTTP 500 with a raw ``ModuleNotFoundError``), pip-fresh-5
(the declared ``langgraph>=0.2.50`` floor was false — ``create_agent`` crashes
there), pip-fresh-10 (the sdist swept in the repo-root ``.gitignore``) and
pip-fresh-12 (no Python 3.13 classifier).

The floors below were established by installing the wheel with each dependency
pinned to a candidate minimum and running the four documented entry points
(``import *``, ``FlowAgent`` + ``TestHost`` turn, ``create_agent``,
``wrap_agent``). Results, for the record:

    langgraph 0.2.50  FAIL  create_react_agent() has no 'prompt' argument
    langgraph 0.3.0   FAIL  yanked; no langgraph.prebuilt at all
    langgraph 0.4.0   FAIL  no langgraph.stream
    langgraph 0.4.10  FAIL  no langgraph.stream
    langgraph 0.5.0   FAIL  MRO error building the graph
    langgraph 0.6.0   PASS  <- the floor
    langgraph 0.6.11  PASS
    langgraph 1.0.0   FAIL  only with langgraph-prebuilt 1.0.9..1.0.13
    langgraph 1.0.10  PASS  with langgraph-prebuilt 1.0.8
    langgraph 1.2.12  PASS  (what a no-pin install resolves to)

This module cannot install anything, so it pins the *declared* floors: if
someone lowers one back to a version that was measured broken, this fails.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


@pytest.fixture(scope="module")
def meta() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def project(meta) -> dict:
    return meta["project"]


def _requirement(deps: list[str], name: str) -> str:
    for dep in deps:
        head = dep.split(";")[0].strip()
        base = head.split("[")[0]
        for sep in (">=", "==", ">", "!=", "<", "~="):
            base = base.split(sep)[0]
        if base.strip().lower().replace("_", "-") == name:
            return head
    raise AssertionError(f"{name} is not declared in {deps}")


# ------------------------------------------------------- pip-fresh-5: floors

# Measured minimums. Raising one is fine; LOWERING one needs a fresh matrix run.
VERIFIED_FLOORS = {
    "langgraph": "0.6.0",
    "langchain-core": "0.3.67",
    "pydantic": "2.7.4",
}


@pytest.mark.parametrize("name,floor", sorted(VERIFIED_FLOORS.items()))
def test_declared_floor_is_the_verified_one(project, name, floor):
    requirement = _requirement(project["dependencies"], name)
    assert f">={floor}" in requirement, (
        f"{name} floor changed to {requirement!r}; {floor} is the oldest version the "
        "four documented entry points were verified against"
    )


def test_langgraph_floor_is_not_the_old_false_one(project):
    assert ">=0.2.50" not in _requirement(project["dependencies"], "langgraph"), (
        "langgraph>=0.2.50 was the reported false floor: create_react_agent() there "
        "has no 'prompt' keyword, so create_agent and prove.py both crash"
    )


def test_langgraph_prebuilt_is_declared_and_the_broken_window_excluded(project):
    """The code imports ``langgraph.prebuilt`` directly, so declare it.

    langgraph-prebuilt 1.0.9..1.0.13 import ``langgraph.runtime.ExecutionInfo``,
    which only exists from langgraph 1.2 — yet langgraph 1.0.x/1.1.x pin
    ``langgraph-prebuilt<1.1.0,>=1.0.8``, so pip resolves a combination that
    cannot import at all.
    """
    requirement = _requirement(project["dependencies"], "langgraph-prebuilt")
    for broken in ("1.0.9", "1.0.10", "1.0.11", "1.0.12", "1.0.13"):
        assert f"!={broken}" in requirement, (broken, requirement)


def test_the_installed_langgraph_satisfies_the_declared_floor(project):
    """The suite must not be passing on a version the metadata forbids."""
    packaging = pytest.importorskip("packaging.requirements")
    from importlib.metadata import version

    for name in ("langgraph", "langgraph-prebuilt", "langchain-core", "pydantic"):
        requirement = packaging.Requirement(_requirement(project["dependencies"], name))
        installed = version(name)
        assert requirement.specifier.contains(installed, prereleases=True), (
            f"installed {name} {installed} does not satisfy declared {requirement}"
        )


# ------------------------------------------- pip-fresh-1: honest [server] extra

PROVIDER_EXTRAS = {
    "anthropic": "langchain-anthropic",
    "openai": "langchain-openai",
    "gemini": "langchain-google-genai",
    "ollama": "langchain-ollama",
}


def test_server_extra_ships_a_usable_llm_provider(project):
    """`pip install "aigenos-flow[server]"` must be able to serve a chat turn.

    The chat routes need a LangChain chat model. With no provider the router
    used to raise ``ModuleNotFoundError: No module named 'langchain_ollama'``,
    which FastAPI turned into a 500 whose body was "Internal Server Error".
    """
    server = project["optional-dependencies"]["server"]
    installed = {
        dist for dist in PROVIDER_EXTRAS.values()
        if any(dist in dep for dep in server)
    }
    assert installed, f"[server] ships no LangChain provider: {server}"
    # The no-API-key default path resolves to Ollama, so that one must be there
    # or a keyless `[server]` install still cannot answer.
    assert "langchain-ollama" in installed, (
        "[server] must include langchain-ollama: it is the provider the auto-detect "
        "falls back to when no provider API key is set"
    )


def test_every_provider_extra_matches_the_resolver(project):
    """The pip extra names in the error messages must be real extras."""
    from aigenos_flow.ai.llm import PROVIDERS

    extras = project["optional-dependencies"]
    for provider, (_module, extra, distribution, _selector) in PROVIDERS.items():
        assert extra in extras, f"ai.llm points at a non-existent extra {extra!r}"
        assert any(distribution in dep for dep in extras[extra]), (
            provider, distribution, extras[extra]
        )
        assert PROVIDER_EXTRAS[provider] == distribution


def test_all_extra_covers_every_shipping_extra(project):
    extras = project["optional-dependencies"]
    referenced = {
        dep.split("[", 1)[1].rstrip("]") for dep in extras["all"] if "[" in dep
    }
    expected = set(extras) - {"all", "dev"}
    assert referenced == expected, (referenced ^ expected)


# --------------------------------------- pip-fresh-12 + license/classifier gaps


def test_python_313_is_declared(project):
    assert "Programming Language :: Python :: 3.13" in project["classifiers"]


def test_classifiers_cover_every_supported_minor(project):
    declared = {
        c.rsplit(" :: ", 1)[-1]
        for c in project["classifiers"]
        if c.startswith("Programming Language :: Python :: 3.")
    }
    assert {"3.11", "3.12", "3.13"} <= declared
    assert project["requires-python"] == ">=3.11"


def test_license_uses_a_spdx_expression_and_ships_the_file(project):
    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE", "NOTICE"]
    assert (PYPROJECT.parent / "LICENSE").is_file()
    assert (PYPROJECT.parent / "NOTICE").is_file()
    # PEP 639 deprecates the classifier once a license expression is declared.
    assert not [c for c in project["classifiers"] if c.startswith("License ::")]


def test_console_script_is_declared_and_importable(project):
    # The entry point is a module with no server imports, so a bare install can
    # name the missing extra instead of printing a traceback.
    assert project["scripts"]["aigenos-flow-server"] == "aigenos_flow._server_entry:main"
    source = (PYPROJECT.parent / "aigenos_flow" / "_server_entry.py").read_text(encoding="utf-8")
    assert "from .main import main" in source and "aigenos-flow[server]" in source


def test_the_typed_classifier_is_backed_by_a_marker(project):
    assert "Typing :: Typed" in project["classifiers"]
    assert (PYPROJECT.parent / "aigenos_flow" / "py.typed").is_file()


# ------------------------------------------------- pip-fresh-10: sdist hygiene


def test_sdist_only_includes_what_the_package_needs(meta):
    sdist = meta["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert sdist["only-include"] == [
        "aigenos_flow", "tests", "README.md", "LICENSE", "NOTICE", "pyproject.toml"
    ]
    assert sdist["ignore-vcs"] is True


def test_a_local_gitignore_keeps_the_monorepo_one_out_of_the_sdist():
    """hatchling force-includes the VCS ignore file it finds, walking upward.

    backend/ had none, so the sdist shipped the monorepo root's .gitignore,
    which is no part of this package.
    Neither ``exclude`` nor ``ignore-vcs`` suppresses that force-include
    (hatchling/builders/sdist.py, get_default_build_data), so the fix is to give
    hatchling a local file to stop at. If this file goes away, the root one
    ships again.
    """
    local = PYPROJECT.parent / ".gitignore"
    assert local.is_file(), "backend/.gitignore is load-bearing for sdist hygiene"
    text = local.read_text(encoding="utf-8")
    root = PYPROJECT.parent.parent / ".gitignore"
    if root.is_file():
        assert text != root.read_text(encoding="utf-8")
    for leak in ("Marketing-video", "marketing site", "Internal material", "private repo"):
        assert leak not in text, leak


def test_neither_target_ships_local_server_state(meta):
    targets = meta["tool"]["hatch"]["build"]["targets"]
    for name in ("wheel", "sdist"):
        excluded = " ".join(targets[name]["exclude"])
        assert "studio.db" in excluded, name
        assert "connectors.sqlite3" in excluded, name

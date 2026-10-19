"""Shared fixtures — point every test at the bundled expense example.

Examples live at the repository root, not under backend/. We walk up from this
file to the repository root, then descend into examples/. Setting
``EXAMPLES_DIR`` via env var lets CI override the location if it ever
diverges.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost


def _examples_root() -> Path:
    override = os.getenv("FLOW_EXAMPLES_DIR")
    if override:
        return Path(override)
    # backend/tests/flow/conftest.py → flow → tests → backend → monorepo root
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "examples"


@pytest.fixture
def skills_dir() -> Path:
    return _examples_root() / "expense" / "skills"


@pytest.fixture
def host() -> TestHost:
    return TestHost()


@pytest.fixture
def agent(skills_dir, host) -> FlowAgent:
    return FlowAgent.from_dir(skills_dir, host=host)

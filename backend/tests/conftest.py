"""Keep the suite hermetic: your own model settings never reach it.

The server picks a model from ANTHROPIC_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY
or GEMINI_API_KEY, reads FG_* settings, and loads a ``.env`` from the folder it
runs in and up to four folders above. Without this file, a developer who
exported OPENAI_API_KEY, or keeps a ``.env`` for the Studio, would run these
tests against that provider: every server test answers 503 when the provider's
extra is not installed, and makes real API calls when it is. CI has none of
those settings, and no test here needs them; a test that wants one sets it with
``monkeypatch``.
"""
from __future__ import annotations

import os

import pytest

_MODEL_SETTINGS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)

for _name in list(os.environ):
    if _name in _MODEL_SETTINGS or _name.startswith("FG_"):
        del os.environ[_name]


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reads a ``.env`` from wherever pytest happens to be started."""
    try:
        from aigenos_flow.integrations import flow as flow_routes
    except ImportError:
        return
    monkeypatch.setattr(flow_routes, "load_env_files", lambda: None)

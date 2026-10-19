"""Every example the READMEs advertise as LLM-free must actually run — in CI.

Why this file exists: CI runs `pytest backend/tests`, so nothing under
`examples/` was ever executed by it. The pre-launch audit found a shipped
example whose own tracked test suite failed 4/4 and three scripts that imported
a package no documented install provides, and none of it was caught, because
276 backend tests were green the whole time.

Each script below is documented as "no API key, no LLM". They are run as
subprocesses with every provider key stripped from the environment and stdin
closed, exactly as a visitor on a clean machine would run them, and each must
exit 0. A script that needs a live model (the `main.py` REPLs) is deliberately
not here — see examples/*/README.md, which say so.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "examples"

# (directory, script) — run with cwd=directory, the way each README says to.
OFFLINE_SCRIPTS = [
    ("before_after", "prove.py"),
    ("context_isolation", "prove.py"),
    ("declarative_flow", "prove.py"),
    ("embed_existing_agent", "verify_roundtrip.py"),
    ("insurance_account_insights", "prove.py"),
    ("studio_agent/proofs/conference_trip", "prove.py"),
    ("studio_agent/proofs/send_money", "prove.py"),
    ("subscription_insights", "prove.py"),
    ("subscription_insights", "determinism_check.py"),
    ("widget_pipeline", "prove.py"),
]

KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "AZURE_OPENAI_API_KEY",
)

pytestmark = pytest.mark.skipif(
    not EXAMPLES.is_dir(), reason="examples/ not present (packaged install)"
)


def _keyless_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in KEY_VARS}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.mark.parametrize(
    ("folder", "script"), OFFLINE_SCRIPTS, ids=lambda v: v.replace("/", "-")
)
def test_offline_example_script_exits_zero(folder: str, script: str) -> None:
    directory = EXAMPLES / folder
    path = directory / script
    assert path.is_file(), f"documented example script is missing: {path}"

    proc = subprocess.run(
        [sys.executable, script],
        cwd=directory,
        env=_keyless_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
        pytest.fail(
            f"examples/{folder}/{script} exited {proc.returncode} with no API key:\n{tail}"
        )


def test_book_room_ci_suite_passes() -> None:
    """examples/studio_agent/proofs/book_room/README.md advertises this as the deterministic
    regression guard that runs "with no LLM". Run it here so it cannot rot."""
    suite = EXAMPLES / "studio_agent" / "proofs" / "book_room" / "test_scenarios_ci.py"
    assert suite.is_file(), f"missing: {suite}"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(suite)],
        cwd=REPO / "backend",
        env=_keyless_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
        pytest.fail(f"book_room CI suite failed:\n{tail}")

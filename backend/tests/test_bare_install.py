"""`pip install aigenos-flow` with no extras must run the README's proof.

FastAPI and sse-starlette live in the `[server]` extra. `create_agent` and
`wrap_agent` import `aigenos_flow.integrations.flow` to build the widget for a
paused field; that module once imported FastAPI at the top, so on a bare
install the widget came back `None` and `examples/before_after/prove.py` — the
first command in the README — failed one of its 42 checks.

The suite itself runs with the server extra installed, so each test here runs a
child interpreter in which those packages cannot be imported.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Refuse the server-only packages the way a bare install would.
BARE = """
import sys

class _NotInstalled:
    BLOCKED = {"fastapi", "sse_starlette", "starlette", "uvicorn"}

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r} (bare install)")
        return None

sys.meta_path.insert(0, _NotInstalled())
"""


def _run_bare(body: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-c", BARE + body],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def test_the_widget_builder_imports_without_the_server_extra():
    result = _run_bare(
        "import aigenos_flow.integrations.flow as flow\n"
        "assert callable(flow.resolve_widget_for_field)\n"
        "assert 'fastapi' not in sys.modules\n"
    )
    assert result.returncode == 0, result.stderr[-800:]


@pytest.mark.parametrize(
    "script, checks",
    [
        ("examples/before_after/prove.py", "42 checks"),
        ("examples/context_isolation/prove.py", "26 checks"),
    ],
)
def test_the_readme_proofs_pass_on_a_bare_install(script: str, checks: str):
    result = _run_bare(
        "import runpy\n"
        f"runpy.run_path({script!r}, run_name='__main__')\n"
    )
    assert result.returncode == 0, (result.stdout[-1200:], result.stderr[-800:])
    verdict = [line for line in result.stdout.splitlines() if line.startswith("RESULT:")]
    assert verdict and "PASS" in verdict[-1] and checks in verdict[-1], verdict

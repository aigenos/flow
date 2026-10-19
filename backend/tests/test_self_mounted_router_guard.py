"""`make_flow_router` enforces FG_SKILL_WRITES itself, wherever it is mounted.

The policy used to live only in `aigenos_flow.main`'s middleware. An
application that did what the docs describe — `app.include_router(
make_flow_router(...))` in its own FastAPI app — therefore served the skill
endpoints with no gate at all: a remote client could `PUT` a handlers.py and
have it imported, even with `FG_SKILL_WRITES=off`.

Nothing here imports `aigenos_flow.main`; every app below is the developer's
own.
"""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from aigenos_flow.integrations.flow import make_flow_router  # noqa: E402

SKILL_MD = """---
name: greet
description: Say hello to the user.
triggers:
  - say hello
---

Greet the user warmly.
"""

HANDLERS_PY = '''from aigenos_flow import flow


@flow
def greet(ctx):
    ctx.say("hello")
'''

REMOTE = ("203.0.113.7", 51234)

# (method, path) for every route that writes a skill or returns its source.
MANAGED = [
    ("get", "/flow/skills/greet/files"),
    ("put", "/flow/skills/greet"),
    ("post", "/flow/skills/greet/restore/1"),
    ("put", "/flow/skills/greet/scenarios"),
    ("post", "/flow/skills/greet/scenarios/run"),
    ("post", "/flow/gallery/greet/install"),
    ("delete", "/flow/skills/greet"),
    ("post", "/flow/demo/reset-bookings"),
    ("post", "/flow/reload"),
    ("post", "/flow/create-skill"),
    ("post", "/flow/preview-skill"),
]


@pytest.fixture
def own_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The developer's own FastAPI app with the flow router included."""
    from aigenos_flow import store as store_mod

    skills = tmp_path / "skills"
    (skills / "greet").mkdir(parents=True)
    (skills / "greet" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skills / "greet" / "handlers.py").write_text(HANDLERS_PY, encoding="utf-8")
    monkeypatch.setenv("FG_STUDIO_DB", str(tmp_path / "studio.db"))
    monkeypatch.setenv("FG_CONNECTOR_STORE_PATH", str(tmp_path / "connectors.sqlite3"))
    monkeypatch.setattr(store_mod, "_store", None)
    for name in ("FG_SKILL_WRITES", "FG_ALLOW_SKILL_WRITES", "FG_ADMIN_TOKEN",
                 "FG_CONNECTOR_ADMIN_TOKEN", "FG_CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)

    app = FastAPI()
    app.include_router(make_flow_router(skills_dir=str(skills), llm=object()), prefix="/flow")
    return app, skills


def _payload(marker: Path) -> dict[str, str]:
    """A handlers.py that loads as a valid skill AND writes a file when imported."""
    return {
        "handlers_py": (
            "from aigenos_flow import flow\n"
            f"open(r'{marker}', 'w').write('executed')\n\n\n"
            "@flow\ndef greet(ctx):\n    ctx.say('x')\n"
        )
    }


@pytest.mark.parametrize("method, path", MANAGED)
def test_a_remote_client_is_refused_on_every_management_route(own_app, method, path):
    app, _ = own_app
    remote = TestClient(app, raise_server_exceptions=False, client=REMOTE)
    response = remote.request(method.upper(), path, json={})
    assert response.status_code == 403, (path, response.status_code, response.text[:200])
    assert "FG_SKILL_WRITES" in response.json()["detail"]


def test_a_remote_upload_is_not_executed(own_app, tmp_path):
    app, skills = own_app
    marker = tmp_path / "executed.txt"
    remote = TestClient(app, raise_server_exceptions=False, client=REMOTE)

    assert remote.put("/flow/skills/greet", json=_payload(marker)).status_code == 403
    assert not marker.exists(), "an uploaded handlers.py was imported"
    assert (skills / "greet" / "handlers.py").read_text(encoding="utf-8") == HANDLERS_PY


def test_off_refuses_even_loopback(own_app, tmp_path, monkeypatch):
    app, _ = own_app
    monkeypatch.setenv("FG_SKILL_WRITES", "off")
    marker = tmp_path / "executed.txt"
    local = TestClient(app, raise_server_exceptions=False)

    assert local.get("/flow/skills/greet/files").status_code == 403
    assert local.put("/flow/skills/greet", json=_payload(marker)).status_code == 403
    assert not marker.exists()


def test_token_mode_needs_the_token(own_app, monkeypatch):
    app, _ = own_app
    monkeypatch.setenv("FG_SKILL_WRITES", "token")
    monkeypatch.setenv("FG_ADMIN_TOKEN", "s3cret")
    remote = TestClient(app, raise_server_exceptions=False, client=REMOTE)

    assert remote.get("/flow/skills/greet/files").status_code == 401
    assert remote.get(
        "/flow/skills/greet/files", headers={"Authorization": "Bearer s3cret"}
    ).status_code == 200


def test_the_default_still_lets_the_developers_own_machine_author(own_app):
    """Loopback keeps working with nothing configured — the Studio depends on it."""
    app, _ = own_app
    local = TestClient(app, raise_server_exceptions=False)

    assert local.get("/flow/skills/greet/files").status_code == 200
    assert local.put("/flow/skills/greet", json={"skill_md": SKILL_MD}).status_code == 200


def test_chat_and_read_only_routes_stay_open_to_a_remote_client(own_app):
    app, _ = own_app
    remote = TestClient(app, raise_server_exceptions=False, client=REMOTE)

    assert remote.get("/flow/skills").status_code == 200
    assert remote.get("/flow/skills/greet/outline").status_code == 200

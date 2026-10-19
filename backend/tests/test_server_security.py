"""Regression tests for the optional FastAPI server's security posture.

Filed as server-1 (unauthenticated remote code execution on the /flow
skill-management endpoints, reachable cross-origin because CORS was ``*``),
server-2 (PII stored in cleartext in the connector audit trail) and server-3
(OpenAPI advertising 0.1.0 for a 0.3.1 release).

Everything runs in-process against ``fastapi.testclient.TestClient``; nothing
binds a port, no LLM is called, and every writable path is redirected into
``tmp_path`` before ``aigenos_flow.main`` is imported.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from aigenos_flow import __version__  # noqa: E402


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


def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A skills dir + data dir + databases that live only under tmp_path.

    Redirected by monkeypatching the already-imported module state rather than
    by reloading modules: ``aigenos_flow.integrations.flow`` imports
    ``engine.loader`` lazily, so swapping that module out in ``sys.modules``
    would leave other tests pointed at a deleted tmp_path.
    """
    from aigenos_flow import store as store_mod
    from aigenos_flow.engine import loader as loader_mod
    from aigenos_flow.engine import runtime as runtime_mod

    skills = tmp_path / "skills"
    (skills / "greet").mkdir(parents=True)
    (skills / "greet" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skills / "greet" / "handlers.py").write_text(HANDLERS_PY, encoding="utf-8")
    data = tmp_path / "data"
    (data / "widgets").mkdir(parents=True)
    (data / "workflows").mkdir(parents=True)

    monkeypatch.setenv("FG_SKILLS_DIR", str(skills))
    monkeypatch.setenv("FG_DATA_DIR", str(data))
    monkeypatch.setenv("FG_STUDIO_DB", str(tmp_path / "studio.db"))
    monkeypatch.setenv("FG_CONNECTOR_STORE_PATH", str(tmp_path / "connectors.sqlite3"))
    for name in (
        "FG_CONNECTOR_ADMIN_TOKEN", "FG_ADMIN_TOKEN", "FG_SKILL_WRITES",
        "FG_ALLOW_SKILL_WRITES", "FG_CORS_ORIGINS", "FG_ALLOW_PUBLIC_BIND",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(loader_mod, "DATA_DIR", data)
    monkeypatch.setattr(loader_mod, "WIDGET_DIR", data / "widgets")
    monkeypatch.setattr(loader_mod, "WORKFLOW_DIR", data / "workflows")
    monkeypatch.setattr(store_mod, "_store", None)
    # A route that saves calls runtime.reload_assets(), which repopulates these
    # from the redirected dirs; snapshot them so teardown puts them back.
    monkeypatch.setattr(runtime_mod, "WIDGETS", dict(runtime_mod.WIDGETS))
    monkeypatch.setattr(runtime_mod, "WORKFLOWS", dict(runtime_mod.WORKFLOWS))
    return skills


def _fresh_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str):
    """Import aigenos_flow.main against a throwaway sandbox.

    CORS and the router mount are decided at import time, so only this one
    module has to be re-imported (and monkeypatch restores the original).
    """
    _sandbox(tmp_path, monkeypatch)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delitem(sys.modules, "aigenos_flow.main", raising=False)
    return importlib.import_module("aigenos_flow.main")


@pytest.fixture
def main_mod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _fresh_app(tmp_path, monkeypatch)


@pytest.fixture
def client(main_mod):
    return TestClient(main_mod.app, raise_server_exceptions=False)


def _rce_payload(marker: Path) -> dict[str, str]:
    """A handlers.py that both loads as a valid skill AND writes a file.

    The write proves the server imported the uploaded module: `load_skills_dir`
    execs handlers.py inside the request.
    """
    return {
        "skill_md": SKILL_MD,
        "handlers_py": (
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('pwned')\n" + HANDLERS_PY
        ),
    }


# ---------------------------------------------------------------- server-1

MUTATING = [
    ("put", "/flow/skills/greet"),
    ("delete", "/flow/skills/greet"),
    ("post", "/flow/skills/greet/restore/1"),
    ("put", "/flow/skills/greet/scenarios"),
    ("post", "/flow/gallery/greet/install"),
    ("post", "/flow/reload"),
    ("post", "/flow/create-skill"),
    ("post", "/flow/preview-skill"),
    ("post", "/flow/skills"),
    ("post", "/flow/skills/check"),
    ("post", "/flow/skills/edit"),
    ("put", "/flow/tools/files"),
    ("delete", "/flow/tools/files"),
    ("post", "/flow/tools/x/run"),
    ("post", "/flow/approvals/APR-000000"),
    ("put", "/widgets/x"),
    ("delete", "/widgets/x"),
    ("post", "/widgets/reload"),
    ("put", "/workflows/x"),
    ("delete", "/workflows/x"),
    ("post", "/ai/generate-widget"),
]


def _remote(main_mod):
    """A client that is NOT on this machine — the attacker in the RCE report."""
    return TestClient(
        main_mod.app, raise_server_exceptions=False, client=("203.0.113.7", 51234)
    )


@pytest.mark.parametrize("method,path", MUTATING)
def test_management_surface_is_refused_off_box_by_default(main_mod, method, path):
    """The default is loopback-only, so every mutating route refuses a remote
    client with no configuration at all. (The Studio runs on loopback, which is
    why the default is `local` and not `off` — see the next two tests.)"""
    response = _remote(main_mod).request(method.upper(), path, json={})
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert "loopback" in response.json()["detail"]


@pytest.mark.parametrize("method,path", MUTATING)
def test_off_closes_the_surface_to_everyone(client, monkeypatch, method, path):
    """FG_SKILL_WRITES=off is the posture for serving chat with no authoring."""
    monkeypatch.setenv("FG_SKILL_WRITES", "off")
    response = client.request(method.upper(), path, json={})
    assert response.status_code == 403, (path, response.status_code, response.text)
    assert "FG_SKILL_WRITES" in response.json()["detail"]


def test_remote_skill_write_does_not_execute_python(main_mod, tmp_path):
    """The reported RCE: PUT a handlers.py and the server imports it. With no
    configuration, an off-box client cannot reach that path at all."""
    marker = tmp_path / "PWNED.txt"
    response = _remote(main_mod).put("/flow/skills/greet", json=_rce_payload(marker))
    assert response.status_code == 403
    assert not marker.exists(), "uploaded handlers.py was executed"


def test_off_mode_skill_write_does_not_execute_python(client, monkeypatch, tmp_path):
    marker = tmp_path / "PWNED.txt"
    monkeypatch.setenv("FG_SKILL_WRITES", "off")
    response = client.put("/flow/skills/greet", json=_rce_payload(marker))
    assert response.status_code == 403
    assert not marker.exists(), "uploaded handlers.py was executed"


def test_skill_source_is_not_readable_off_box(main_mod):
    """GET .../files returns raw handlers.py, so it is on the gated surface."""
    assert _remote(main_mod).get("/flow/skills/greet/files").status_code == 403
    # The tool library is the same kind of thing: the server's own Python.
    assert _remote(main_mod).get("/flow/tools/files").status_code == 403
    # What is waiting on someone's yes carries the user's answers.
    assert _remote(main_mod).get("/flow/approvals").status_code == 403


def test_the_studio_works_on_a_dev_box_with_no_configuration(client):
    """The regression this default exists for: every call the Studio makes to
    load and author a skill must succeed from loopback, unconfigured. Before
    this, the Canvas could not even open a skill (403 on .../files) and
    creating one was impossible."""
    assert client.get("/flow/skills").status_code == 200
    assert client.get("/flow/skills/greet/files").status_code == 200
    assert client.post("/flow/reload").status_code == 200
    assert client.get("/store/revisions/skill/greet").status_code == 200


def test_token_mode_requires_the_token(client, monkeypatch, tmp_path):
    monkeypatch.setenv("FG_SKILL_WRITES", "token")
    monkeypatch.setenv("FG_ADMIN_TOKEN", "s3cret")
    marker = tmp_path / "edit.txt"
    payload = _rce_payload(marker)

    assert client.put("/flow/skills/greet", json=payload).status_code == 401
    assert client.put(
        "/flow/skills/greet", json=payload, headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    assert not marker.exists()

    ok = client.put(
        "/flow/skills/greet", json=payload, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200, ok.text
    assert marker.exists(), "an authorized operator edit must still work"


def test_token_mode_accepts_the_x_admin_token_header(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "token")
    monkeypatch.setenv("FG_ADMIN_TOKEN", "s3cret")
    assert client.get(
        "/flow/skills/greet/files", headers={"X-Admin-Token": "s3cret"}
    ).status_code == 200


def test_token_mode_without_a_token_stays_shut(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "token")
    response = client.post("/flow/reload")
    assert response.status_code == 503
    assert "FG_ADMIN_TOKEN" in response.json()["detail"]


def test_configuring_a_token_is_itself_the_opt_in(client, monkeypatch):
    """FG_ADMIN_TOKEN alone implies token mode — it never means 'wide open'."""
    monkeypatch.setenv("FG_ADMIN_TOKEN", "s3cret")
    assert client.post("/flow/reload").status_code == 401
    assert client.post(
        "/flow/reload", headers={"Authorization": "Bearer s3cret"}
    ).status_code == 200


def test_local_mode_refuses_a_cross_origin_write(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "local")
    assert client.post("/flow/reload").status_code == 200
    denied = client.post("/flow/reload", headers={"Origin": "https://evil.example"})
    assert denied.status_code == 403
    assert "cross-origin" in denied.json()["detail"]
    assert client.post(
        "/flow/reload", headers={"Origin": "http://localhost:5173"}
    ).status_code == 200


def test_local_mode_refuses_a_remote_client(main_mod, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "local")
    client = TestClient(
        main_mod.app, raise_server_exceptions=False, client=("203.0.113.7", 51234)
    )
    denied = client.post("/flow/reload")
    assert denied.status_code == 403
    assert "loopback" in denied.json()["detail"]


def test_unknown_mode_falls_back_to_closed(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "maybe")
    assert client.post("/flow/reload").status_code == 403


def test_open_mode_is_the_only_ungated_setting(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    assert client.post("/flow/reload").status_code == 200


def test_open_mode_shouts_at_startup(main_mod, monkeypatch, caplog):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    with caplog.at_level("WARNING"):
        asyncio.run(main_mod._announce_posture())
    assert any("FG_SKILL_WRITES=open" in r.message for r in caplog.records)
    assert any("write Python" in r.message for r in caplog.records)


def test_reads_and_chat_are_not_gated(client):
    """The gate covers the management surface, not the product."""
    for path in ("/health", "/flow/skills", "/widgets", "/workflows", "/store/activity"):
        assert client.get(path).status_code == 200, path


def test_connector_routes_keep_their_own_auth(client):
    """/flow/connectors/** must not be double-gated by the skill-write guard."""
    response = client.get("/flow/connectors")
    assert response.status_code == 503
    assert "connector management is disabled" in response.json()["detail"]


# ---------------------------------------------------------------- CORS


def test_cors_is_not_a_wildcard_by_default(client):
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"

    evil = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_cors_preflight_for_a_write_is_refused_for_a_foreign_origin(client):
    refused = client.options(
        "/flow/skills/greet",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"},
    )
    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers


def test_cors_origins_are_configurable(tmp_path, monkeypatch):
    main = _fresh_app(tmp_path, monkeypatch, FG_CORS_ORIGINS="https://studio.example")
    client = TestClient(main.app, raise_server_exceptions=False)
    ok = client.get("/health", headers={"Origin": "https://studio.example"})
    assert ok.headers["access-control-allow-origin"] == "https://studio.example"
    assert "access-control-allow-origin" not in client.get(
        "/health", headers={"Origin": "http://localhost:5173"}
    ).headers


# ---------------------------------------------- identifier hygiene / traversal

TRAVERSAL_IDS = [
    "..",
    ".",
    "%2e%2e",
    "..%2F..%2Fescaped",
    "C:escaped",
    "greet%00",
    "greet ",
    "greet.",
    "-leading-dash",
    ".hidden",
    "CON",
    "com1",
    "x" * 300,
]


@pytest.mark.parametrize("bad", TRAVERSAL_IDS)
def test_widget_ids_never_reach_the_filesystem_writer(client, monkeypatch, bad, tmp_path):
    """A hostile widget id is a 400 at the boundary — never a 500, never a file."""
    monkeypatch.setenv("FG_SKILL_WRITES", "open")  # exercise the id check, not the gate
    body = {"id": bad, "version": "1.0.0", "spec": {"type": "form", "children": []}}
    response = client.put(f"/widgets/{bad}", json=body)
    assert response.status_code in (400, 404, 405), (bad, response.status_code)
    assert response.status_code != 500
    written = sorted(p.name for p in (tmp_path / "data" / "widgets").glob("*"))
    assert written == [], written


@pytest.mark.parametrize("bad", ["..", "C:escaped", "greet ", "greet.", "CON", "x" * 300])
def test_revision_refs_are_validated(client, monkeypatch, bad):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")  # exercise the id check, not the gate
    response = client.get(f"/store/revisions/skill/{bad}")
    assert response.status_code in (400, 404), (bad, response.status_code)
    assert response.status_code != 500


def test_revision_body_rejects_an_unknown_kind(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    assert client.get("/store/revisions/nope/x/1").status_code == 400


def test_check_ref_accepts_the_bundled_ids(main_mod):
    for good in ("refund_confirm", "subscription-grid", "workflow2", "a.b-c_d", "x1"):
        assert main_mod.check_ref("widget id", good) == good


@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "a\\b", "CON", "lpt9", "x" * 65, " a", "a "])
def test_check_ref_rejects_everything_dangerous(main_mod, bad):
    with pytest.raises(fastapi.HTTPException) as exc:
        main_mod.check_ref("widget id", bad)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------- bind policy


def test_public_bind_is_refused_while_writes_are_open(main_mod, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    with pytest.raises(SystemExit) as exc:
        main_mod.check_bind("0.0.0.0")
    assert "FG_ALLOW_PUBLIC_BIND" in str(exc.value)

    monkeypatch.setenv("FG_SKILL_WRITES", "local")
    with pytest.raises(SystemExit):
        main_mod.check_bind("0.0.0.0")


def test_loopback_bind_is_always_fine(main_mod, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    main_mod.check_bind("127.0.0.1")
    main_mod.check_bind("localhost")
    main_mod.check_bind("::1")


def test_public_bind_is_allowed_when_writes_are_token_gated(main_mod, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "token")
    monkeypatch.setenv("FG_ADMIN_TOKEN", "s3cret")
    main_mod.check_bind("0.0.0.0")  # warns, does not exit


def test_public_bind_opt_out(main_mod, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    main_mod.check_bind("0.0.0.0", allow_public=True)


def test_cli_defaults_to_loopback(main_mod, monkeypatch):
    """`aigenos-flow-server` with no arguments must not bind the world."""
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    monkeypatch.delenv("FG_HOST", raising=False)
    seen: dict[str, object] = {}

    class FakeUvicorn:
        @staticmethod
        def run(app, **kwargs):
            seen.update(kwargs)

    monkeypatch.setitem(sys.modules, "uvicorn", FakeUvicorn)
    main_mod.main([])
    assert seen["host"] == "127.0.0.1"


# ---------------------------------------------------------------- server-3


def test_openapi_reports_the_package_version(client):
    info = json.loads(client.get("/openapi.json").text)["info"]
    assert info["version"] == __version__ != "0.1.0"


# ------------------------------------------------- missing LLM provider (503)


def test_missing_provider_is_a_503_naming_the_extra(main_mod):
    """pip-fresh-1: a missing langchain provider must never be a bare 500."""
    app = main_mod.app

    @app.get("/_test/no-provider")
    def _boom():  # pragma: no cover - exercised through the client
        raise ModuleNotFoundError("No module named 'langchain_ollama'", name="langchain_ollama")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/_test/no-provider")
    assert response.status_code == 503
    body = response.json()
    assert body["extra"] == "ollama"
    assert body["install"] == 'pip install "aigenos-flow[ollama]"'
    assert "langchain_ollama" in body["detail"]


def test_an_unrelated_import_error_is_not_swallowed(main_mod):
    app = main_mod.app

    @app.get("/_test/other-import-error")
    def _boom():  # pragma: no cover - exercised through the client
        raise ModuleNotFoundError("No module named 'nope'", name="nope")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/_test/other-import-error").status_code == 500


# ------------------------------------------- skill names reaching the filesystem


@pytest.mark.parametrize("bad", ["greet ", "greet.", "%2e%2e", "greet%00", "C:escaped", "x" * 300])
def test_skill_names_are_checked_at_the_boundary(client, monkeypatch, bad):
    """A skill name becomes a directory, so the check cannot live only in the router.

    ``greet%20`` in particular used to answer 200 with the ``greet`` skill's
    source: Windows normalizes the trailing space away, so the name addressed one
    skill while the revision store keyed another.
    """
    monkeypatch.setenv("FG_SKILL_WRITES", "open")  # exercise the name check
    for suffix in ("/files", "/outline", "/scenarios", ""):
        response = client.get(f"/flow/skills/{bad}{suffix}")
        assert response.status_code != 200, (bad, suffix, response.text[:120])
        assert response.status_code != 500, (bad, suffix)


def test_a_trailing_space_no_longer_aliases_another_skill(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    assert client.get("/flow/skills/greet/files").status_code == 200
    aliased = client.get("/flow/skills/greet%20/files")
    assert aliased.status_code == 400
    assert "skill name" in aliased.json()["detail"]


def test_a_legitimate_skill_name_still_works(client, monkeypatch):
    monkeypatch.setenv("FG_SKILL_WRITES", "open")
    assert client.get("/flow/skills/greet/files").status_code == 200
    assert client.post("/flow/gallery/greet/install").status_code in (404, 409)

"""`POST /flow/create-skill` may only replace the skill the caller named.

The skill's name comes from the model, and the model is asked again on every
request. So "replace the skill you just told me exists" has to carry that name:
otherwise the retry can come back under another name and overwrite a skill the
user was never asked about.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

# Importlib mode: load the server sandbox from the file that owns it.
_spec = importlib.util.spec_from_file_location(
    "_server_sandbox", Path(__file__).with_name("test_server_security.py")
)
_sandbox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sandbox)


def _files(name: str, greeting: str) -> dict:
    """What the generator returns, for a skill called `name`."""
    return {
        "name": name,
        "SKILL.md": _sandbox.SKILL_MD.replace("name: greet", f"name: {name}"),
        "handlers.py": _sandbox.HANDLERS_PY.replace("def greet", f"def {name}").replace(
            '"hello"', f'"{greeting}"'
        ),
        "spec": {},
    }


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A sandboxed server whose generator returns whatever `app.next` holds."""
    main_mod = _sandbox._fresh_app(tmp_path, monkeypatch)
    from aigenos_flow import skill_generator
    from aigenos_flow.integrations import flow as flow_mod

    class Harness:
        client = TestClient(main_mod.app, raise_server_exceptions=False)
        skills = tmp_path / "skills"
        next: dict = {}

    harness = Harness()
    monkeypatch.setattr(skill_generator, "generate_skill_files", lambda description, llm: harness.next)
    # No model is ever called: the generator above is canned.
    monkeypatch.setattr(flow_mod, "_auto_llm", lambda: object())
    return harness


def _create(app, **body):
    return app.client.post("/flow/create-skill", json={"description": "say hi", **body})


def test_a_second_create_under_the_same_name_is_refused_and_names_the_skill(app):
    app.next = _files("alpha", "first")
    assert _create(app).status_code == 200

    app.next = _files("alpha", "second")
    again = _create(app)
    assert again.status_code == 409
    body = again.json()
    assert body["name"] == "alpha"
    assert "alpha" in body["detail"]
    assert str(app.skills) not in body["detail"], "the 409 leaked a server path"
    assert '"first"' in (app.skills / "alpha" / "handlers.py").read_text(encoding="utf-8")


def test_replace_overwrites_the_named_skill(app):
    app.next = _files("alpha", "first")
    assert _create(app).status_code == 200

    app.next = _files("alpha", "second")
    assert _create(app, overwrite=True, replace="alpha").status_code == 200
    assert '"second"' in (app.skills / "alpha" / "handlers.py").read_text(encoding="utf-8")


def test_replace_cannot_land_on_a_different_existing_skill(app):
    """Asked to replace `alpha`, the model answers `greet` — which also exists."""
    before = (app.skills / "greet" / "handlers.py").read_text(encoding="utf-8")
    app.next = _files("alpha", "first")
    assert _create(app).status_code == 200

    app.next = _files("greet", "hijacked")
    response = _create(app, overwrite=True, replace="alpha")
    assert response.status_code == 409
    assert response.json()["name"] == "greet"
    assert (app.skills / "greet" / "handlers.py").read_text(encoding="utf-8") == before
    assert '"first"' in (app.skills / "alpha" / "handlers.py").read_text(encoding="utf-8")


def test_replace_under_a_new_name_creates_it_and_leaves_the_named_skill_alone(app):
    app.next = _files("alpha", "first")
    assert _create(app).status_code == 200

    app.next = _files("beta", "other")
    response = _create(app, overwrite=True, replace="alpha")
    assert response.status_code == 200
    assert response.json()["name"] == "beta"
    assert '"first"' in (app.skills / "alpha" / "handlers.py").read_text(encoding="utf-8")

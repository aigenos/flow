"""The Studio Gallery lists example skills that actually ship, and each installs.

The Gallery used to read the repo's top-level ``skills/`` folder, which is
gitignored scratch space — so it worked on the author's machine and was empty
on every clone. It now reads a fixed list of tracked example skills.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from aigenos_flow.integrations.flow import GALLERY_EXAMPLES  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# The suite runs in importlib mode, so a sibling test module is not importable
# by name. Load the server sandbox (throwaway skills dir, data dir, databases)
# from the file that owns it rather than keeping a second copy.
_spec = importlib.util.spec_from_file_location(
    "_server_sandbox", Path(__file__).with_name("test_server_security.py")
)
_sandbox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sandbox)
_fresh_app = _sandbox._fresh_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    main_mod = _fresh_app(tmp_path, monkeypatch)
    return TestClient(main_mod.app, raise_server_exceptions=False)


def test_every_gallery_example_exists_in_the_repo():
    for rel in GALLERY_EXAMPLES:
        assert (REPO / "examples" / rel / "SKILL.md").is_file(), rel


def test_gallery_names_are_unique():
    names = [Path(rel).name for rel in GALLERY_EXAMPLES]
    assert len(names) == len(set(names)), names


def test_gallery_lists_the_shipped_examples(client):
    templates = client.get("/flow/gallery").json()["templates"]
    assert {t["name"] for t in templates} == {Path(rel).name for rel in GALLERY_EXAMPLES}
    for t in templates:
        assert t["description"], t["name"]
        assert t["steps"] > 0, t["name"]
        assert t["installed"] is False, t["name"]


@pytest.mark.parametrize("name", [Path(rel).name for rel in GALLERY_EXAMPLES])
def test_each_example_installs_into_a_fresh_skills_dir(client, name):
    response = client.post(f"/flow/gallery/{name}/install")
    assert response.status_code == 200, response.text
    assert response.json()["name"] == name

    loaded = client.get("/flow/skills").json()
    assert name in {s["name"] for s in loaded.get("skills", loaded)}
    listed = {t["name"]: t for t in client.get("/flow/gallery").json()["templates"]}
    assert listed[name]["installed"] is True

    again = client.post(f"/flow/gallery/{name}/install")
    assert again.status_code == 409, again.text


@pytest.mark.parametrize("name", [Path(rel).name for rel in GALLERY_EXAMPLES])
def test_a_card_promises_what_the_verify_tab_reports_once_installed(client, name):
    card = {t["name"]: t for t in client.get("/flow/gallery").json()["templates"]}[name]
    assert card["mode"] == "steps"
    assert client.post(f"/flow/gallery/{name}/install").status_code == 200
    report = client.get(f"/flow/skills/{name}/guarantees").json()
    assert card["guarantees"] == report["holds"] > 0, (card, report)
    outline = client.get(f"/flow/skills/{name}/outline").json()
    assert card["steps"] == len(outline["steps"]), "a card counts the nodes the Canvas draws"


def test_an_unknown_template_is_a_404_and_never_a_path(client):
    assert client.post("/flow/gallery/greet/install").status_code == 404
    assert client.post("/flow/gallery/..%2F..%2Fbackend/install").status_code in (400, 404)

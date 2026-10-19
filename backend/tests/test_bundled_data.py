"""Regression guard: the bundled widget/workflow catalog must ship INSIDE the
installed package so a plain `pip install aigenos-flow` includes it.

History: the data dir used to live at backend/data/ — a sibling of the package
— so `pip install` (which ships only the `aigenos_flow` package) dropped it
entirely and load_widgets()/load_workflows() silently returned empty. It now
lives at aigenos_flow/data/ and is verified here.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

import aigenos_flow
from aigenos_flow.engine import loader
from aigenos_flow.schemas.widget_spec import WidgetSpec
from aigenos_flow.schemas.workflow import Workflow

PKG_ROOT = Path(aigenos_flow.__file__).resolve().parent


def test_packaged_data_lives_inside_the_package():
    # Must resolve to aigenos_flow/data so it is shipped in the wheel.
    assert loader._PACKAGED_DATA == PKG_ROOT / "data"
    assert loader._PACKAGED_DATA.is_dir(), (
        "bundled data dir missing — it would not ship with pip install"
    )


def test_bundled_widgets_present_and_valid():
    widget_dir = loader._PACKAGED_DATA / "widgets"
    files = sorted(p.name for p in widget_dir.glob("*.json"))
    assert files, "no bundled widgets shipped"
    for p in widget_dir.glob("*.json"):
        WidgetSpec.model_validate(json.loads(p.read_text(encoding="utf-8")))


def test_bundled_workflows_present_and_valid():
    wf_dir = loader._PACKAGED_DATA / "workflows"
    files = sorted(p.name for p in wf_dir.glob("*.yaml"))
    assert files, "no bundled workflows shipped"
    for p in wf_dir.glob("*.yaml"):
        Workflow.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


def test_fg_data_dir_env_override(monkeypatch, tmp_path):
    # FG_DATA_DIR redirects the data dir (so production can use a writable path).
    import importlib

    monkeypatch.setenv("FG_DATA_DIR", str(tmp_path))
    reloaded = importlib.reload(loader)
    try:
        assert reloaded.DATA_DIR == tmp_path
        assert reloaded.WIDGET_DIR == tmp_path / "widgets"
    finally:
        monkeypatch.delenv("FG_DATA_DIR", raising=False)
        importlib.reload(loader)  # restore module-level defaults for other tests

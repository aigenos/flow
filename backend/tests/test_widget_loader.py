import json
from pathlib import Path

import pytest

from aigenos_flow.engine import loader
from aigenos_flow.schemas.widget_spec import WidgetSpec, SpecNode
from aigenos_flow.schemas.workflow import Workflow, WorkflowNode


@pytest.fixture
def tmp_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    widgets = tmp_path / "widgets"
    workflows = tmp_path / "workflows"
    widgets.mkdir()
    workflows.mkdir()
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "WIDGET_DIR", widgets)
    monkeypatch.setattr(loader, "WORKFLOW_DIR", workflows)
    return tmp_path


def test_widget_save_load_roundtrip(tmp_data: Path):
    spec = WidgetSpec(
        id="test_card",
        version="1.0.0",
        spec=SpecNode(type="card", props={"title": "Hi"}, children=[]),
    )
    loader.save_widget(spec)

    loaded = loader.load_widgets()
    assert "test_card" in loaded
    assert loaded["test_card"].spec.props["title"] == "Hi"


def test_widget_delete(tmp_data: Path):
    spec = WidgetSpec(id="goner", spec=SpecNode(type="card"))
    loader.save_widget(spec)
    assert loader.delete_widget("goner") is True
    assert loader.delete_widget("goner") is False


def test_workflow_save_load_roundtrip(tmp_data: Path):
    wf = Workflow(
        id="wf1",
        start="a",
        nodes=[
            WorkflowNode(id="a", type="message", text="hi", next="b"),
            WorkflowNode(id="b", type="end"),
        ],
    )
    loader.save_workflow(wf)

    loaded = loader.load_workflows()
    assert "wf1" in loaded
    assert loaded["wf1"].start == "a"
    assert len(loaded["wf1"].nodes) == 2


def test_save_rejects_unsafe_id(tmp_data: Path):
    spec = WidgetSpec(id="../escape", spec=SpecNode(type="card"))
    with pytest.raises(ValueError):
        loader.save_widget(spec)

"""Filesystem loader + writer for widgets and workflows."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

from ..schemas.widget_spec import WidgetSpec
from ..schemas.workflow import Workflow


# Bundled defaults ship INSIDE the package (aigenos_flow/data) so a plain
# `pip install aigenos-flow` includes the widget catalog + example workflows.
# Set FG_DATA_DIR to redirect reads/writes to a writable, persistent location
# in production (recommended if you use the runtime widget/skill-creation APIs,
# since the packaged dir may be read-only).
_PACKAGED_DATA = Path(__file__).resolve().parent.parent / "data"
DATA_DIR = (
    Path(os.environ["FG_DATA_DIR"]).expanduser()
    if os.environ.get("FG_DATA_DIR")
    else _PACKAGED_DATA
)
WIDGET_DIR = DATA_DIR / "widgets"
WORKFLOW_DIR = DATA_DIR / "workflows"

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_\-.]{1,80}$")


def _check_id(value: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise ValueError(f"Invalid id (allowed: letters, digits, _-.): {value!r}")


def load_widgets() -> dict[str, WidgetSpec]:
    WIDGET_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, WidgetSpec] = {}
    for p in WIDGET_DIR.glob("*.json"):
        spec = WidgetSpec.model_validate(json.loads(p.read_text(encoding="utf-8")))
        out[spec.id] = spec
    return out


def load_workflows() -> dict[str, Workflow]:
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, Workflow] = {}
    for p in WORKFLOW_DIR.glob("*.yaml"):
        wf = Workflow.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        out[wf.id] = wf
    return out


def save_widget(spec: WidgetSpec) -> None:
    _check_id(spec.id)
    WIDGET_DIR.mkdir(parents=True, exist_ok=True)
    target = WIDGET_DIR / f"{spec.id}.json"
    target.write_text(
        json.dumps(spec.model_dump(exclude_none=True), indent=2),
        encoding="utf-8",
    )


def delete_widget(widget_id: str) -> bool:
    _check_id(widget_id)
    target = WIDGET_DIR / f"{widget_id}.json"
    if target.exists():
        target.unlink()
        return True
    return False


def save_workflow(wf: Workflow) -> None:
    _check_id(wf.id)
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    target = WORKFLOW_DIR / f"{wf.id}.yaml"
    target.write_text(
        yaml.safe_dump(wf.model_dump(exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )


def delete_workflow(workflow_id: str) -> bool:
    _check_id(workflow_id)
    target = WORKFLOW_DIR / f"{workflow_id}.yaml"
    if target.exists():
        target.unlink()
        return True
    return False

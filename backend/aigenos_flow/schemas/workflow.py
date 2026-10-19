"""Workflow YAML schema."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


NodeType = Literal["widget", "message", "tool_call", "branch", "end"]


class WorkflowNode(BaseModel):
    id: str
    type: NodeType
    # widget node
    widget: str | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    save_to: str | None = None
    # message node
    text: str | None = None
    # tool_call node
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    # branch node
    branches: list[dict[str, Any]] = Field(default_factory=list)
    # routing
    next: str | None = None


class Workflow(BaseModel):
    id: str
    description: str = ""
    start: str
    nodes: list[WorkflowNode]

    def node_map(self) -> dict[str, WorkflowNode]:
        return {n.id: n for n in self.nodes}

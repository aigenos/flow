"""Declarative widget spec schema.

A widget is a tree of primitives. Each primitive has a `type`, optional `props`,
and optional `children`. Bindings (`{{var}}`) inside string props are resolved
against workflow state before the spec is shipped to the client.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SpecNode(BaseModel):
    type: str
    props: dict[str, Any] = Field(default_factory=dict)
    children: list["SpecNode"] = Field(default_factory=list)


SpecNode.model_rebuild()


class WidgetSpec(BaseModel):
    id: str
    version: str = "1.0.0"
    description: str = ""
    bindings: list[str] = Field(default_factory=list)
    submit_schema: dict[str, Any] = Field(default_factory=dict)
    spec: SpecNode

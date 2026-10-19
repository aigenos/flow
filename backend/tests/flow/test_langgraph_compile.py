"""Smoke test: FlowAgent.as_langgraph() must actually compile.

This catches mistakes like inner-scoped type annotations that break
LangGraph's get_type_hints() introspection of route functions.
"""
from __future__ import annotations

import pytest

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.test_host import TestHost


def test_as_langgraph_compiles(skills_dir):
    """If this test passes, the LangGraph graph definition is at least
    well-formed enough that all annotations can be resolved."""
    agent = FlowAgent.from_dir(skills_dir, host=TestHost())
    graph = agent.as_langgraph()
    assert graph is not None
    # Verify the basic graph shape
    schema = graph.get_graph()
    node_names = {n.id for n in schema.nodes.values()}
    assert "turn" in node_names
    assert "wait" in node_names


def test_as_langgraph_handles_checkpointer(skills_dir):
    """A host without a checkpointer attribute must still compile."""
    host = TestHost()
    assert not hasattr(host, "checkpointer")  # TestHost has none
    agent = FlowAgent.from_dir(skills_dir, host=host)
    # Should not raise even without a checkpointer.
    graph = agent.as_langgraph()
    assert graph is not None

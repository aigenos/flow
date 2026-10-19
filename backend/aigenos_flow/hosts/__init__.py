"""Host adapters.

Each module exposes a Host class that satisfies the `aigenos_flow.Host` protocol.
LangGraph is the one host that ships (``langgraph_host``). ``TestHost`` stands in
for the model in tests.
"""
from .test_host import TestHost

__all__ = ["TestHost"]

"""The Host protocol — every agent-framework integration implements this.

Keep this surface small. Adding a method here is a public-API change that
every host must implement; resist the urge.

Contract:

  - llm_structured() is how the executor reaches the model for free-form
    JSON / Pydantic responses (flex-mode interpreter).

  - llm_tool_call() is how the executor reaches the model in strict mode.
    The host invokes the provider's native tool-calling with the supplied
    tool schemas and returns the resulting tool calls. Strict mode collapses
    the LLM's output space to "pick a tool, fill typed args" — the schema
    physically prevents invalid field names, invalid enum values, etc.

  - supports_tool_use is a capability flag. Strict mode only runs when this
    is True; otherwise the runtime silently falls back to flex. Defaults to
    True for hosts where tool use is reliable (Anthropic, OpenAI); host
    implementations should set it to False for providers/models known to be
    flaky at tool calling (small local models, etc.).

  - emit() is how the executor surfaces assistant messages. The host queues
    them to be returned to the user once the turn completes.

  - now() exists only so tests can pin time deterministically.
"""
from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class ToolCall(BaseModel):
    """One tool invocation the LLM asked for."""

    name: str
    args: dict[str, Any]


class Host(Protocol):
    """Framework-agnostic glue. Each host is a thin shim over a specific agent
    framework (LangGraph, CrewAI, ADK, etc.) and over a specific LLM provider.
    """

    supports_tool_use: bool

    def llm_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
    ) -> T:
        """Call the LLM and parse the response into `schema`. Implementations
        should use the provider's native structured-output mechanism (tool
        calling, JSON mode, etc.) rather than parsing free text."""
        ...

    def llm_tool_call(
        self,
        *,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        tool_choice: str = "any",
    ) -> list[ToolCall]:
        """Call the LLM with a constrained tool set. ``tool_choice="any"``
        forces the model to call at least one tool. Returns the list of tool
        calls the model produced (zero or more). Implementations use the
        provider's native tool-use API (Anthropic tool_use, OpenAI tools).
        Raises NotImplementedError if the host does not support tool use."""
        ...

    def emit(self, text: str) -> None:
        """Queue an assistant message for the user. The host buffers these;
        the executor doesn't care how they're flushed."""
        ...

    def drain_pending_messages(self) -> list[str]:
        """Pop and return any messages queued by emit() since the last call.
        Used by tests; production hosts typically also call this between
        graph nodes to flush to the wire."""
        ...

    def now(self) -> float:
        """Wall-clock seconds. Override in tests."""
        ...

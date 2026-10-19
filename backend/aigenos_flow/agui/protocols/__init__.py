"""Protocol adapters.

Backend speaks AG-UI internally; adapters translate AG-UI events into other
emerging UI protocols (A2UI, MCP-UI). They produce SSE payloads in the target
protocol's shape.
"""
from __future__ import annotations

from typing import AsyncIterator

from ..events import AnyEvent
from ..stream import event_to_sse
from .a2ui import to_a2ui_sse
from .mcp_ui import to_mcp_ui_sse


SUPPORTED = ("ag-ui", "a2ui", "mcp-ui")


async def emit(
    protocol: str,
    events: AsyncIterator[AnyEvent],
    *,
    resume_url: str | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Translate the internal AG-UI event stream into ``protocol``'s SSE frames.

    ``resume_url`` is the absolute path a client POSTs a widget submission to.
    MCP-UI embeds it in each widget resource so an MCP host has a resolvable
    submit target; the other two protocols resume through the same endpoint the
    client already POSTed to, so they do not need it.
    """
    proto = (protocol or "ag-ui").lower()
    if proto == "a2ui":
        async for sse in to_a2ui_sse(events):
            yield sse
    elif proto == "mcp-ui":
        async for sse in to_mcp_ui_sse(events, resume_url=resume_url):
            yield sse
    else:
        async for ev in events:
            yield event_to_sse(ev)

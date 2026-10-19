"""Client side: the trip skill's MCP server as tools a flow can be granted.

``aigenos_flow.mcp_tools.MCPTools`` does the work: it starts the server as a
subprocess, keeps one session open, and hands back one callable per tool. Each
callable says what it takes (from the tool's input schema) and whether it only
reads (from its ``readOnlyHint``), so the same SKILL.md runs against it
unchanged.

    from mcp_tools import TravelMCP

    mcp = TravelMCP()
    agent = create_agent(llm, "skills/", tools=mcp.tools())   # or wrap_agent / FlowAgent.from_dir
    ...
    mcp.close()

A tool passed in ``tools=`` wins over the function of the same name in the
skill's scripts.py, so the SKILL.md does not change at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

from aigenos_flow.mcp_tools import MCPTools

SERVER = Path(__file__).resolve().parents[2] / "skills" / "conference-trip" / "mcp_server.py"

#: The tools the skill's steps name. `list_bookings` is extra, for proofs.
FLOW_TOOLS = (
    "search_flights", "check_arrival", "find_hotels", "check_hotel", "quote_cab", "quote_trip",
    "verify_approval", "build_itinerary", "hold_flight", "hold_hotel", "reserve_cab", "take_payment",
)


class TravelMCP(MCPTools):
    """The trip server's twelve tools, and its ledger for the proof to read."""

    def __init__(self, server: Path = SERVER, env: dict[str, str] | None = None) -> None:
        super().__init__([sys.executable, str(server)], env=env, only=FLOW_TOOLS)

    def bookings(self) -> list[dict]:
        found = self.call("list_bookings")["bookings"]
        self.calls.pop()        # the proof's own question, not a call the flow made
        return found

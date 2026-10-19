"""SSE serialization for AG-UI events."""
from __future__ import annotations

import json
from typing import AsyncIterator

from .events import AnyEvent


def event_to_sse(event: AnyEvent) -> dict[str, str]:
    payload = event.model_dump(mode="json", exclude_none=True)
    return {"event": event.type, "data": json.dumps(payload)}


async def to_sse_stream(events: AsyncIterator[AnyEvent]) -> AsyncIterator[dict[str, str]]:
    async for ev in events:
        yield event_to_sse(ev)

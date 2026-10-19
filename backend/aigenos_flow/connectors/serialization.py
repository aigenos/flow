"""Bounded JSON-safe result normalization shared by database providers."""
from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import decimal
import json
import uuid
from typing import Any


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (uuid.UUID, decimal.Decimal)):
        return str(value)
    if isinstance(value, bytes):
        return {"$binary": base64.b64encode(value).decode("ascii")}
    if dataclasses.is_dataclass(value):
        return json_safe(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    # BSON ObjectId and provider-specific scalar wrappers are stable as strings.
    return str(value)


def bound_rows(rows: list[Any], byte_limit: int) -> tuple[list[Any], bool]:
    """Return the largest row prefix whose serialized envelope stays bounded."""
    out: list[Any] = []
    used = 2  # []
    truncated = False
    for row in rows:
        safe = json_safe(row)
        size = len(json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if used + size + (1 if out else 0) > byte_limit:
            truncated = True
            break
        out.append(safe)
        used += size + (1 if out else 0)
    return out, truncated

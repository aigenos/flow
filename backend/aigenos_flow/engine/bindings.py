"""Safe `{{var.path}}` binding resolution against a state dict."""
from __future__ import annotations

import re
from typing import Any

_BINDING = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


def _lookup(state: dict[str, Any], path: str) -> Any:
    cur: Any = state
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def resolve(value: Any, state: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def sub(m: re.Match[str]) -> str:
            v = _lookup(state, m.group(1))
            return "" if v is None else str(v)
        if _BINDING.fullmatch(value.strip()):
            return _lookup(state, _BINDING.fullmatch(value.strip()).group(1))
        return _BINDING.sub(sub, value)
    if isinstance(value, dict):
        return {k: resolve(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, state) for v in value]
    return value

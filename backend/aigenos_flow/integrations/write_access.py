"""The write-surface policy as a FastAPI dependency (needs the ``[server]`` extra).

``make_flow_router`` puts this on every route that writes a skill or hands back
its source, so the ``FG_SKILL_WRITES`` policy holds wherever the router is
mounted. The bundled server's middleware applies the same policy earlier; this
is what protects an application that includes the router in its own app. A
skill carries Python the process imports, so without it that application would
serve remote code execution to anyone who can reach the port.

It lives in its own module, not inside ``make_flow_router``, because FastAPI
resolves the ``Request`` annotation from the function's module globals.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from ..write_guard import refusal


def require_write_access(request: Request) -> None:
    refused = refusal(
        request.method,
        request.url.path,
        request.client.host if request.client else None,
        request.headers,
    )
    if refused is not None:
        raise HTTPException(refused[0], refused[1])

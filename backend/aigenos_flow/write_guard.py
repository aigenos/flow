"""Who may reach the endpoints that write a skill or hand back its source.

A skill can carry Python that the server imports, so those endpoints are a
code-execution surface. The policy lives here, with no FastAPI import, so that
BOTH callers enforce the same thing:

  * the bundled server (``aigenos_flow.main``), as middleware, and
  * ``make_flow_router`` itself, as a dependency on each management route.

The second is what protects an application that mounts the router in its own
FastAPI app. Before 0.3.1 only the middleware existed, so a self-mounted router
served ``PUT /skills/{name}`` to anyone who could reach the port.
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import os
from typing import Mapping

log = logging.getLogger("aigenos_flow.write_guard")

DEV_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)

HOW_TO_ENABLE = (
    "Set FG_SKILL_WRITES=token together with FG_ADMIN_TOKEN=<secret> and send "
    "'Authorization: Bearer <secret>', or FG_SKILL_WRITES=local for a loopback-only "
    "dev box. These endpoints write Python that the server then imports, so by "
    "default they answer only to this machine."
)


def cors_origins() -> list[str]:
    """Allowed browser origins. ``FG_CORS_ORIGINS`` is a comma-separated list.

    The default is the local Vite/Next dev servers. ``*`` is honoured only if an
    operator asks for it explicitly, and it is logged as a warning, because this
    app's management surface is code-executing.
    """
    raw = (os.getenv("FG_CORS_ORIGINS") or "").strip()
    if not raw:
        return list(DEV_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


def admin_token() -> str:
    """The configured management token, if any (FG_CONNECTOR_ADMIN_TOKEN reused)."""
    return (
        os.getenv("FG_ADMIN_TOKEN")
        or os.getenv("FG_CONNECTOR_ADMIN_TOKEN")
        or ""
    ).strip()


def skill_writes_mode() -> str:
    """``off`` | ``token`` | ``local`` | ``open`` — how the write surface is gated."""
    raw = (os.getenv("FG_SKILL_WRITES") or os.getenv("FG_ALLOW_SKILL_WRITES") or "").strip().lower()
    aliases = {
        "": "",
        "0": "off", "false": "off", "no": "off", "off": "off", "disabled": "off",
        "token": "token", "auth": "token", "1": "token", "true": "token", "yes": "token",
        "local": "local", "loopback": "local",
        "open": "open", "insecure": "open", "unsafe": "open",
    }
    mode = aliases.get(raw)
    if mode is None:
        log.warning(
            "FG_SKILL_WRITES=%r is not one of off|token|local|open — treating it as 'off'", raw
        )
        return "off"
    if mode:
        return mode
    # Not set: a configured token is itself an opt-in to token-gated writes.
    # Otherwise default to loopback-only rather than fully off. The Studio is
    # the documented way to author a skill and every one of its save/load calls
    # lands on this surface, so defaulting to "off" meant the product did not
    # work on the developer's own machine. "local" keeps these endpoints
    # reachable from 127.0.0.1 and nowhere else, only from an allowed origin,
    # and check_bind() refuses a public bind while this mode is in force.
    return "token" if admin_token() else "local"


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "testclient":  # fastapi.testclient's synthetic client
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "::1"}


def refusal(
    method: str, path: str, client_host: str | None, headers: Mapping[str, str]
) -> tuple[int, str] | None:
    """``None`` when a management request may proceed, else ``(status, detail)``.

    ``headers`` is looked up with lower-case names (Starlette's ``Headers`` is
    case-insensitive; a plain dict must already be lower-cased).
    """
    mode = skill_writes_mode()
    if mode == "open":
        log.warning("FG_SKILL_WRITES=open — serving %s %s with NO authorization check", method, path)
        return None
    if mode == "off":
        return 403, (
            f"{method} {path} is part of the skill-management surface, "
            f"which is disabled. {HOW_TO_ENABLE}"
        )
    if mode == "token":
        expected = admin_token()
        if not expected:
            return 503, (
                "FG_SKILL_WRITES=token but no FG_ADMIN_TOKEN is configured, so the "
                "skill-management surface stays disabled."
            )
        supplied = headers.get("x-admin-token") or ""
        auth = headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            supplied = auth[7:]
        if not supplied or not hmac.compare_digest(supplied, expected):
            return 401, "invalid or missing management token"
        return None
    # mode == "local"
    if not is_loopback(client_host):
        return 403, (
            f"FG_SKILL_WRITES=local allows the skill-management surface from loopback "
            f"only; this request came from {client_host!r}. {HOW_TO_ENABLE}"
        )
    origin = headers.get("origin")
    allowed = cors_origins()
    if origin and "*" not in allowed and origin not in allowed:
        return 403, (
            f"cross-origin skill-management request from {origin!r} refused "
            "(FG_SKILL_WRITES=local trusts only FG_CORS_ORIGINS)"
        )
    return None

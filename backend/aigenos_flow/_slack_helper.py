"""Tiny Slack incoming-webhook helper used by generated skill actions.

Why this lives in the engine: the skill_generator emits handlers.py files
whose `@action` stubs call ``post_to_slack(...)`` when ``SLACK_WEBHOOK_URL``
is set in the environment. Generated actions degrade gracefully — without
the env var they just return their fake_result, so demos still work offline.

Usage in a generated action::

    @action
    def submit_refund(state):
        info = post_to_slack(
            "Refund request",
            fields={"order_id": state["fields"]["order_id"], "amount": state["fields"]["amount"]},
        )
        # info["posted"] is True if a webhook was hit, False otherwise.
        return {"refund_id": "RFD-4242", "status": "pending", "slack": info}
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib import error, request

log = logging.getLogger("aigenos_flow.slack")


def post_to_slack(
    title: str,
    *,
    fields: dict[str, Any] | None = None,
    webhook_url: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Post a formatted message to a Slack incoming webhook.

    Args:
        title: Bolded header line (e.g. "New refund request").
        fields: Optional key/value pairs rendered as a bulleted block under the title.
        webhook_url: Override the SLACK_WEBHOOK_URL env var.
        timeout: HTTP timeout in seconds.

    Returns:
        ``{"posted": bool, "status": int | None, "error": str | None}``.
        Never raises — silent skip when the webhook isn't configured so the
        demo continues working without any setup.
    """
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        log.info("[slack] SLACK_WEBHOOK_URL not set — skipping post")
        return {"posted": False, "status": None, "error": "SLACK_WEBHOOK_URL not set"}

    # Slack Block Kit payload — renders nicely in the channel.
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title, "emoji": True},
        }
    ]
    if fields:
        field_lines = "\n".join(f"• *{k}:* {v}" for k, v in fields.items())
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": field_lines},
            }
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "_Posted by an aigenos/flow skill_"},
            ],
        }
    )

    payload = json.dumps({"text": title, "blocks": blocks}).encode("utf-8")
    req = request.Request(
        url, data=payload, headers={"content-type": "application/json"}
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            log.info("[slack] posted %r → status=%s", title, status)
            return {"posted": True, "status": status, "error": None}
    except error.HTTPError as exc:
        log.warning("[slack] post failed: HTTP %s", exc.code)
        return {"posted": False, "status": exc.code, "error": f"HTTP {exc.code}"}
    except error.URLError as exc:
        log.warning("[slack] post failed: %s", exc.reason)
        return {"posted": False, "status": None, "error": str(exc.reason)}
    except Exception as exc:  # noqa: BLE001 — never raise to skill code
        log.exception("[slack] unexpected error")
        return {"posted": False, "status": None, "error": str(exc)}

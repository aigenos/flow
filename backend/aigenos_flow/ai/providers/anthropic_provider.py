"""Anthropic / Claude provider."""
from __future__ import annotations

import os

import anthropic

from ..prompts import WIDGET_SYSTEM_PROMPT


DEFAULT_MODEL = "claude-sonnet-5-5"


def is_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def model_name() -> str:
    return os.environ.get("FLOW_ANTHROPIC_MODEL", DEFAULT_MODEL)


_client: anthropic.Anthropic | None = None


def _client_singleton() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def generate_widget_text(user_prompt: str) -> str:
    response = _client_singleton().messages.create(
        model=model_name(),
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=[
            {
                "type": "text",
                "text": WIDGET_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = ""
    for block in response.content:
        if block.type == "text":
            text += block.text
    return text

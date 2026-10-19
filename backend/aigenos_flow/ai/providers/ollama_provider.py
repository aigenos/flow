"""Ollama (local LLM) provider.

Uses /api/chat with `format: "json"` to force valid JSON output. No SDK
dependency — talks to the Ollama HTTP API directly via httpx (already pulled
in by FastAPI / anthropic).
"""
from __future__ import annotations

import os

import httpx

from ..prompts import WIDGET_SYSTEM_PROMPT


DEFAULT_HOST = "http://localhost:11434"


# Preference order when OLLAMA_MODEL is not set explicitly: pick the most
# capable installed model for JSON generation.
PREFERRED = ("codellama:7b-instruct", "codellama:7b", "llama3.1:8b", "llama3.2:3b", "llama3.2", "llama3.1")


def host() -> str:
    return os.environ.get("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")


def model_name() -> str:
    explicit = os.environ.get("OLLAMA_MODEL")
    if explicit:
        return explicit
    installed = list_models()
    for name in PREFERRED:
        if name in installed:
            return name
    return installed[0] if installed else "llama3.2"


def is_configured() -> bool:
    """True if Ollama is reachable at the configured host."""
    try:
        r = httpx.get(f"{host()}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def list_models() -> list[str]:
    try:
        r = httpx.get(f"{host()}/api/tags", timeout=2.0)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


def generate_widget_text(user_prompt: str) -> str:
    r = httpx.post(
        f"{host()}/api/chat",
        timeout=120.0,
        json={
            "model": model_name(),
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": WIDGET_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "options": {"temperature": 0.4},
        },
    )
    r.raise_for_status()
    data = r.json()
    return data.get("message", {}).get("content", "")

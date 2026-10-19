"""Provider-agnostic NL → widget spec.

Selection order, from most-explicit to most-implicit:
1. `FLOW_AI_PROVIDER` env var, if set to a known provider.
2. Anthropic, if `ANTHROPIC_API_KEY` is set.
3. Ollama, if reachable at `OLLAMA_HOST` (default localhost:11434).
4. Otherwise, raise `ProviderUnavailable` — UI surfaces this as a clear error.
"""
from __future__ import annotations

import json
import os
from typing import Any

from pydantic import ValidationError

from ..schemas.widget_spec import WidgetSpec
from .providers import anthropic_provider, ollama_provider


class ProviderUnavailable(RuntimeError):
    pass


def available_providers() -> dict[str, Any]:
    return {
        "anthropic": {
            "available": anthropic_provider.is_configured(),
            "model": anthropic_provider.model_name(),
        },
        "ollama": {
            "available": ollama_provider.is_configured(),
            "model": ollama_provider.model_name(),
            "host": ollama_provider.host(),
            "installed_models": ollama_provider.list_models() if ollama_provider.is_configured() else [],
        },
    }


def select_provider() -> str:
    explicit = (os.environ.get("FLOW_AI_PROVIDER") or "").strip().lower()
    if explicit in {"anthropic", "ollama"}:
        return explicit
    if anthropic_provider.is_configured():
        return "anthropic"
    if ollama_provider.is_configured():
        return "ollama"
    raise ProviderUnavailable(
        "No AI provider configured. Set ANTHROPIC_API_KEY, or run Ollama locally "
        "(default http://localhost:11434) and pull a model (e.g. `ollama pull llama3.2`)."
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[-1].startswith("```"):
            lines = lines[1:-1]
        else:
            lines = lines[1:]
        text = "\n".join(lines).strip()
    return text


def generate_widget(prompt: str, suggested_id: str | None = None) -> dict[str, Any]:
    provider = select_provider()
    user_msg = prompt.strip()
    if suggested_id:
        user_msg += f"\n\nUse `{suggested_id}` as the widget id."

    if provider == "anthropic":
        text = anthropic_provider.generate_widget_text(user_msg)
        model_used = anthropic_provider.model_name()
    elif provider == "ollama":
        text = ollama_provider.generate_widget_text(user_msg)
        model_used = ollama_provider.model_name()
    else:
        raise ProviderUnavailable(f"Unknown provider: {provider}")

    text = _strip_fences(text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{provider}/{model_used} did not return valid JSON: {exc}. Raw: {text[:500]}"
        ) from exc

    try:
        spec = WidgetSpec.model_validate(parsed)
    except ValidationError as exc:
        raise ValueError(f"Generated widget failed schema validation ({provider}/{model_used}): {exc}") from exc

    return {
        "widget": spec.model_dump(exclude_none=True),
        "provider": provider,
        "model": model_used,
    }

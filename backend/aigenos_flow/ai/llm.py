"""Chat-model resolution with an actionable error when the provider is missing.

The provider packages are optional extras. When the one a configured key
needs is missing, the error names it and the extra that installs it, instead
of an HTTP 500 raised from deep inside a router:

    >>> resolve_chat_model()                      # doctest: +SKIP
    MissingLLMProvider: OPENAI_API_KEY is set, but the OpenAI provider is not
    installed. Install it with:  pip install "aigenos-flow[openai]"

:class:`MissingLLMProvider` carries the pip ``extra`` that fixes it, so an HTTP
layer can answer 503 with the exact command instead of a stack trace.
"""
from __future__ import annotations

import os
from typing import Any

__all__ = ["MissingLLMProvider", "PROVIDERS", "provider_for_env", "resolve_chat_model"]


class MissingLLMProvider(RuntimeError):
    """A provider was selected by the environment but is not installed.

    Attributes:
        provider: Short provider id — ``anthropic``/``openai``/``gemini``/``ollama``.
        extra: The pip extra that installs it, e.g. ``ollama``.
        distribution: The distribution name the extra pulls in.
        module: The Python module that could not be imported.
    """

    def __init__(
        self,
        provider: str,
        *,
        extra: str,
        distribution: str,
        module: str,
        reason: str,
    ) -> None:
        self.provider = provider
        self.extra = extra
        self.distribution = distribution
        self.module = module
        super().__init__(
            f"{reason} Install it with:  pip install \"aigenos-flow[{extra}]\"  "
            f"(or: pip install {distribution}). "
            f"Missing module: {module}."
        )


# provider id -> (module imported, pip extra, distribution, what selects it)
PROVIDERS: dict[str, tuple[str, str, str, str]] = {
    "anthropic": ("langchain_anthropic", "anthropic", "langchain-anthropic", "ANTHROPIC_API_KEY"),
    "openai": ("langchain_openai", "openai", "langchain-openai", "OPENAI_API_KEY"),
    "gemini": (
        "langchain_google_genai",
        "gemini",
        "langchain-google-genai",
        "GOOGLE_API_KEY / GEMINI_API_KEY",
    ),
    "ollama": ("langchain_ollama", "ollama", "langchain-ollama", "no API key (local default)"),
}


def provider_for_env(env: dict[str, str] | None = None) -> str:
    """Which provider the environment selects, without importing anything.

    Mirrors the auto-detect order used by the server: Anthropic > OpenAI >
    Gemini > local Ollama.
    """
    e = os.environ if env is None else env
    if e.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if e.get("OPENAI_API_KEY"):
        return "openai"
    if e.get("GOOGLE_API_KEY") or e.get("GEMINI_API_KEY"):
        return "gemini"
    return "ollama"


def missing_provider_error(provider: str, module: str) -> MissingLLMProvider:
    """Build the actionable error for a provider whose import failed."""
    mod, extra, dist, selector = PROVIDERS[provider]
    if provider == "ollama":
        reason = (
            "No LLM provider is installed and no provider API key is set, so the "
            "local Ollama provider was selected."
        )
    else:
        reason = (
            f"{selector} is set, but the {provider} provider is not installed."
        )
    return MissingLLMProvider(
        provider, extra=extra, distribution=dist, module=module or mod, reason=reason
    )


def _import(provider: str) -> Any:
    """Import a provider's module, translating ImportError into our error."""
    module = PROVIDERS[provider][0]
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as exc:
        raise missing_provider_error(provider, getattr(exc, "name", "") or module) from None


def resolve_chat_model(*, model: str | None = None, temperature: float = 0) -> Any:
    """Build the auto-detected LangChain chat model, or raise MissingLLMProvider."""
    provider = provider_for_env()
    mod = _import(provider)
    if provider == "anthropic":
        return mod.ChatAnthropic(
            model=model or "claude-sonnet-5-5", temperature=temperature
        )
    if provider == "openai":
        kwargs: dict[str, Any] = {"model": model or "gpt-4o", "temperature": temperature}
        if os.getenv("OPENAI_BASE_URL"):
            kwargs["base_url"] = os.getenv("OPENAI_BASE_URL")
        return mod.ChatOpenAI(**kwargs)
    if provider == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if api_key:
            os.environ["GOOGLE_API_KEY"] = api_key
        return mod.ChatGoogleGenerativeAI(
            model=model or "gemini-3.8-flash", temperature=temperature
        )
    return mod.ChatOllama(model=model or "llama3.1:8b", temperature=temperature)


def describe_import_error(exc: BaseException) -> MissingLLMProvider | None:
    """If ``exc`` is a missing LangChain provider module, name the pip extra.

    Lets an HTTP layer translate the raw ``ModuleNotFoundError`` that escapes
    an un-migrated call site into a 503 with an actionable message.
    """
    if not isinstance(exc, ImportError):
        return None
    name = (getattr(exc, "name", "") or "").split(".")[0]
    for provider, (module, _extra, _dist, _sel) in PROVIDERS.items():
        if name == module:
            return missing_provider_error(provider, name)
    return None

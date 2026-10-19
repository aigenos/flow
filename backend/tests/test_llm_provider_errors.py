"""A missing optional LLM provider must name the pip extra that fixes it.

Filed as pip-fresh-1: ``pip install "aigenos-flow[server]"`` shipped no
LangChain provider, so ``POST /chat`` answered HTTP 500 with the body
"Internal Server Error" and a server-side traceback ending in
``ModuleNotFoundError: No module named 'langchain_ollama'``. Unguessable.
"""
from __future__ import annotations

import builtins

import pytest

from aigenos_flow.ai.llm import (
    MissingLLMProvider,
    PROVIDERS,
    describe_import_error,
    missing_provider_error,
    provider_for_env,
    resolve_chat_model,
)

PROVIDER_ENV = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
)


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)


def test_env_selects_providers_in_the_documented_order(monkeypatch):
    assert provider_for_env({}) == "ollama"
    assert provider_for_env({"GEMINI_API_KEY": "k"}) == "gemini"
    assert provider_for_env({"GOOGLE_API_KEY": "k"}) == "gemini"
    assert provider_for_env({"OPENAI_API_KEY": "k", "GEMINI_API_KEY": "k"}) == "openai"
    assert provider_for_env(
        {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"}
    ) == "anthropic"
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert provider_for_env() == "openai"


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_every_error_names_the_extra_and_the_pip_command(provider):
    error = missing_provider_error(provider, PROVIDERS[provider][0])
    text = str(error)
    assert f'pip install "aigenos-flow[{error.extra}]"' in text
    assert error.distribution in text
    assert PROVIDERS[provider][0] in text
    assert isinstance(error, RuntimeError)


def test_the_keyless_default_says_so():
    error = missing_provider_error("ollama", "langchain_ollama")
    assert "no provider API key is set" in str(error)
    assert 'pip install "aigenos-flow[ollama]"' in str(error)


def test_a_key_without_its_provider_says_which_key(monkeypatch):
    error = missing_provider_error("openai", "langchain_openai")
    assert "OPENAI_API_KEY is set" in str(error)


def test_resolve_chat_model_translates_a_missing_module(monkeypatch):
    """The exact reported failure: no provider installed, no key set."""
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("langchain_ollama"):
            raise ModuleNotFoundError(
                "No module named 'langchain_ollama'", name="langchain_ollama"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(MissingLLMProvider) as exc:
        resolve_chat_model()
    assert exc.value.extra == "ollama"
    assert exc.value.provider == "ollama"


def test_describe_import_error_maps_every_provider_module():
    for provider, (module, extra, _dist, _sel) in PROVIDERS.items():
        described = describe_import_error(
            ModuleNotFoundError(f"No module named {module!r}", name=module)
        )
        assert described is not None, module
        assert described.provider == provider
        assert described.extra == extra


def test_describe_import_error_maps_a_submodule_too():
    described = describe_import_error(
        ModuleNotFoundError("boom", name="langchain_anthropic.chat_models")
    )
    assert described is not None and described.extra == "anthropic"


def test_describe_import_error_ignores_anything_else():
    assert describe_import_error(ModuleNotFoundError("x", name="numpy")) is None
    assert describe_import_error(ValueError("x")) is None


def test_resolve_chat_model_builds_a_model_when_the_provider_is_present(monkeypatch):
    """Positive control, so the test above is not passing for the wrong reason."""
    pytest.importorskip("langchain_ollama")
    model = resolve_chat_model(model="llama3.1:8b")
    assert model.__class__.__name__ == "ChatOllama"

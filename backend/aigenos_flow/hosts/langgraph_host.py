"""LangGraph host — the v0.1 production host.

Wraps:

  - any LangChain BaseChatModel for structured LLM calls (Anthropic / OpenAI /
    Ollama all work via different provider quirks; see llm_structured below)
  - LangGraph's `interrupt()` for pause-on-collect
  - LangGraph's checkpointer for state persistence

Usage::

    from langchain_anthropic import ChatAnthropic
    from langgraph.checkpoint.memory import MemorySaver

    from aigenos_flow import FlowAgent
    from aigenos_flow.hosts.langgraph_host import LangGraphHost

    host = LangGraphHost(
        llm=ChatAnthropic(model="claude-sonnet-5-5"),
        checkpointer=MemorySaver(),
    )
    agent = FlowAgent.from_dir("skills/", host=host)
    graph = agent.as_langgraph()
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypeVar

from pydantic import BaseModel

from ..host import ToolCall

_log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LangGraphHost:
    """A `aigenos_flow.Host` implementation backed by LangChain + LangGraph.

    We don't take a hard dependency on a specific provider — pass any
    LangChain `BaseChatModel`. Structured output uses a two-strategy approach
    so the same code works against models with different capabilities:

      1. Try the provider's native `with_structured_output(schema)`. This is
         the fastest and most reliable path for Anthropic and OpenAI (tool
         calling / JSON schema mode).

      2. If that throws (Ollama rejects our discriminated-union schema with
         "invalid JSON schema in format"; some local llama.cpp builds reject
         `oneOf`), fall back to embedding the JSON schema in the system
         prompt + binding `format="json"` and parsing the response with
         Pydantic ourselves. Less efficient but provider-agnostic.
    """

    def __init__(
        self,
        llm: Any,  # langchain_core.language_models.BaseChatModel
        checkpointer: Any | None = None,
        supports_tool_use: bool | None = None,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 2.0,
    ):
        self.llm = llm
        self.checkpointer = checkpointer
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._pending: list[str] = []
        self._pending_widgets: list[dict[str, Any]] = []
        # Cache the result of the strategy probe so we don't retry the
        # failing path on every turn.
        self._fallback_locked: bool = False
        # Strict-mode capability flag. Auto-detect from the LLM class +
        # model name when the caller didn't say. Overridable so users with
        # exotic setups (custom Ollama tags, finetunes) can force it.
        self.supports_tool_use = (
            supports_tool_use
            if supports_tool_use is not None
            else _detect_tool_use_support(llm)
        )

    # -------------------------------------------------------------- llm
    def llm_structured(self, *, system: str, user: str, schema: type[T]) -> T:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        if not self._fallback_locked:
            try:
                model = self.llm.with_structured_output(schema)
                return self._call_with_retry(model.invoke, messages)  # type: ignore[return-value]
            except Exception as native_exc:
                # Common cause: Ollama can't validate Pydantic's
                # `discriminator`/`oneOf` JSON schema. Lock the fallback so
                # subsequent calls skip the failing path.
                self._fallback_locked = True
                self._native_failure_reason = repr(native_exc)

        return self._structured_via_prompt(messages, schema)

    def _structured_via_prompt(
        self,
        messages: list[dict[str, str]],
        schema: type[T],
    ) -> T:
        """Provider-agnostic fallback: schema description in prompt + JSON mode.

        Schemas can opt into a hand-crafted, model-friendly description via a
        classmethod `describe_for_llm() -> str`. We prefer that over Pydantic's
        raw JSON schema because small models often regurgitate `$ref`/`$defs`
        notation literally when they see it in the prompt. The hand-crafted
        version uses concrete examples and TypeScript-ish syntax instead.
        """
        if hasattr(schema, "describe_for_llm"):
            schema_desc = schema.describe_for_llm()  # type: ignore[attr-defined]
        else:
            schema_json = json.dumps(schema.model_json_schema(), indent=2)
            schema_desc = (
                "Respond with a SINGLE JSON object matching this schema.\n"
                "No prose, no markdown fences, no commentary — JSON only.\n\n"
                f"```json\n{schema_json}\n```"
            )

        augmented = list(messages)
        augmented[0] = {
            "role": "system",
            "content": (
                f"{messages[0]['content']}\n\n"
                f"# Output format\n\n{schema_desc}"
            ),
        }

        # Hint to Ollama (and llama.cpp-style backends) that we want valid
        # JSON via the `format="json"` request field. Newer providers reject
        # unknown kwargs at request-build time (notably langchain_google_genai
        # >= 2.0 raises pydantic ValidationError for `format`), so we attempt
        # the hinted call first and silently retry without the hint if the
        # provider doesn't accept it.
        try:
            response = self.llm.bind(format="json").invoke(augmented)
        except Exception as bind_exc:
            if "format" in str(bind_exc).lower() or "extra_forbidden" in str(bind_exc):
                response = self.llm.invoke(augmented)
            else:
                raise
        content = getattr(response, "content", str(response))
        if isinstance(content, list):
            # Some providers (Anthropic) wrap text in a content-block list.
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        content = _extract_json(str(content).strip())

        # Strict parse first.
        try:
            return schema.model_validate_json(content)
        except Exception as parse_exc:
            # Forgiving recovery: only used for TurnInterpretation today
            # since it's the only schema the host has reason to be lenient
            # about. If a weak model emitted some valid items mixed with
            # garbage (`$ref` blobs etc.), keep the valid ones and drop the
            # rest. An empty interpretation is also a valid outcome: the
            # runtime will re-ask on the next turn.
            from ..interpret import TurnInterpretation

            if schema is TurnInterpretation:
                recovered = _recover_turn_interpretation(content)
                if recovered is not None:
                    _log.warning(
                        "interpretation parse failed; recovered %d item(s) "
                        "from partial output (%s)",
                        len(recovered.items), parse_exc,
                    )
                    return recovered  # type: ignore[return-value]
                _log.warning(
                    "interpretation parse failed; no items recovered — "
                    "re-asking user (%s). Raw (truncated): %.300s",
                    parse_exc, content,
                )
                self.emit(
                    "Sorry — I had trouble understanding that. Could you rephrase?"
                )
                return TurnInterpretation(items=[])  # type: ignore[return-value]

            raise RuntimeError(
                "LLM structured-output failed. The model returned content "
                "that didn't validate against the requested schema. This is "
                "often a sign that the model is too small to follow the "
                "structured-output prompt reliably; try a larger model.\n\n"
                f"Schema: {schema.__name__}\n"
                f"Raw response (truncated): {content[:500]}\n"
                f"Parse error: {parse_exc}"
            ) from parse_exc

    # ---------------------------------------------------------- tool_call
    def llm_tool_call(
        self,
        *,
        system: str,
        user: str,
        tools: list[dict[str, Any]],
        tool_choice: str = "any",
    ) -> list[ToolCall]:
        """Strict-mode interpretation path. Binds the supplied tools to the
        LLM with ``tool_choice="any"`` so the model MUST call at least one
        tool, then returns the calls as ``ToolCall`` records.

        Implementation notes:
          - LangChain's ``bind_tools`` accepts the JSON-schema dict shape we
            pass (provider adapters normalize to Anthropic/OpenAI/Ollama
            native formats internally).
          - ``tool_choice`` semantics vary by provider; LangChain accepts
            the string ``"any"`` and translates. We pass it through.
          - The returned AIMessage's ``tool_calls`` is the normalized
            cross-provider list — `[{"name": str, "args": dict, "id": str}]`.
        """
        if not self.supports_tool_use:
            raise NotImplementedError(
                "This host's underlying LLM does not support reliable "
                "tool calling. Use mode='flex' or pass "
                "supports_tool_use=True to override."
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        try:
            bound = self.llm.bind_tools(tools, tool_choice=tool_choice)
        except TypeError:
            # Some adapters don't accept tool_choice — bind without it. The
            # prompt already pressures the model to call a tool.
            bound = self.llm.bind_tools(tools)

        response = self._call_with_retry(bound.invoke, messages)
        raw_calls = getattr(response, "tool_calls", None) or []
        return [
            ToolCall(name=c.get("name", ""), args=c.get("args") or {})
            for c in raw_calls
            if c.get("name")
        ]

    # ---------------------------------------------------------- plain text
    def llm_text(self, *, system: str, user: str) -> str:
        """One unstructured completion — used by the chat leaf to phrase a
        natural-language answer over a tool result."""
        response = self.llm.invoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
        content = getattr(response, "content", str(response))
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return str(content).strip()

    # -------------------------------------------------------------- emit
    def emit(self, text: str) -> None:
        self._pending.append(text)

    def emit_widget(self, spec: dict[str, Any]) -> None:
        """Queue a display-only widget spec (ctx.show / chat results). The app
        layer drains these alongside pending messages and renders them with
        the @aigenos/flow SpecRenderer."""
        self._pending_widgets.append(spec)

    def drain_pending_messages(self) -> list[str]:
        out, self._pending = self._pending, []
        return out

    def drain_pending_widgets(self) -> list[dict[str, Any]]:
        out, self._pending_widgets = self._pending_widgets, []
        return out
    def _call_with_retry(self, fn, *args, **kwargs):
        """Call *fn* with exponential-backoff retry on transient failures."""
        import logging
        _retry_log = logging.getLogger("aigenos_flow.retry")
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                err_str = str(exc).lower()
                is_transient = any(
                    k in err_str
                    for k in ("timeout", "rate", "429", "503", "overloaded", "connection", "retry")
                )
                if not is_transient and attempt == 0:
                    raise  # non-transient errors fail immediately
                wait = min(self.backoff_factor ** attempt, 30.0)
                _retry_log.warning(
                    "LLM call failed (attempt %d/%d): %s. Retrying in %.1fs...",
                    attempt + 1, self.max_retries, exc, wait,
                )
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    def now(self) -> float:
        return time.time()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_tool_use_support(llm: Any) -> bool:
    """Best-effort default for ``supports_tool_use``. Sensible per-provider
    defaults so users don't have to remember which Ollama tag handles tool
    calling well.

    Anthropic, OpenAI: yes (native tool use, reliable).
    Ollama: depends on the underlying model. Modern 14B+ Qwen / Mistral /
    Hermes-tuned models work; 7-8B Llama variants are flaky and default to
    no — aigenos flow will fall back to flex mode rather than misbehave. Users
    can override with ``supports_tool_use=True``.
    Anything else: assume yes — the caller can override if wrong.
    """
    cls = type(llm).__name__
    if cls in (
        "ChatAnthropic", "AnthropicChat",
        "ChatOpenAI",
        "ChatGoogleGenerativeAI", "ChatVertexAI",
    ):
        return True
    if cls in ("ChatOllama", "OllamaLLM", "Ollama"):
        model = (getattr(llm, "model", "") or "").lower()
        # Models known to handle tool calling well at small sizes.
        if "qwen2.5" in model or "qwen3" in model:
            return True
        if "hermes" in model or "command-r" in model or "firefunction" in model:
            return True
        # Small Llama 3.1 / 3.2 are flaky; only trust 70B+.
        if "llama3" in model or "llama-3" in model:
            return any(tag in model for tag in ("70b", "405b"))
        # Mistral mid-size is OK; small is hit-or-miss.
        if "mistral" in model:
            return any(tag in model for tag in ("large", "8x", "nemo", "small"))
        return False
    # Unknown class — trust it; user can override.
    return True


_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(?P<body>.*?)\n```\s*$", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
# Reasoning models (qwen3, deepseek-r1, etc.) prepend a <think>...</think>
# block. Its prose often contains JSON-looking fragments ({"field": ...}) that
# the greedy object regex below would wrongly latch onto, so strip it first.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> str:
    """Best-effort: pull the JSON body out of a response that may include
    reasoning blocks, accidental code fences, or trailing prose."""
    text = _THINK_BLOCK_RE.sub("", text).strip()
    m = _CODE_FENCE_RE.match(text)
    if m:
        return _normalize_duplicate_json_keys(m.group("body").strip())
    # If the model emitted prose then a JSON object, grab the object.
    m = _JSON_OBJECT_RE.search(text)
    if m:
        return _normalize_duplicate_json_keys(m.group(0))
    return _normalize_duplicate_json_keys(text)


def _merge_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Pairs hook: on a duplicated key, concatenate list values, otherwise
    keep the first non-empty value. Standard parsers keep the LAST value,
    which for a duplicated '"items": [...] ... "items": []' silently
    discards the model's real answer."""
    merged: dict[str, Any] = {}
    for k, v in pairs:
        if k in merged:
            prev = merged[k]
            if isinstance(prev, list) and isinstance(v, list):
                merged[k] = prev + v
            elif not prev and v:
                merged[k] = v
            # else: first non-empty value stands
        else:
            merged[k] = v
    return merged


def _normalize_duplicate_json_keys(content: str) -> str:
    """Weak models sometimes emit a JSON object with a duplicated key —
    observed verbatim from qwen3:8b under format=json:

        {"items": [<correct interpretation>], "items": []}

    ``json.loads`` and Pydantic both apply last-key-wins, so the empty
    duplicate would silently replace the model's actual answer. Re-parse
    with a merging pairs hook and re-serialize canonically; return the
    input unchanged when it isn't parseable JSON."""
    try:
        return json.dumps(json.loads(content, object_pairs_hook=_merge_duplicate_keys))
    except Exception:
        return content


def _recover_turn_interpretation(content: str) -> Any:
    """Recover as much of a TurnInterpretation as possible from a partially-
    malformed response. Returns a TurnInterpretation if any recovery
    succeeds, else None.

    Useful when a weak model emits a mix of valid items and garbage — e.g.
    ``[{"type": "SetField", "field": "amount", "value": 100}, {"$ref": "..."}]``.
    """
    from ..interpret import TurnInterpretation  # local: avoid circular

    try:
        data = json.loads(content)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    # Accept either the new "items" key or the legacy "commands" key.
    raw_items = data.get("items")
    if raw_items is None:
        raw_items = data.get("commands")
    if not isinstance(raw_items, list):
        return TurnInterpretation(items=[])

    valid: list[Any] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if "$ref" in item or "type" not in item:
            continue
        # Back-compat: translate legacy ``SetSlot``/``slot`` shape into the
        # new ``SetField``/``field`` shape on the fly.
        if item.get("type") == "SetSlot":
            item = {"type": "SetField", **{k: v for k, v in item.items() if k != "type"}}
        if "slot" in item and "field" not in item:
            item = {**item, "field": item["slot"]}
            item.pop("slot", None)
        try:
            one = TurnInterpretation.model_validate({"items": [item]})
            valid.extend(one.items)
        except Exception:
            continue

    return TurnInterpretation(items=valid)

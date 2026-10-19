"""TestHost — a deterministic host for unit/integration tests.

No LLM, no framework. You pre-program command lists keyed by user message;
the host returns them when the executor asks for structured output. This is
what makes the test harness deterministic and fast.

Usage:

    host = TestHost()
    host.queue_commands(
        "I want to file an expense for $100 at Whole Foods, food",
        SetField(field="amount", value=100),
        SetField(field="merchant", value="Whole Foods"),
        SetField(field="category", value="food"),
    )
    ...
"""
from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ..host import ToolCall
from ..interpret import SkillChoice, Turn, TurnInterpretation

T = TypeVar("T", bound=BaseModel)


class TestHost:
    # pytest sees the `Test*` prefix and tries to collect this class as a
    # test container. Disable collection — it's a host implementation, not
    # a test fixture.
    __test__ = False

    def __init__(self) -> None:
        # Maps user-message string -> the TurnInterpretation the LLM "would" return.
        self._scripted: dict[str, TurnInterpretation] = {}
        # Maps user-message string -> scripted tool calls (strict-mode tests).
        self._scripted_tool_calls: dict[str, list[ToolCall]] = {}
        # Maps user-message string -> the skill-classifier verdict ("" = None).
        self._classified: dict[str, str | None] = {}
        # Maps schema-class-name -> override response (advanced).
        self._schema_overrides: dict[str, BaseModel] = {}
        # Maps substring-of-user-prompt -> scripted llm_text answer (chat leaf).
        self._chat_answers: dict[str, str] = {}
        self._pending: list[str] = []
        self._pending_widgets: list[dict] = []
        self._clock: float = 0.0
        # Strict assertion mode: raise on unscripted messages instead of
        # returning empty. (Distinct from the runtime's mode="strict".)
        self.strict = True
        # Capability flag — TestHost happily fakes tool calling for tests.
        self.supports_tool_use = True

    # ---------- programming the host ----------

    def queue_commands(self, user_message: str, *items: Turn) -> None:
        """Pre-script: when the interpreter sees ``user_message``, return
        these items. Tests build a stack of expected exchanges with this."""
        self._scripted[user_message] = TurnInterpretation(items=list(items))

    # Back-compat alias for tests written before the rename.
    queue_items = queue_commands

    def queue_tool_calls(self, user_message: str, *calls: ToolCall) -> None:
        """Pre-script strict-mode interpretation: when the interpreter calls
        ``llm_tool_call`` for ``user_message``, return these tool calls.
        Construct calls like ``ToolCall(name="set_room", args={"value":
        "redwood"})``."""
        self._scripted_tool_calls[user_message] = list(calls)

    def queue_classification(self, user_message: str, skill: str | None) -> None:
        """Pre-script the first-turn skill classifier for a specific
        message. ``skill=None`` means the classifier says no skill matches
        (the runtime falls through to single-skill default or ReAct)."""
        self._classified[user_message] = skill

    def set_schema_override(self, schema: type[BaseModel], response: BaseModel) -> None:
        """Force the host to always return ``response`` for calls expecting
        ``schema``. Useful for non-interpreter LLM calls."""
        self._schema_overrides[schema.__name__] = response

    def queue_chat_answer(self, contains: str, answer: str) -> None:
        """Pre-script ``llm_text``: any prompt containing ``contains`` returns
        ``answer``. Used to script the chat leaf's natural-language replies."""
        self._chat_answers[contains] = answer

    def set_time(self, t: float) -> None:
        self._clock = t

    # ---------- Host protocol ----------

    def llm_structured(self, *, system: str, user: str, schema: type[T]) -> T:
        if schema.__name__ in self._schema_overrides:
            return self._schema_overrides[schema.__name__]  # type: ignore[return-value]

        if schema is TurnInterpretation:
            user_message = self._parse_user_message(user)
            if user_message in self._scripted:
                return self._scripted[user_message]  # type: ignore[return-value]
            if self.strict:
                raise AssertionError(
                    f"TestHost has no scripted items for user message: "
                    f"{user_message!r}\n"
                    f"Scripted messages: {list(self._scripted)}"
                )
            return TurnInterpretation(items=[])  # type: ignore[return-value]

        if schema is SkillChoice:
            user_message = self._parse_user_message_for_classifier(user)
            if user_message in self._classified:
                pick = self._classified[user_message]
                return SkillChoice(skill=pick, reason="")  # type: ignore[return-value]
            # Default: no classification. The runtime falls through to its
            # single-skill default (when registry has one skill) or asks
            # the user to rephrase.
            return SkillChoice(skill=None, reason="unscripted")  # type: ignore[return-value]

        raise NotImplementedError(
            f"TestHost.llm_structured does not handle schema {schema!r}; "
            f"use set_schema_override() to script it."
        )

    def llm_tool_call(
        self,
        *,
        system: str,
        user: str,
        tools: list,
        tool_choice: str = "any",
    ) -> list[ToolCall]:
        user_message = self._parse_user_message_strict(user)
        if user_message in self._scripted_tool_calls:
            return self._scripted_tool_calls[user_message]
        if self.strict:
            raise AssertionError(
                f"TestHost has no scripted tool calls for: {user_message!r}\n"
                f"Scripted tool-call messages: {list(self._scripted_tool_calls)}"
            )
        return []

    def llm_text(self, *, system: str, user: str) -> str:
        # Captured for assertions (e.g. "the chat context: made it into the
        # system prompt") — tests read it, production hosts don't have it.
        self.last_llm_text_system = system
        for key, answer in self._chat_answers.items():
            if key in user:
                return answer
        if self.strict:
            raise AssertionError(
                f"TestHost has no scripted chat answer matching: {user[:120]!r}\n"
                f"Scripted substrings: {list(self._chat_answers)}"
            )
        return ""

    def emit(self, text: str) -> None:
        self._pending.append(text)

    def emit_widget(self, spec: dict) -> None:
        self._pending_widgets.append(spec)

    def drain_pending_messages(self) -> list[str]:
        out, self._pending = self._pending, []
        return out

    def drain_pending_widgets(self) -> list[dict]:
        out, self._pending_widgets = self._pending_widgets, []
        return out

    def now(self) -> float:
        return self._clock

    # ---------- helpers ----------

    @staticmethod
    def _parse_user_message(user_prompt: str) -> str:
        """The interpreter's user prompt has the message between two specific
        markers. Pull the literal user message out."""
        lines = [line.rstrip() for line in user_prompt.splitlines()]
        try:
            i = lines.index("The user just said:")
        except ValueError:
            return user_prompt.strip()
        body = []
        for line in lines[i + 1 :]:
            if line.strip() == "":
                if body:
                    break
                continue
            if line.startswith("Interpret.") or line.startswith("Extract commands."):
                break
            body.append(line)
        return "\n".join(body).strip()

    @staticmethod
    def _parse_user_message_strict(user_prompt: str) -> str:
        """Strict-mode user prompt has the same 'The user just said:' marker
        as flex; reuse the parser but trim the trailing tool-call hint."""
        lines = [line.rstrip() for line in user_prompt.splitlines()]
        try:
            i = lines.index("The user just said:")
        except ValueError:
            return user_prompt.strip()
        body: list[str] = []
        for line in lines[i + 1 :]:
            if line.strip() == "":
                if body:
                    break
                continue
            if line.startswith("Interpret by calling"):
                break
            body.append(line)
        return "\n".join(body).strip()

    @staticmethod
    def _parse_user_message_for_classifier(user_prompt: str) -> str:
        """The classifier's user prompt has 'User said:' as the marker."""
        lines = [line.rstrip() for line in user_prompt.splitlines()]
        try:
            i = lines.index("User said:")
        except ValueError:
            return user_prompt.strip()
        body: list[str] = []
        for line in lines[i + 1 :]:
            if line.strip() == "":
                if body:
                    break
                continue
            if line.startswith("Pick one skill"):
                break
            body.append(line)
        return "\n".join(body).strip()

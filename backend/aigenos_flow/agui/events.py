"""AG-UI protocol event types.

Subset of the AG-UI spec sufficient for text streaming + generative-UI tool
calls. Frontend renders widgets by treating TOOL_CALL_* events whose tool name
is `render_widget` as widget render requests.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


def _ts() -> int:
    return int(time.time() * 1000)


def _id() -> str:
    return uuid.uuid4().hex


class BaseEvent(BaseModel):
    type: str
    timestamp: int = Field(default_factory=_ts)
    raw_event: dict[str, Any] | None = None


class RunStarted(BaseEvent):
    type: Literal["RUN_STARTED"] = "RUN_STARTED"
    thread_id: str
    run_id: str


class RunFinished(BaseEvent):
    type: Literal["RUN_FINISHED"] = "RUN_FINISHED"
    thread_id: str
    run_id: str


class RunError(BaseEvent):
    type: Literal["RUN_ERROR"] = "RUN_ERROR"
    message: str
    code: str | None = None


class StepStarted(BaseEvent):
    type: Literal["STEP_STARTED"] = "STEP_STARTED"
    step_name: str


class StepFinished(BaseEvent):
    type: Literal["STEP_FINISHED"] = "STEP_FINISHED"
    step_name: str


class TextMessageStart(BaseEvent):
    type: Literal["TEXT_MESSAGE_START"] = "TEXT_MESSAGE_START"
    message_id: str
    role: Literal["assistant"] = "assistant"


class TextMessageContent(BaseEvent):
    type: Literal["TEXT_MESSAGE_CONTENT"] = "TEXT_MESSAGE_CONTENT"
    message_id: str
    delta: str


class TextMessageEnd(BaseEvent):
    type: Literal["TEXT_MESSAGE_END"] = "TEXT_MESSAGE_END"
    message_id: str


class ToolCallStart(BaseEvent):
    type: Literal["TOOL_CALL_START"] = "TOOL_CALL_START"
    tool_call_id: str
    tool_call_name: str
    parent_message_id: str | None = None


class ToolCallArgs(BaseEvent):
    type: Literal["TOOL_CALL_ARGS"] = "TOOL_CALL_ARGS"
    tool_call_id: str
    delta: str


class ToolCallEnd(BaseEvent):
    type: Literal["TOOL_CALL_END"] = "TOOL_CALL_END"
    tool_call_id: str


class ToolCallResult(BaseEvent):
    type: Literal["TOOL_CALL_RESULT"] = "TOOL_CALL_RESULT"
    tool_call_id: str
    message_id: str
    content: str
    role: Literal["tool"] = "tool"


class StateSnapshot(BaseEvent):
    type: Literal["STATE_SNAPSHOT"] = "STATE_SNAPSHOT"
    snapshot: dict[str, Any]


class StateDelta(BaseEvent):
    type: Literal["STATE_DELTA"] = "STATE_DELTA"
    delta: list[dict[str, Any]]


class CustomEvent(BaseEvent):
    type: Literal["CUSTOM"] = "CUSTOM"
    name: str
    value: dict[str, Any]


AnyEvent = (
    RunStarted | RunFinished | RunError
    | StepStarted | StepFinished
    | TextMessageStart | TextMessageContent | TextMessageEnd
    | ToolCallStart | ToolCallArgs | ToolCallEnd | ToolCallResult
    | StateSnapshot | StateDelta | CustomEvent
)


def new_run_id() -> str:
    return _id()


def new_message_id() -> str:
    return _id()


def new_tool_call_id() -> str:
    return _id()

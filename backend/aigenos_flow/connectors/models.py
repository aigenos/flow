"""Provider-neutral connector contracts and JSON-safe data models."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


ProviderName = Literal["postgresql", "mongodb"]
OperationMode = Literal["read", "write"]


class ParameterSpec(BaseModel):
    type: Literal["string", "integer", "number", "boolean", "object", "array"] = "string"
    required: bool = True
    description: str = ""
    sensitive: bool = False
    enum: list[Any] | None = None
    pattern: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    log_value: bool = False
    """Keep this parameter's VALUE in the audit trail.

    The audit trail redacts by default (see
    :func:`aigenos_flow.connectors.manager.redact_parameters`): only values
    drawn from a closed domain — a declared ``enum``, a boolean, a number — are
    stored verbatim, because a free string can be a card number, an IBAN, an
    email or a date of birth and nothing in its name has to say so. Set this
    when an unconstrained parameter is genuinely safe to keep and you want it in
    the trail. ``sensitive: true`` always wins.
    """


class ManagedOperation(BaseModel):
    id: str
    workspace_id: str = "default"
    connection_id: str
    provider: ProviderName
    name: str
    display_name: str
    tool_name: str
    kind: str
    mode: OperationMode
    definition: dict[str, Any]
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    row_limit: int = Field(default=100, gt=0, le=1000)
    result_limit_bytes: int = Field(default=1_048_576, gt=1024, le=10_485_760)
    enabled: bool = True
    revision: int = 1

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
            raise ValueError("operation name must be snake_case and at most 63 characters")
        return value


class ToolDescriptor(BaseModel):
    name: str
    description: str = ""
    provider: str | None = None
    operation_id: str | None = None
    connection_id: str | None = None
    side_effecting: bool = False
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)


class InvocationContext(BaseModel):
    skill_name: str | None = None
    action_name: str | None = None
    flow_run_id: str | None = None
    idempotency_key: str | None = None


class ConnectorResult(BaseModel):
    rows: list[Any] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    affected_count: int = 0
    inserted_ids: list[str] = Field(default_factory=list)
    execution_id: str


class ConnectorAdapter(ABC):
    provider: ProviderName

    @abstractmethod
    def validate_operation(self, operation: ManagedOperation) -> dict[str, Any]:
        """Validate without contacting the target database."""

    @abstractmethod
    def execute(
        self,
        uri: str,
        operation: ManagedOperation,
        parameters: dict[str, Any],
    ) -> ConnectorResult:
        """Run an already validated operation using separately-bound inputs."""

    @abstractmethod
    def test_connection(self, connection_id: str, uri: str) -> dict[str, Any]:
        """Make the smallest useful round trip to the target."""

    @abstractmethod
    def inspect_schema(
        self,
        connection_id: str,
        uri: str,
        *,
        infer_fields: bool = False,
    ) -> dict[str, Any]:
        """Return metadata, never application row values."""

    @abstractmethod
    def invalidate(self, connection_id: str) -> None:
        """Drop a cached provider client/pool after credential rotation."""

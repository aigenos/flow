"""Connector lifecycle, stable managed tools, parameter validation, and audits."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from ..declarative import ToolRegistry
from ..skills import parse_skill_md
from .models import InvocationContext, ManagedOperation, ParameterSpec, ToolDescriptor
from .mongodb import MongoDBAdapter
from .postgresql import PostgreSQLAdapter
from .secrets import SecretResolver
from .store import ConnectorStore


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SENSITIVE_NAME = re.compile(r"pass|secret|token|credential|api.?key|otp|ssn", re.I)

REDACTED = "***REDACTED***"


def redact_parameters(
    specs: dict[str, ParameterSpec], values: dict[str, Any]
) -> dict[str, Any]:
    """What the audit trail is allowed to keep — a default-DENY allowlist.

    The old rule was the other way round: a value was stored verbatim unless
    its name matched ``pass|secret|token|credential|api.?key|otp|ssn`` or the
    admin had remembered ``sensitive: true``. So ``card_number``, ``cvv``,
    ``iban``, ``email``, ``dob``, ``account_number``, ``home_address`` and
    ``salary`` all landed in ``audit_events.parameters_json`` in cleartext and
    came back out of ``GET /flow/connectors/audits``.

    A value is kept only when its declared type cannot carry a secret:

    * ``sensitive: true`` → redacted, always, no exceptions.
    * a name matching the sensitive-name heuristic → redacted (kept as a belt:
      it catches a mis-declared ``password`` typed as a plain string).
    * a declared ``enum`` → kept. The domain is a closed set the admin wrote.
    * ``boolean`` → kept. One bit, and it is usually the gate the audit is for.
    * ``integer`` / ``number`` → kept. Amounts and ids are the point of an audit
      trail; an admin storing a numeric account number marks it sensitive.
    * anything else (``string``, ``object``, ``array``) → redacted unless the
      admin sets ``log_value: true`` on that parameter.

    A parameter that was not supplied at all is reported as ``None`` rather than
    as the redaction marker, so a reader can tell "a secret was passed" from
    "nothing was passed".
    """
    out: dict[str, Any] = {}
    for key, value in values.items():
        spec = specs.get(key)
        if value is None:
            out[key] = None
            continue
        if spec is None:
            # Undeclared parameter: nothing vouches for it.
            out[key] = REDACTED
            continue
        if spec.sensitive or _SENSITIVE_NAME.search(key):
            out[key] = REDACTED
            continue
        if getattr(spec, "log_value", False) or spec.enum:
            out[key] = value
            continue
        if spec.type in {"boolean", "integer", "number"}:
            out[key] = value
            continue
        out[key] = REDACTED
    return out


def slugify(value: str) -> str:
    slug = _SLUG_RE.sub("_", value.lower()).strip("_")
    if not slug:
        raise ValueError("name must contain a letter or number")
    return slug[:48]


class _ManagedTool:
    def __init__(self, manager: "ConnectorManager", descriptor: ToolDescriptor) -> None:
        self.manager = manager
        self.descriptor = descriptor
        self.name = descriptor.name
        self.description = descriptor.description

    def invoke_with_context(
        self, args: dict[str, Any], context: InvocationContext | None = None
    ) -> dict[str, Any]:
        if context is not None and not isinstance(context, InvocationContext):
            context = InvocationContext.model_validate(context)
        return self.manager.invoke(
            self.descriptor.operation_id or "", args, context=context
        )

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        return self.invoke_with_context(args)


class ConnectorManager:
    def __init__(
        self,
        store: ConnectorStore | None = None,
        resolver: SecretResolver | None = None,
    ) -> None:
        self.store = store or ConnectorStore()
        self.resolver = resolver or SecretResolver()
        self.adapters = {
            "postgresql": PostgreSQLAdapter(),
            "mongodb": MongoDBAdapter(),
        }
        self._lock = threading.RLock()
        self._registry = ToolRegistry()
        self.store.prune_audits()
        self.rebuild_registry()

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    @staticmethod
    def providers() -> list[dict[str, Any]]:
        return [
            {
                "id": "postgresql",
                "label": "PostgreSQL",
                "operation_kinds": ["select", "insert", "update", "delete"],
                "uri_schemes": ["postgresql://", "postgres://"],
            },
            {
                "id": "mongodb",
                "label": "MongoDB",
                "operation_kinds": [
                    "find_one", "find_many", "aggregate", "insert_one", "update_one",
                    "update_many", "delete_one", "delete_many",
                ],
                "uri_schemes": ["mongodb://", "mongodb+srv://"],
            },
        ]

    def descriptor_for(self, operation: ManagedOperation) -> ToolDescriptor:
        return ToolDescriptor(
            name=operation.tool_name,
            description=(
                f"Managed {operation.provider} {operation.kind} operation: {operation.display_name}. "
                "Inputs are validated and bound by the deterministic flow runtime."
            ),
            provider=operation.provider,
            operation_id=operation.id,
            connection_id=operation.connection_id,
            side_effecting=operation.mode == "write",
            parameters=operation.parameters,
        )

    def build_registry(self, operations: list[ManagedOperation] | None = None) -> ToolRegistry:
        registry = ToolRegistry()
        for operation in operations if operations is not None else self.store.list_operations():
            connection = self.store.get_connection(operation.connection_id)
            if not operation.enabled or not connection or not connection["enabled"]:
                continue
            descriptor = self.descriptor_for(operation)
            registry.register(operation.tool_name, _ManagedTool(self, descriptor), descriptor=descriptor)
        return registry

    def rebuild_registry(self) -> ToolRegistry:
        candidate = self.build_registry()
        with self._lock:
            self._registry = candidate
        return candidate

    def candidate_registry(
        self,
        *,
        replacement: ManagedOperation | None = None,
        remove_id: str | None = None,
    ) -> ToolRegistry:
        operations = []
        replaced = False
        for operation in self.store.list_operations():
            if operation.id == remove_id:
                continue
            if replacement and operation.id == replacement.id:
                operations.append(replacement)
                replaced = True
            else:
                operations.append(operation)
        if replacement and not replaced:
            operations.append(replacement)
        return self.build_registry(operations)

    # ------------------------------------------------------------------ connection lifecycle
    def create_connection(self, body: dict[str, Any]) -> dict[str, Any]:
        provider = body.get("provider")
        if provider not in self.adapters:
            raise ValueError(f"unsupported connector provider {provider!r}")
        name = str(body.get("name") or "").strip()
        if not name:
            raise ValueError("connection name is required")
        credential = str(body.get("credential") or "")
        mode, payload = self.resolver.parse_input(credential)
        encrypted = self.resolver.encrypt(payload) if mode == "inline" else None
        connection = self.store.create_connection({
            "id": uuid.uuid4().hex,
            "provider": provider,
            "name": name,
            "slug": slugify(name),
            "access_level": body.get("access_level", "read"),
            "credential_mode": mode,
            "credential_encrypted": encrypted,
            "credential_env": payload if mode == "environment" else None,
            "enabled": body.get("enabled", True),
            "config": body.get("config") or {},
        })
        self.rebuild_registry()
        return connection

    def update_connection(self, connection_id: str, body: dict[str, Any]) -> dict[str, Any]:
        current = self.store.get_connection(connection_id, include_secret=True)
        if current is None:
            raise KeyError(connection_id)
        changes: dict[str, Any] = {}
        if body.get("access_level") == "read" and any(
            operation.mode == "write"
            for operation in self.store.list_operations(connection_id)
        ):
            raise ValueError(
                "remove write operations before changing this connection to read-only"
            )
        for name in ("name", "access_level", "enabled"):
            if name in body:
                changes[name] = body[name]
        if "config" in body:
            changes["config_json"] = json.dumps(body["config"] or {})
        if "credential" in body and body["credential"]:
            mode, payload = self.resolver.parse_input(str(body["credential"]))
            changes.update({
                "credential_mode": mode,
                "credential_encrypted": self.resolver.encrypt(payload) if mode == "inline" else None,
                "credential_env": payload if mode == "environment" else None,
            })
            self.adapters[current["provider"]].invalidate(connection_id)
        updated = self.store.update_connection(connection_id, changes)
        self.rebuild_registry()
        return updated

    def delete_connection(self, connection_id: str) -> None:
        current = self.store.get_connection(connection_id)
        if current is None:
            raise KeyError(connection_id)
        self.store.delete_connection(connection_id)
        self.adapters[current["provider"]].invalidate(connection_id)
        self.rebuild_registry()

    def _uri(self, connection_id: str) -> tuple[dict[str, Any], str]:
        connection = self.store.get_connection(connection_id, include_secret=True)
        if connection is None:
            raise KeyError(connection_id)
        uri = self.resolver.resolve(
            encrypted=connection.get("credential_encrypted"),
            env_reference=connection.get("credential_env"),
        )
        self._validate_uri(connection["provider"], uri)
        return connection, uri

    @staticmethod
    def _validate_uri(provider: str, uri: str) -> None:
        import os

        parsed = urlsplit(uri)
        allowed = {
            "postgresql": {"postgresql", "postgres"},
            "mongodb": {"mongodb", "mongodb+srv"},
        }[provider]
        if parsed.scheme.lower() not in allowed:
            raise ValueError(f"credential URI scheme does not match provider {provider}")
        insecure_dev = os.getenv("FG_CONNECTOR_ALLOW_INSECURE_LOCAL", "").lower() in {"1", "true"}
        query = {key.lower(): values for key, values in parse_qs(parsed.query).items()}
        if provider == "postgresql":
            sslmode = (query.get("sslmode") or [""])[0].lower()
            if sslmode != "verify-full" and not insecure_dev:
                raise ValueError(
                    "PostgreSQL connector URI must set sslmode=verify-full; "
                    "plain local development requires FG_CONNECTOR_ALLOW_INSECURE_LOCAL=true"
                )
        else:
            tls = (query.get("tls") or query.get("ssl") or [""])[0].lower()
            if parsed.scheme.lower() == "mongodb+srv":
                if tls in {"false", "0"} and not insecure_dev:
                    raise ValueError("MongoDB SRV connectors cannot disable verified TLS")
            elif tls not in {"true", "1"} and not insecure_dev:
                raise ValueError(
                    "MongoDB connector URI must set tls=true; plain local development requires "
                    "FG_CONNECTOR_ALLOW_INSECURE_LOCAL=true"
                )

    def test_connection(self, connection_id: str) -> dict[str, Any]:
        connection, uri = self._uri(connection_id)
        return self.adapters[connection["provider"]].test_connection(connection_id, uri)

    def inspect_schema(self, connection_id: str, *, infer_fields: bool = False) -> dict[str, Any]:
        connection, uri = self._uri(connection_id)
        return self.adapters[connection["provider"]].inspect_schema(
            connection_id, uri, infer_fields=infer_fields
        )

    # ------------------------------------------------------------------ operation lifecycle
    def prepare_operation(
        self, connection_id: str, body: dict[str, Any], *, current: ManagedOperation | None = None
    ) -> ManagedOperation:
        connection = self.store.get_connection(connection_id)
        if connection is None:
            raise KeyError(connection_id)
        name = str(body.get("name", current.name if current else "")).strip()
        display_name = str(body.get("display_name", current.display_name if current else name)).strip()
        if not name or not display_name:
            raise ValueError("operation name and display_name are required")
        op_id = current.id if current else uuid.uuid4().hex
        tool_name = current.tool_name if current else f"db__{connection['slug']}__{slugify(name)}"
        data = current.model_dump(mode="python") if current else {}
        data.update(body)
        data.update({
            "id": op_id,
            "connection_id": connection_id,
            "provider": connection["provider"],
            "name": name,
            "display_name": display_name,
            "tool_name": tool_name,
            "definition": body.get("definition", data.get("definition") or {}),
            "parameters": body.get("parameters", data.get("parameters") or {}),
        })
        operation = ManagedOperation.model_validate(data)
        if operation.mode == "write" and connection["access_level"] != "read_write":
            raise ValueError("write operations require a read_write connection")
        self.adapters[operation.provider].validate_operation(operation)
        return operation

    def validate_operation(self, operation: ManagedOperation) -> dict[str, Any]:
        return self.adapters[operation.provider].validate_operation(operation)

    def commit_operation(
        self,
        operation: ManagedOperation,
        *,
        create: bool,
        candidate_registry: ToolRegistry | None = None,
        after_swap: Callable[[], None] | None = None,
    ) -> ManagedOperation:
        """Persist and install a prevalidated catalog under one process lock."""
        with self._lock:
            saved = (
                self.store.create_operation(operation)
                if create else self.store.update_operation(operation)
            )
            self._registry = candidate_registry or self.build_registry()
            if after_swap:
                after_swap()
            return saved

    def delete_operation(
        self,
        operation_id: str,
        *,
        candidate_registry: ToolRegistry | None = None,
        after_swap: Callable[[], None] | None = None,
    ) -> None:
        with self._lock:
            self.store.delete_operation(operation_id)
            self._registry = candidate_registry or self.build_registry()
            if after_swap:
                after_swap()

    # ------------------------------------------------------------------ invocation
    @staticmethod
    def _validate_parameters(
        specs: dict[str, ParameterSpec], parameters: dict[str, Any]
    ) -> dict[str, Any]:
        extra = set(parameters) - set(specs)
        if extra:
            raise ValueError(f"unexpected operation parameters: {sorted(extra)}")
        out: dict[str, Any] = {}
        for name, spec in specs.items():
            if name not in parameters or parameters[name] is None:
                if spec.required:
                    raise ValueError(f"missing required operation parameter {name!r}")
                out[name] = None
                continue
            value = parameters[name]
            if spec.type == "string":
                if not isinstance(value, str):
                    raise ValueError(f"parameter {name!r} must be a string")
                if spec.pattern and re.fullmatch(spec.pattern, value) is None:
                    raise ValueError(f"parameter {name!r} does not match its required pattern")
            elif spec.type == "boolean" and not isinstance(value, bool):
                raise ValueError(f"parameter {name!r} must be boolean")
            elif spec.type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"parameter {name!r} must be an integer")
            elif spec.type == "number" and (
                not isinstance(value, (int, float)) or isinstance(value, bool)
            ):
                raise ValueError(f"parameter {name!r} must be numeric")
            elif spec.type == "object" and not isinstance(value, dict):
                raise ValueError(f"parameter {name!r} must be an object")
            elif spec.type == "array" and not isinstance(value, list):
                raise ValueError(f"parameter {name!r} must be an array")
            if spec.enum is not None and value not in spec.enum:
                raise ValueError(f"parameter {name!r} must be one of {spec.enum!r}")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if spec.minimum is not None and value < spec.minimum:
                    raise ValueError(f"parameter {name!r} is below its minimum")
                if spec.maximum is not None and value > spec.maximum:
                    raise ValueError(f"parameter {name!r} is above its maximum")
            out[name] = value
        return out

    def invoke(
        self,
        operation_id: str,
        parameters: dict[str, Any],
        *,
        context: InvocationContext | None = None,
    ) -> dict[str, Any]:
        operation = self.store.get_operation(operation_id)
        if operation is None or not operation.enabled:
            raise RuntimeError("managed operation is unavailable")
        connection, uri = self._uri(operation.connection_id)
        if not connection["enabled"]:
            raise RuntimeError("managed connection is disabled")
        if operation.mode == "write" and connection["access_level"] != "read_write":
            raise PermissionError("managed write is not allowed by the connection access level")
        validated = self._validate_parameters(operation.parameters, parameters)
        started_dt = datetime.now(timezone.utc)
        started = time.monotonic()
        execution_id = uuid.uuid4().hex
        result = None
        error: Exception | None = None
        try:
            result = self.adapters[operation.provider].execute(uri, operation, validated)
            execution_id = result.execution_id
            return result.model_dump(mode="json")
        except Exception as exc:
            error = exc
            # Provider exceptions can contain a DSN. Do not let them escape to
            # a flow response or traceback/logging surface.
            raise RuntimeError(
                f"managed operation failed ({type(exc).__name__})"
            ) from None
        finally:
            duration = (time.monotonic() - started) * 1000
            redacted = redact_parameters(operation.parameters, validated)
            ctx = context or InvocationContext()
            self.store.record_audit({
                "execution_id": execution_id,
                "connection_id": operation.connection_id,
                "operation_id": operation.id,
                "tool_name": operation.tool_name,
                "skill_name": ctx.skill_name,
                "action_name": ctx.action_name,
                "flow_run_id": ctx.flow_run_id,
                "started_at": started_dt.isoformat(),
                "duration_ms": duration,
                "status": "error" if error else "success",
                "row_count": result.row_count if result else 0,
                "affected_count": result.affected_count if result else 0,
                "truncated": result.truncated if result else False,
                "parameters": redacted,
                "error_code": type(error).__name__ if error else None,
            })

    # ------------------------------------------------------------------ skill references
    def sync_dependencies(self, skills_dir: str | Path) -> list[dict[str, Any]]:
        tool_to_operation = {
            op.tool_name: op.id for op in self.store.list_operations()
        }
        dependencies: list[dict[str, Any]] = []
        root = Path(skills_dir)
        if root.exists():
            for folder in root.iterdir():
                skill_md = folder / "SKILL.md"
                if not skill_md.is_file():
                    continue
                frontmatter, _ = parse_skill_md(skill_md.read_text(encoding="utf-8"), source=str(skill_md))
                for action_name, spec in frontmatter.actions.items():
                    operation_id = tool_to_operation.get(spec.tool)
                    if operation_id:
                        dependencies.append({
                            "operation_id": operation_id,
                            "skill_name": frontmatter.name,
                            "action_name": action_name,
                            "usage": "action",
                        })
                if frontmatter.steps is not None:
                    # The short form names tools in its steps, not in `actions:`.
                    from ..shortform import tools_named

                    for tool_name in sorted(tools_named(frontmatter)):
                        operation_id = tool_to_operation.get(tool_name)
                        if operation_id:
                            dependencies.append({
                                "operation_id": operation_id,
                                "skill_name": frontmatter.name,
                                "action_name": tool_name,
                                "usage": "action",
                            })
                for step in frontmatter.flow or []:
                    for tool_name in (step.get("chat") or {}).get("tools") or []:
                        operation_id = tool_to_operation.get(tool_name)
                        if operation_id:
                            dependencies.append({
                                "operation_id": operation_id,
                                "skill_name": frontmatter.name,
                                "action_name": None,
                                "usage": "chat",
                            })
                for tool_name in (frontmatter.assist or {}).get("tools") or []:
                    operation_id = tool_to_operation.get(tool_name)
                    if operation_id:
                        dependencies.append({
                            "operation_id": operation_id,
                            "skill_name": frontmatter.name,
                            "action_name": None,
                            "usage": "assist",
                        })
        self.store.replace_dependencies(dependencies)
        return dependencies

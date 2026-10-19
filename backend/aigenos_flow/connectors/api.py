"""Authenticated FastAPI management surface for managed connectors."""
from __future__ import annotations

import hmac
import os
import sqlite3
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from ..declarative import ToolRegistry
from .manager import ConnectorManager


class ConnectionCreate(BaseModel):
    provider: str
    name: str
    credential: str = Field(
        description="Complete database URI or env:VARIABLE. It is write-only."
    )
    access_level: str = "read"
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class ConnectionUpdate(BaseModel):
    name: str | None = None
    credential: str | None = Field(default=None, description="Write-only replacement")
    access_level: str | None = None
    enabled: bool | None = None
    config: dict[str, Any] | None = None


class OperationBody(BaseModel):
    name: str
    display_name: str
    kind: str
    mode: str
    definition: dict[str, Any]
    parameters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    timeout_seconds: float = 10
    row_limit: int = 100
    result_limit_bytes: int = 1_048_576
    enabled: bool = True


class OperationTest(BaseModel):
    parameters: dict[str, Any] = Field(default_factory=dict)


def make_connector_router(
    manager: ConnectorManager,
    *,
    validate_candidate: Callable[[ToolRegistry], Any],
    apply_candidate: Callable[[Any, ToolRegistry], None],
    skills_dir: str,
) -> APIRouter:
    router = APIRouter(prefix="/connectors", tags=["connectors"])

    def require_admin(
        authorization: str | None = Header(default=None),
        x_connector_admin_token: str | None = Header(default=None),
    ) -> None:
        expected = os.getenv("FG_CONNECTOR_ADMIN_TOKEN", "")
        if not expected:
            raise HTTPException(503, "connector management is disabled")
        supplied = x_connector_admin_token
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:]
        if not supplied or not hmac.compare_digest(supplied, expected):
            raise HTTPException(401, "invalid or expired connector admin token")

    auth = [Depends(require_admin)]

    def missing(exc: KeyError) -> HTTPException:
        return HTTPException(404, f"connector resource {exc.args[0]!r} not found")

    def conflict(detail: str, dependencies: list[dict[str, Any]]) -> HTTPException:
        return HTTPException(409, {"message": detail, "dependencies": dependencies})

    def operation_json(operation: Any) -> dict[str, Any]:
        result = operation.model_dump(mode="json")
        result["dependencies"] = manager.store.dependencies(operation.id)
        return result

    def refresh_dependencies() -> None:
        manager.sync_dependencies(skills_dir)

    @router.get("/providers", dependencies=auth)
    def providers() -> dict[str, Any]:
        return {"providers": manager.providers()}

    @router.get("", dependencies=auth)
    def list_connections() -> dict[str, Any]:
        return {"connections": manager.store.list_connections()}

    @router.post("", dependencies=auth, status_code=201)
    def create_connection(body: ConnectionCreate) -> dict[str, Any]:
        try:
            return manager.create_connection(body.model_dump())
        except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/catalog", dependencies=auth)
    def catalog() -> dict[str, Any]:
        refresh_dependencies()
        return {
            "tools": [
                {
                    **manager.descriptor_for(operation).model_dump(mode="json"),
                    "display_name": operation.display_name,
                    "kind": operation.kind,
                    "mode": operation.mode,
                    "dependencies": manager.store.dependencies(operation.id),
                }
                for operation in manager.store.list_operations()
                if operation.enabled
            ]
        }

    @router.get("/audits", dependencies=auth)
    def audits(
        operation_id: str | None = None,
        connection_id: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ) -> dict[str, Any]:
        manager.store.prune_audits()
        return {
            "audits": manager.store.list_audits(
                operation_id=operation_id, connection_id=connection_id, limit=limit
            )
        }

    @router.get("/{connection_id}", dependencies=auth)
    def get_connection(connection_id: str) -> dict[str, Any]:
        connection = manager.store.get_connection(connection_id)
        if connection is None:
            raise HTTPException(404, "connection not found")
        connection["operations"] = [
            operation_json(op) for op in manager.store.list_operations(connection_id)
        ]
        return connection

    @router.put("/{connection_id}", dependencies=auth)
    def update_connection(connection_id: str, body: ConnectionUpdate) -> dict[str, Any]:
        try:
            return manager.update_connection(
                connection_id, body.model_dump(exclude_none=True)
            )
        except KeyError as exc:
            raise missing(exc) from exc
        except (ValueError, RuntimeError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/{connection_id}", dependencies=auth)
    def delete_connection(connection_id: str) -> dict[str, Any]:
        try:
            manager.delete_connection(connection_id)
        except KeyError as exc:
            raise missing(exc) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deleted": connection_id}

    @router.post("/{connection_id}/test", dependencies=auth)
    def test_connection(connection_id: str) -> dict[str, Any]:
        try:
            return manager.test_connection(connection_id)
        except KeyError as exc:
            raise missing(exc) from exc
        except Exception as exc:  # never reflect a driver message containing a DSN
            raise HTTPException(422, f"connection test failed ({type(exc).__name__})") from exc

    @router.get("/{connection_id}/schema", dependencies=auth)
    def schema(connection_id: str, infer_fields: bool = False) -> dict[str, Any]:
        try:
            return manager.inspect_schema(connection_id, infer_fields=infer_fields)
        except KeyError as exc:
            raise missing(exc) from exc
        except Exception as exc:
            raise HTTPException(422, f"schema inspection failed ({type(exc).__name__})") from exc

    @router.get("/{connection_id}/operations", dependencies=auth)
    def list_operations(connection_id: str) -> dict[str, Any]:
        if manager.store.get_connection(connection_id) is None:
            raise HTTPException(404, "connection not found")
        refresh_dependencies()
        return {"operations": [
            operation_json(op) for op in manager.store.list_operations(connection_id)
        ]}

    def validate_and_load(operation: Any, *, remove_id: str | None = None) -> tuple[ToolRegistry, Any]:
        candidate_registry = manager.candidate_registry(
            replacement=operation, remove_id=remove_id
        )
        candidate_skills = validate_candidate(candidate_registry)
        return candidate_registry, candidate_skills

    @router.post("/{connection_id}/operations", dependencies=auth, status_code=201)
    def create_operation(connection_id: str, body: OperationBody) -> dict[str, Any]:
        try:
            operation = manager.prepare_operation(connection_id, body.model_dump())
            candidate_registry, candidate_skills = validate_and_load(operation)
            saved = manager.commit_operation(
                operation,
                create=True,
                candidate_registry=candidate_registry,
                after_swap=lambda: apply_candidate(candidate_skills, candidate_registry),
            )
            refresh_dependencies()
            return operation_json(saved)
        except KeyError as exc:
            raise missing(exc) from exc
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, f"operation rejected: {exc}") from exc

    @router.post("/{connection_id}/operations/validate", dependencies=auth)
    def validate_new_operation(connection_id: str, body: OperationBody) -> dict[str, Any]:
        try:
            operation = manager.prepare_operation(connection_id, body.model_dump())
            manager.validate_operation(operation)
            return {"valid": True, "tool_name": operation.tool_name}
        except KeyError as exc:
            raise missing(exc) from exc
        except ValueError as exc:
            return {"valid": False, "error": str(exc)}

    @router.get("/operations/{operation_id}", dependencies=auth)
    def get_operation(operation_id: str) -> dict[str, Any]:
        operation = manager.store.get_operation(operation_id)
        if operation is None:
            raise HTTPException(404, "operation not found")
        refresh_dependencies()
        return operation_json(operation)

    @router.put("/operations/{operation_id}", dependencies=auth)
    def update_operation(operation_id: str, body: OperationBody) -> dict[str, Any]:
        current = manager.store.get_operation(operation_id)
        if current is None:
            raise HTTPException(404, "operation not found")
        refresh_dependencies()
        dependencies = manager.store.dependencies(operation_id)
        if not body.enabled and current.enabled and dependencies:
            raise conflict("referenced operation cannot be disabled", dependencies)
        try:
            operation = manager.prepare_operation(
                current.connection_id, body.model_dump(), current=current
            )
            candidate_registry, candidate_skills = validate_and_load(operation)
            saved = manager.commit_operation(
                operation,
                create=False,
                candidate_registry=candidate_registry,
                after_swap=lambda: apply_candidate(candidate_skills, candidate_registry),
            )
            refresh_dependencies()
            return operation_json(saved)
        except (ValueError, sqlite3.IntegrityError) as exc:
            raise HTTPException(422, f"operation edit rejected: {exc}") from exc

    @router.delete("/operations/{operation_id}", dependencies=auth)
    def delete_operation(operation_id: str) -> dict[str, Any]:
        operation = manager.store.get_operation(operation_id)
        if operation is None:
            raise HTTPException(404, "operation not found")
        refresh_dependencies()
        dependencies = manager.store.dependencies(operation_id)
        if dependencies:
            raise conflict("referenced operation cannot be deleted", dependencies)
        try:
            candidate_registry, candidate_skills = validate_and_load(None, remove_id=operation_id)
            manager.delete_operation(
                operation_id,
                candidate_registry=candidate_registry,
                after_swap=lambda: apply_candidate(candidate_skills, candidate_registry),
            )
            return {"deleted": operation_id}
        except ValueError as exc:
            raise HTTPException(422, f"operation deletion rejected: {exc}") from exc

    @router.post("/operations/{operation_id}/validate", dependencies=auth)
    def validate_existing_operation(operation_id: str) -> dict[str, Any]:
        operation = manager.store.get_operation(operation_id)
        if operation is None:
            raise HTTPException(404, "operation not found")
        try:
            return manager.validate_operation(operation)
        except ValueError as exc:
            return {"valid": False, "error": str(exc)}

    @router.post("/operations/{operation_id}/test", dependencies=auth)
    def test_operation(operation_id: str, body: OperationTest) -> dict[str, Any]:
        operation = manager.store.get_operation(operation_id)
        if operation is None:
            raise HTTPException(404, "operation not found")
        if operation.mode == "write":
            # Admin previews never mutate target data.
            try:
                validation = manager.validate_operation(operation)
                return {**validation, "executed": False, "reason": "write operations validate only"}
            except ValueError as exc:
                return {"valid": False, "executed": False, "error": str(exc)}
        try:
            return {
                "valid": True,
                "executed": True,
                "result": manager.invoke(operation_id, body.parameters),
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422, f"read preview failed ({type(exc).__name__})") from exc

    @router.get("/operations/{operation_id}/dependencies", dependencies=auth)
    def operation_dependencies(operation_id: str) -> dict[str, Any]:
        if manager.store.get_operation(operation_id) is None:
            raise HTTPException(404, "operation not found")
        refresh_dependencies()
        return {"dependencies": manager.store.dependencies(operation_id)}

    return router

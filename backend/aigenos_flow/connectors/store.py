"""SQLite control plane for managed connectors.

The database contains configuration and audit metadata only. PostgreSQL and
MongoDB application data always stays in the external target systems.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import ManagedOperation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConnectorStore:
    workspace_id = "default"

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("FG_CONNECTOR_STORE_PATH")
        if configured is None:
            configured = (
                "/app/data/connectors.sqlite3"
                if Path("/app").is_dir()
                else str(Path.cwd() / "data" / "connectors.sqlite3")
            )
        self.path = Path(configured).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self.transaction() as db:
            version = int(db.execute("PRAGMA user_version").fetchone()[0])
            if version < 1:
                db.executescript(
                    """
                    CREATE TABLE connections (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL DEFAULT 'default',
                        provider TEXT NOT NULL CHECK(provider IN ('postgresql','mongodb')),
                        name TEXT NOT NULL,
                        slug TEXT NOT NULL,
                        access_level TEXT NOT NULL CHECK(access_level IN ('read','read_write')),
                        credential_mode TEXT NOT NULL CHECK(credential_mode IN ('inline','environment')),
                        credential_encrypted TEXT,
                        credential_env TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        config_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(workspace_id, slug)
                    );
                    CREATE TABLE operations (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL DEFAULT 'default',
                        connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE RESTRICT,
                        provider TEXT NOT NULL,
                        name TEXT NOT NULL,
                        display_name TEXT NOT NULL,
                        tool_name TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK(mode IN ('read','write')),
                        definition_json TEXT NOT NULL,
                        parameters_json TEXT NOT NULL DEFAULT '{}',
                        timeout_seconds REAL NOT NULL DEFAULT 10,
                        row_limit INTEGER NOT NULL DEFAULT 100,
                        result_limit_bytes INTEGER NOT NULL DEFAULT 1048576,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        revision INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(connection_id, name)
                    );
                    CREATE TABLE operation_revisions (
                        operation_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        snapshot_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(operation_id, revision)
                    );
                    CREATE TABLE dependencies (
                        operation_id TEXT NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
                        skill_name TEXT NOT NULL,
                        action_name TEXT,
                        usage TEXT NOT NULL,
                        PRIMARY KEY(operation_id, skill_name, action_name, usage)
                    );
                    CREATE TABLE audit_events (
                        execution_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL DEFAULT 'default',
                        connection_id TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        skill_name TEXT,
                        action_name TEXT,
                        flow_run_id TEXT,
                        started_at TEXT NOT NULL,
                        duration_ms REAL NOT NULL,
                        status TEXT NOT NULL,
                        row_count INTEGER NOT NULL DEFAULT 0,
                        affected_count INTEGER NOT NULL DEFAULT 0,
                        truncated INTEGER NOT NULL DEFAULT 0,
                        parameters_json TEXT NOT NULL DEFAULT '{}',
                        error_code TEXT
                    );
                    CREATE INDEX idx_operations_connection ON operations(connection_id);
                    CREATE INDEX idx_audit_started ON audit_events(started_at DESC);
                    CREATE INDEX idx_dependencies_operation ON dependencies(operation_id);
                    PRAGMA user_version=1;
                    """
                )

    # ------------------------------------------------------------------ connections
    def list_connections(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM connections WHERE workspace_id=? ORDER BY name", (self.workspace_id,)
            ).fetchall()
        return [self._public_connection(row) for row in rows]

    def get_connection(self, connection_id: str, *, include_secret: bool = False) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM connections WHERE id=? AND workspace_id=?",
                (connection_id, self.workspace_id),
            ).fetchone()
        if row is None:
            return None
        result = self._public_connection(row)
        if include_secret:
            result["credential_encrypted"] = row["credential_encrypted"]
            result["credential_env"] = row["credential_env"]
        return result

    @staticmethod
    def _public_connection(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "provider": row["provider"],
            "name": row["name"],
            "slug": row["slug"],
            "access_level": row["access_level"],
            "credential_mode": row["credential_mode"],
            "credential_configured": bool(row["credential_encrypted"] or row["credential_env"]),
            "enabled": bool(row["enabled"]),
            "config": json.loads(row["config_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_connection(self, record: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        with self.transaction() as db:
            db.execute(
                """INSERT INTO connections
                (id, workspace_id, provider, name, slug, access_level, credential_mode,
                 credential_encrypted, credential_env, enabled, config_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record["id"], self.workspace_id, record["provider"], record["name"],
                    record["slug"], record.get("access_level", "read"), record["credential_mode"],
                    record.get("credential_encrypted"), record.get("credential_env"),
                    int(record.get("enabled", True)), json.dumps(record.get("config") or {}), now, now,
                ),
            )
        return self.get_connection(record["id"])  # type: ignore[return-value]

    def update_connection(self, connection_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name", "slug", "access_level", "credential_mode", "credential_encrypted",
            "credential_env", "enabled", "config_json",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            assignments.append(f"{key}=?")
            values.append(int(value) if key == "enabled" else value)
        if not assignments:
            current = self.get_connection(connection_id)
            if current is None:
                raise KeyError(connection_id)
            return current
        assignments.append("updated_at=?")
        values.extend([_now(), connection_id, self.workspace_id])
        with self.transaction() as db:
            cur = db.execute(
                f"UPDATE connections SET {', '.join(assignments)} WHERE id=? AND workspace_id=?",
                values,
            )
            if cur.rowcount != 1:
                raise KeyError(connection_id)
        return self.get_connection(connection_id)  # type: ignore[return-value]

    def delete_connection(self, connection_id: str) -> None:
        with self.transaction() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM operations WHERE connection_id=?", (connection_id,)
            ).fetchone()[0]
            if count:
                raise ValueError("connection still has operations")
            cur = db.execute(
                "DELETE FROM connections WHERE id=? AND workspace_id=?",
                (connection_id, self.workspace_id),
            )
            if cur.rowcount != 1:
                raise KeyError(connection_id)

    # ------------------------------------------------------------------ operations
    def list_operations(self, connection_id: str | None = None) -> list[ManagedOperation]:
        sql = "SELECT * FROM operations WHERE workspace_id=?"
        args: list[Any] = [self.workspace_id]
        if connection_id:
            sql += " AND connection_id=?"
            args.append(connection_id)
        sql += " ORDER BY display_name"
        with self._connect() as db:
            rows = db.execute(sql, args).fetchall()
        return [self._operation(row) for row in rows]

    def get_operation(self, operation_id: str) -> ManagedOperation | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM operations WHERE id=? AND workspace_id=?",
                (operation_id, self.workspace_id),
            ).fetchone()
        return self._operation(row) if row else None

    @staticmethod
    def _operation(row: sqlite3.Row) -> ManagedOperation:
        return ManagedOperation.model_validate({
            "id": row["id"], "workspace_id": row["workspace_id"],
            "connection_id": row["connection_id"], "provider": row["provider"],
            "name": row["name"], "display_name": row["display_name"],
            "tool_name": row["tool_name"], "kind": row["kind"], "mode": row["mode"],
            "definition": json.loads(row["definition_json"]),
            "parameters": json.loads(row["parameters_json"]),
            "timeout_seconds": row["timeout_seconds"], "row_limit": row["row_limit"],
            "result_limit_bytes": row["result_limit_bytes"], "enabled": bool(row["enabled"]),
            "revision": row["revision"],
        })

    def create_operation(self, operation: ManagedOperation) -> ManagedOperation:
        now = _now()
        snapshot = operation.model_dump(mode="json")
        with self.transaction() as db:
            db.execute(
                """INSERT INTO operations
                (id, workspace_id, connection_id, provider, name, display_name, tool_name,
                 kind, mode, definition_json, parameters_json, timeout_seconds, row_limit,
                 result_limit_bytes, enabled, revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    operation.id, self.workspace_id, operation.connection_id, operation.provider,
                    operation.name, operation.display_name, operation.tool_name, operation.kind,
                    operation.mode, json.dumps(operation.definition),
                    json.dumps({k: v.model_dump(mode="json") for k, v in operation.parameters.items()}),
                    operation.timeout_seconds, operation.row_limit, operation.result_limit_bytes,
                    int(operation.enabled), operation.revision, now, now,
                ),
            )
            db.execute(
                "INSERT INTO operation_revisions VALUES (?, ?, ?, ?)",
                (operation.id, operation.revision, json.dumps(snapshot), now),
            )
        return self.get_operation(operation.id)  # type: ignore[return-value]

    def update_operation(self, operation: ManagedOperation) -> ManagedOperation:
        current = self.get_operation(operation.id)
        if current is None:
            raise KeyError(operation.id)
        if operation.tool_name != current.tool_name:
            raise ValueError("managed operation tool_name is immutable")
        if operation.connection_id != current.connection_id:
            raise ValueError("managed operation connection_id is immutable")
        operation = operation.model_copy(update={"revision": current.revision + 1})
        now = _now()
        with self.transaction() as db:
            db.execute(
                """UPDATE operations SET name=?, display_name=?, kind=?, mode=?, definition_json=?,
                parameters_json=?, timeout_seconds=?, row_limit=?, result_limit_bytes=?, enabled=?,
                revision=?, updated_at=? WHERE id=? AND workspace_id=?""",
                (
                    operation.name, operation.display_name, operation.kind, operation.mode,
                    json.dumps(operation.definition),
                    json.dumps({k: v.model_dump(mode="json") for k, v in operation.parameters.items()}),
                    operation.timeout_seconds, operation.row_limit, operation.result_limit_bytes,
                    int(operation.enabled), operation.revision, now, operation.id, self.workspace_id,
                ),
            )
            db.execute(
                "INSERT INTO operation_revisions VALUES (?, ?, ?, ?)",
                (operation.id, operation.revision, json.dumps(operation.model_dump(mode="json")), now),
            )
        return operation

    def delete_operation(self, operation_id: str) -> None:
        with self.transaction() as db:
            deps = db.execute(
                "SELECT COUNT(*) FROM dependencies WHERE operation_id=?", (operation_id,)
            ).fetchone()[0]
            if deps:
                raise ValueError("operation is referenced by skills")
            cur = db.execute("DELETE FROM operations WHERE id=?", (operation_id,))
            if cur.rowcount != 1:
                raise KeyError(operation_id)

    # ------------------------------------------------------------------ dependencies/audits
    def replace_dependencies(self, dependencies: list[dict[str, Any]]) -> None:
        with self.transaction() as db:
            db.execute("DELETE FROM dependencies")
            db.executemany(
                """INSERT OR IGNORE INTO dependencies
                (operation_id, skill_name, action_name, usage) VALUES (?, ?, ?, ?)""",
                [
                    (d["operation_id"], d["skill_name"], d.get("action_name"), d["usage"])
                    for d in dependencies
                ],
            )

    def dependencies(self, operation_id: str) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT skill_name, action_name, usage FROM dependencies WHERE operation_id=? "
                "ORDER BY skill_name, action_name",
                (operation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_audit(self, event: dict[str, Any]) -> None:
        with self.transaction() as db:
            db.execute(
                """INSERT INTO audit_events
                (execution_id, workspace_id, connection_id, operation_id, tool_name,
                 skill_name, action_name, flow_run_id, started_at, duration_ms, status,
                 row_count, affected_count, truncated, parameters_json, error_code)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["execution_id"], self.workspace_id, event["connection_id"],
                    event["operation_id"], event["tool_name"], event.get("skill_name"),
                    event.get("action_name"), event.get("flow_run_id"), event["started_at"],
                    event["duration_ms"], event["status"], event.get("row_count", 0),
                    event.get("affected_count", 0), int(event.get("truncated", False)),
                    json.dumps(event.get("parameters") or {}, default=str), event.get("error_code"),
                ),
            )

    def list_audits(
        self, *, operation_id: str | None = None, connection_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id=?"]
        args: list[Any] = [self.workspace_id]
        if operation_id:
            clauses.append("operation_id=?")
            args.append(operation_id)
        if connection_id:
            clauses.append("connection_id=?")
            args.append(connection_id)
        args.append(min(max(limit, 1), 1000))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM audit_events WHERE " + " AND ".join(clauses)
                + " ORDER BY started_at DESC LIMIT ?", args,
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["truncated"] = bool(item["truncated"])
            item["parameters"] = json.loads(item.pop("parameters_json"))
            result.append(item)
        return result

    def prune_audits(self, retention_days: int | None = None) -> int:
        days = retention_days or int(os.getenv("FG_CONNECTOR_AUDIT_RETENTION_DAYS", "90"))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(days, 1))).isoformat()
        with self.transaction() as db:
            cur = db.execute("DELETE FROM audit_events WHERE started_at < ?", (cutoff,))
            return cur.rowcount

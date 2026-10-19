"""PostgreSQL managed-operation validation and execution."""
from __future__ import annotations

import re
import threading
import uuid
from typing import Any

from .models import ConnectorAdapter, ConnectorResult, ManagedOperation
from .serialization import bound_rows


_PARAM_RE = re.compile(r"(?<!%)%\(([A-Za-z_][A-Za-z0-9_]*)\)s")
_FORBIDDEN_RE = re.compile(
    r"\b(CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|CALL|COPY|DO|VACUUM|ANALYZE|EXECUTE|PREPARE)\b",
    re.IGNORECASE,
)


def _without_literals(sql: str) -> str:
    """Blank quoted SQL content so safety token checks cannot be spoofed."""
    out: list[str] = []
    i = 0
    quote: str | None = None
    while i < len(sql):
        c = sql[i]
        if quote:
            out.append(" ")
            if c == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(" ")
                    i += 2
                    continue
                quote = None
            elif c == "\\" and i + 1 < len(sql):
                out.append(" ")
                i += 2
                continue
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            out.append(" ")
            i += 1
            continue
        if c == "-" and i + 1 < len(sql) and sql[i + 1] == "-":
            end = sql.find("\n", i + 2)
            if end < 0:
                out.extend(" " * (len(sql) - i))
                break
            out.extend(" " * (end - i))
            i = end
            continue
        if c == "/" and i + 1 < len(sql) and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated SQL comment")
            out.extend(" " * (end + 2 - i))
            i = end + 2
            continue
        out.append(c)
        i += 1
    if quote:
        raise ValueError("unterminated SQL string or identifier")
    return "".join(out)


def validate_sql(operation: ManagedOperation) -> dict[str, Any]:
    sql = operation.definition.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("PostgreSQL operation requires definition.sql")
    scrubbed = _without_literals(sql)
    semicolons = [i for i, c in enumerate(scrubbed) if c == ";"]
    if semicolons:
        if len(semicolons) > 1 or scrubbed[semicolons[0] + 1 :].strip():
            raise ValueError("exactly one SQL statement is allowed")
    statement = scrubbed[: semicolons[0] if semicolons else None].strip()
    first = re.match(r"([A-Za-z]+)", statement)
    keyword = first.group(1).upper() if first else ""
    if keyword not in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
        raise ValueError("only SELECT, INSERT, UPDATE, or DELETE operations are allowed")
    if _FORBIDDEN_RE.search(statement):
        raise ValueError("DDL, CALL, COPY, and administrative SQL are not allowed")
    expected_mode = "read" if keyword == "SELECT" else "write"
    if operation.mode != expected_mode:
        raise ValueError(f"{keyword} must use operation mode {expected_mode!r}")
    if operation.kind.lower() != keyword.lower():
        raise ValueError(f"SQL statement {keyword} does not match operation kind {operation.kind!r}")
    if keyword in {"UPDATE", "DELETE"} and not re.search(r"\bWHERE\b", statement, re.I):
        raise ValueError(f"unbounded {keyword} is not allowed; add a WHERE clause")
    if re.search(r"(?<!%)%s\b", scrubbed):
        raise ValueError("use named %(parameter)s bindings, not positional %s")
    referenced = set(_PARAM_RE.findall(scrubbed))
    declared = set(operation.parameters)
    if referenced != declared:
        missing = sorted(referenced - declared)
        unused = sorted(declared - referenced)
        raise ValueError(f"SQL parameter mismatch (undeclared={missing}, unused={unused})")
    return {"valid": True, "statement": keyword, "parameters": sorted(referenced)}


class PostgreSQLAdapter(ConnectorAdapter):
    provider = "postgresql"

    def __init__(self) -> None:
        self._pools: dict[str, tuple[str, Any]] = {}
        self._lock = threading.RLock()

    def validate_operation(self, operation: ManagedOperation) -> dict[str, Any]:
        return validate_sql(operation)

    @staticmethod
    def _driver() -> tuple[Any, Any]:
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "PostgreSQL connectors require pip install 'aigenos-flow[connectors]'"
            ) from exc
        return ConnectionPool, dict_row

    def _pool(self, connection_id: str, uri: str) -> Any:
        import hashlib

        fingerprint = hashlib.sha256(uri.encode()).hexdigest()
        with self._lock:
            cached = self._pools.get(connection_id)
            if cached and cached[0] == fingerprint:
                return cached[1]
            if cached:
                cached[1].close()
            pool_cls, dict_row = self._driver()
            pool = pool_cls(
                conninfo=uri,
                min_size=0,
                max_size=4,
                open=True,
                kwargs={"autocommit": False, "row_factory": dict_row},
            )
            self._pools[connection_id] = (fingerprint, pool)
            return pool

    def execute(
        self, uri: str, operation: ManagedOperation, parameters: dict[str, Any]
    ) -> ConnectorResult:
        self.validate_operation(operation)
        execution_id = uuid.uuid4().hex
        pool = self._pool(operation.connection_id, uri)
        sql = operation.definition["sql"]
        rows: list[Any] = []
        affected = 0
        truncated = False
        with pool.connection(timeout=operation.timeout_seconds) as conn:
            try:
                with conn.transaction():
                    timeout_ms = max(1, int(operation.timeout_seconds * 1000))
                    if operation.mode == "read":
                        conn.execute("SET TRANSACTION READ ONLY")
                    conn.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
                    cur = conn.execute(sql, parameters)
                    if cur.description:
                        fetched = list(cur.fetchmany(operation.row_limit + 1))
                        if len(fetched) > operation.row_limit:
                            fetched = fetched[: operation.row_limit]
                            truncated = True
                        rows, byte_truncated = bound_rows(fetched, operation.result_limit_bytes)
                        truncated = truncated or byte_truncated
                    if operation.mode == "write":
                        affected = max(cur.rowcount or 0, 0)
            except Exception:
                conn.rollback()
                raise
        return ConnectorResult(
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            affected_count=affected,
            execution_id=execution_id,
        )

    def test_connection(self, connection_id: str, uri: str) -> dict[str, Any]:
        pool = self._pool(connection_id, uri)
        with pool.connection(timeout=10) as conn:
            row = conn.execute(
                "SELECT current_database() AS database, current_user AS user, version() AS version"
            ).fetchone()
            conn.rollback()
        return {"ok": True, "database": row["database"], "user": row["user"], "version": row["version"]}

    def inspect_schema(
        self, connection_id: str, uri: str, *, infer_fields: bool = False
    ) -> dict[str, Any]:
        del infer_fields
        pool = self._pool(connection_id, uri)
        with pool.connection(timeout=10) as conn:
            columns = list(conn.execute(
                """SELECT table_schema, table_name, column_name, data_type, is_nullable,
                          ordinal_position
                   FROM information_schema.columns
                   WHERE table_schema NOT IN ('pg_catalog','information_schema')
                   ORDER BY table_schema, table_name, ordinal_position"""
            ).fetchall())
            keys = list(conn.execute(
                """SELECT tc.table_schema, tc.table_name, tc.constraint_name,
                          tc.constraint_type, kcu.column_name
                   FROM information_schema.table_constraints tc
                   LEFT JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema
                   WHERE tc.table_schema NOT IN ('pg_catalog','information_schema')
                     AND tc.constraint_type IN ('PRIMARY KEY','FOREIGN KEY','UNIQUE')
                   ORDER BY tc.table_schema, tc.table_name, tc.constraint_name"""
            ).fetchall())
            indexes = list(conn.execute(
                """SELECT schemaname AS table_schema, tablename AS table_name,
                          indexname AS index_name, indexdef AS definition
                   FROM pg_indexes WHERE schemaname NOT IN ('pg_catalog','information_schema')
                   ORDER BY schemaname, tablename, indexname"""
            ).fetchall())
            conn.rollback()
        tables: dict[tuple[str, str], dict[str, Any]] = {}
        for column in columns:
            key = (column["table_schema"], column["table_name"])
            table = tables.setdefault(key, {"schema": key[0], "name": key[1], "columns": [], "keys": [], "indexes": []})
            table["columns"].append({
                "name": column["column_name"], "type": column["data_type"],
                "nullable": column["is_nullable"] == "YES",
            })
        for item in keys:
            table = tables.get((item["table_schema"], item["table_name"]))
            if table is not None:
                table["keys"].append({
                    "name": item["constraint_name"], "type": item["constraint_type"],
                    "column": item["column_name"],
                })
        for item in indexes:
            table = tables.get((item["table_schema"], item["table_name"]))
            if table is not None:
                table["indexes"].append({"name": item["index_name"], "definition": item["definition"]})
        return {"provider": self.provider, "tables": list(tables.values())}

    def invalidate(self, connection_id: str) -> None:
        with self._lock:
            cached = self._pools.pop(connection_id, None)
            if cached:
                cached[1].close()

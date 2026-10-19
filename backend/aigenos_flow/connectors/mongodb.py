"""MongoDB managed-operation templates, validation, and execution."""
from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import re
import threading
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .models import ConnectorAdapter, ConnectorResult, ManagedOperation
from .serialization import bound_rows, json_safe


_EXACT_TEMPLATE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
_ALL_KINDS = {
    "find_one", "find_many", "aggregate", "insert_one", "update_one",
    "update_many", "delete_one", "delete_many",
}
_READ_KINDS = {"find_one", "find_many", "aggregate"}
_BANNED_ALWAYS = {"$where", "$function", "$accumulator"}
_BANNED_PIPELINE = {"$out", "$merge"}


def _walk(value: Any, visit: Any) -> None:
    visit(value)
    if isinstance(value, dict):
        for key, child in value.items():
            visit(key)
            _walk(child, visit)
    elif isinstance(value, list):
        for child in value:
            _walk(child, visit)


def _template_parameters(value: Any) -> set[str]:
    found: set[str] = set()

    def check(item: Any) -> None:
        if not isinstance(item, str):
            return
        match = _EXACT_TEMPLATE.fullmatch(item)
        if match:
            found.add(match.group(1))
        elif "{{" in item or "}}" in item:
            raise ValueError(
                "MongoDB parameters must be the complete JSON/EJSON value, for example \"{{customer_id}}\""
            )

    _walk(value, check)
    return found


def _contains_key(value: Any, banned: set[str]) -> str | None:
    hit: str | None = None

    def check(item: Any) -> None:
        nonlocal hit
        if isinstance(item, str) and item.lower() in banned:
            hit = item

    _walk(value, check)
    return hit


def substitute_template(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = _EXACT_TEMPLATE.fullmatch(value)
        if match:
            name = match.group(1)
            if name not in parameters:
                raise KeyError(f"missing MongoDB operation parameter {name!r}")
            return parameters[name]
        if "{{" in value or "}}" in value:
            raise ValueError("string interpolation is not allowed in MongoDB templates")
        return value
    if isinstance(value, dict):
        return {key: substitute_template(child, parameters) for key, child in value.items()}
    if isinstance(value, list):
        return [substitute_template(child, parameters) for child in value]
    return value


def validate_mongo_operation(operation: ManagedOperation) -> dict[str, Any]:
    if operation.kind not in _ALL_KINDS:
        raise ValueError(f"unsupported MongoDB operation kind {operation.kind!r}")
    expected_mode = "read" if operation.kind in _READ_KINDS else "write"
    if operation.mode != expected_mode:
        raise ValueError(f"{operation.kind} must use operation mode {expected_mode!r}")
    definition = operation.definition
    if not isinstance(definition.get("collection"), str) or not definition["collection"].strip():
        raise ValueError("MongoDB operation requires a fixed collection name")
    referenced = _template_parameters(definition)
    declared = set(operation.parameters)
    if referenced != declared:
        raise ValueError(
            f"MongoDB parameter mismatch (undeclared={sorted(referenced-declared)}, "
            f"unused={sorted(declared-referenced)})"
        )
    banned = _contains_key(definition, _BANNED_ALWAYS)
    if banned:
        raise ValueError(f"MongoDB operator {banned} is not allowed")
    if operation.kind == "aggregate":
        pipeline = definition.get("pipeline")
        if not isinstance(pipeline, list):
            raise ValueError("aggregate requires a pipeline array")
        banned = _contains_key(pipeline, _BANNED_PIPELINE)
        if banned:
            raise ValueError(f"MongoDB aggregation stage {banned} is not allowed")
    if operation.kind.startswith(("update_", "delete_")):
        filt = definition.get("filter")
        if not isinstance(filt, dict) or not filt:
            raise ValueError("MongoDB mutations require a non-empty filter")
    if operation.kind.startswith("update_"):
        update = definition.get("update")
        if not isinstance(update, (dict, list)) or not update:
            raise ValueError("MongoDB update requires a non-empty update document or pipeline")
    if operation.kind == "insert_one" and not isinstance(definition.get("document"), dict):
        raise ValueError("insert_one requires a document object")
    return {"valid": True, "kind": operation.kind, "parameters": sorted(referenced)}


class MongoDBAdapter(ConnectorAdapter):
    provider = "mongodb"

    def __init__(self) -> None:
        self._clients: dict[str, tuple[str, Any]] = {}
        self._lock = threading.RLock()

    def validate_operation(self, operation: ManagedOperation) -> dict[str, Any]:
        return validate_mongo_operation(operation)

    @staticmethod
    def _driver() -> tuple[Any, Any]:
        try:
            import pymongo
            from pymongo import MongoClient
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "MongoDB connectors require pip install 'aigenos-flow[connectors]'"
            ) from exc
        return pymongo, MongoClient

    @staticmethod
    def _check_tls(uri: str) -> None:
        query = parse_qs(urlsplit(uri).query.lower())
        insecure = any(
            query.get(name, ["false"])[0] in {"true", "1"}
            for name in ("tlsallowinvalidcertificates", "tlsinsecure")
        )
        if insecure and os.getenv("FG_CONNECTOR_ALLOW_INSECURE_LOCAL", "").lower() not in {"1", "true"}:
            raise ValueError(
                "unverified MongoDB TLS is disabled; set FG_CONNECTOR_ALLOW_INSECURE_LOCAL=true only for development"
            )

    def _client(self, connection_id: str, uri: str) -> Any:
        self._check_tls(uri)
        fingerprint = hashlib.sha256(uri.encode()).hexdigest()
        with self._lock:
            cached = self._clients.get(connection_id)
            if cached and cached[0] == fingerprint:
                return cached[1]
            if cached:
                cached[1].close()
            _, client_cls = self._driver()
            client = client_cls(uri, serverSelectionTimeoutMS=10_000, connectTimeoutMS=10_000)
            self._clients[connection_id] = (fingerprint, client)
            return client

    @staticmethod
    def _database(client: Any, definition: dict[str, Any]) -> Any:
        fixed = definition.get("database")
        if fixed:
            return client[fixed]
        try:
            return client.get_default_database()
        except Exception as exc:
            raise ValueError(
                "MongoDB URI must include a database or operation definition.database"
            ) from exc

    def execute(
        self, uri: str, operation: ManagedOperation, parameters: dict[str, Any]
    ) -> ConnectorResult:
        self.validate_operation(operation)
        definition = substitute_template(operation.definition, parameters)
        client = self._client(operation.connection_id, uri)
        pymongo, _ = self._driver()
        db = self._database(client, definition)
        collection = db[definition["collection"]]
        execution_id = uuid.uuid4().hex
        rows: list[Any] = []
        affected = 0
        inserted: list[str] = []
        truncated = False
        timeout_ctx = getattr(pymongo, "timeout", None)
        ctx = timeout_ctx(operation.timeout_seconds) if timeout_ctx else contextlib.nullcontext()
        with ctx:
            kind = operation.kind
            if kind == "find_one":
                doc = collection.find_one(
                    definition.get("filter") or {}, definition.get("projection"),
                    max_time_ms=max(1, int(operation.timeout_seconds * 1000)),
                )
                rows = [] if doc is None else [doc]
            elif kind == "find_many":
                cursor = collection.find(
                    definition.get("filter") or {}, definition.get("projection"),
                ).max_time_ms(max(1, int(operation.timeout_seconds * 1000)))
                if definition.get("sort"):
                    sort = definition["sort"]
                    cursor = cursor.sort(list(sort.items()) if isinstance(sort, dict) else list(sort))
                requested = min(int(definition.get("limit") or operation.row_limit), operation.row_limit)
                fetched = list(cursor.limit(requested + 1))
                truncated = len(fetched) > requested
                rows = fetched[:requested]
            elif kind == "aggregate":
                cursor = collection.aggregate(
                    definition["pipeline"],
                    maxTimeMS=max(1, int(operation.timeout_seconds * 1000)),
                )
                fetched = list(itertools.islice(cursor, operation.row_limit + 1))
                truncated = len(fetched) > operation.row_limit
                rows = fetched[: operation.row_limit]
            elif kind == "insert_one":
                result = collection.insert_one(definition["document"])
                inserted = [str(result.inserted_id)]
                affected = 1
            elif kind in {"update_one", "update_many"}:
                method = getattr(collection, kind)
                result = method(
                    definition["filter"], definition["update"],
                    upsert=bool(definition.get("upsert", False)),
                )
                affected = int(result.modified_count)
                if result.upserted_id is not None:
                    inserted = [str(result.upserted_id)]
            elif kind in {"delete_one", "delete_many"}:
                result = getattr(collection, kind)(definition["filter"])
                affected = int(result.deleted_count)
        safe_rows, byte_truncated = bound_rows(rows, operation.result_limit_bytes)
        return ConnectorResult(
            rows=safe_rows,
            row_count=len(safe_rows),
            truncated=truncated or byte_truncated,
            affected_count=affected,
            inserted_ids=inserted,
            execution_id=execution_id,
        )

    def test_connection(self, connection_id: str, uri: str) -> dict[str, Any]:
        client = self._client(connection_id, uri)
        response = client.admin.command("ping")
        return {"ok": response.get("ok") == 1, "server": str(client.address or "mongodb")}

    def inspect_schema(
        self, connection_id: str, uri: str, *, infer_fields: bool = False
    ) -> dict[str, Any]:
        client = self._client(connection_id, uri)
        try:
            db = client.get_default_database()
        except Exception as exc:
            raise ValueError("MongoDB URI must include a database for schema inspection") from exc
        collections: list[dict[str, Any]] = []
        for info in db.list_collections():
            name = info["name"]
            indexes = []
            for index in db[name].list_indexes():
                indexes.append({
                    "name": index.get("name"),
                    "keys": list((index.get("key") or {}).items()),
                    "unique": bool(index.get("unique", False)),
                })
            item: dict[str, Any] = {
                "name": name,
                "type": info.get("type", "collection"),
                "validator": json_safe((info.get("options") or {}).get("validator") or {}),
                "indexes": indexes,
            }
            if infer_fields:
                doc = db[name].find_one()
                item["inferred_fields"] = (
                    [{"name": str(k), "type": type(v).__name__} for k, v in doc.items()]
                    if doc else []
                )
            collections.append(item)
        return {"provider": self.provider, "database": db.name, "collections": collections}

    def invalidate(self, connection_id: str) -> None:
        with self._lock:
            cached = self._clients.pop(connection_id, None)
            if cached:
                cached[1].close()

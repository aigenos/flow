"""Database-backed tools: MongoDB and Redis as workflow data sources.

The design rule is the same one that governs every tool in this runtime:

    THE OPERATOR WRITES THE QUERY; THE MODEL ONLY FILLS TYPED VALUES.

A factory like ``MongoTools.find(...)`` takes a *query template* — a normal
Mongo filter whose values may contain ``{{arg}}`` placeholders — and returns a
named tool. At call time the placeholders are bound from the args dict (which,
inside a flow, comes from validated typed fields). Args that the template does
not reference are IGNORED, so a model cannot alter the query structure, add
operators, or reach other collections. A referenced-but-missing arg raises
(fail closed) rather than querying with an empty value.

Usage::

    from aigenos_flow.integrations.databases import MongoTools, RedisTools

    mongo = MongoTools(uri="mongodb://localhost:27017", database="insurance")
    redis = RedisTools(url="redis://localhost:6379/0")

    tools = [
        mongo.find(
            "search_clients", "clients",
            {"full_name": {"$regex": "{{name}}", "$options": "i"}},
            projection={"_id": 0, "id": 1, "full_name": 1},
            limit=20,
            description="Search clients by (partial) name.",
        ),
        mongo.find_one(
            "validate_account", "accounts",
            {"account_number": "{{account_number}}"},
            projection={"_id": 0},
            description="Look up one account by its number.",
        ),
        redis.hgetall(
            "account_summary", "account:{{account_number}}:summary",
            description="Cached account summary.",
        ),
    ]

    agent = FlowAgent.from_dir("skills/", host=host, tools=tools)

Install extras: ``pip install "aigenos-flow[mongodb]"`` / ``"aigenos-flow[redis]"``.
Session/conversation persistence is separate and already handled by
``FG_TRACKER_STORE_URL`` (LangGraph checkpointers for redis/postgres/sqlite/
mongodb); this module is about DATA the workflows read and act on.

Safety notes:

  * A value bound into a ``$regex`` template is still a regex. Constrain the
    field that feeds it with a ``pattern:`` in SKILL.md (the loader already
    refuses unconstrained free-string fields in tool args), or pass
    ``regex_escape=[...]`` to escape those args wholesale.
  * Write tools (``redis.set_value`` / ``hset``) exist but side effects belong
    behind a flow gate — declare them as `actions:` with `requires:`.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_WHOLE = re.compile(r"^\s*\{\{\s*(\w+)\s*\}\}\s*$")


class MissingToolArgError(KeyError):
    """A template references an arg the caller did not supply (fail closed)."""


def template_args(template: Any) -> set[str]:
    """Every ``{{name}}`` referenced anywhere in a template tree."""
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            out.update(_PLACEHOLDER.findall(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)

    walk(template)
    return out


def bind_template(template: Any, args: dict[str, Any], *, escape: set[str] | None = None) -> Any:
    """Bind ``{{arg}}`` placeholders in a template tree from ``args``.

    * a value that is exactly ``"{{name}}"`` becomes the TYPED arg value;
    * a string containing placeholders is interpolated;
    * everything else passes through untouched;
    * a referenced-but-missing arg raises MissingToolArgError;
    * args not referenced by the template are ignored (the model cannot
      widen a query by inventing parameters).
    """
    escape = escape or set()

    def value_of(name: str) -> Any:
        if name not in args or args[name] is None:
            raise MissingToolArgError(
                f"query template needs arg {name!r} but it was not provided"
            )
        v = args[name]
        if name in escape and isinstance(v, str):
            return re.escape(v)
        return v

    def walk(v: Any) -> Any:
        if isinstance(v, str):
            m = _WHOLE.match(v)
            if m:
                return value_of(m.group(1))
            if "{{" in v:
                return _PLACEHOLDER.sub(lambda mm: str(value_of(mm.group(1))), v)
            return v
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v

    return walk(template)


def _finish(fn: Callable[[dict[str, Any]], Any], name: str, description: str | None) -> Callable:
    fn.__name__ = name
    doc = (description or name.replace("_", " ")).strip()
    fn.__doc__ = doc
    fn.description = doc  # type: ignore[attr-defined] — chat tool-defs read this
    return fn


# --------------------------------------------------------------------------- #
# MongoDB
# --------------------------------------------------------------------------- #
class MongoTools:
    """Factory for Mongo-backed read tools over ONE database.

    Pass ``db=`` any object with ``db[collection].find/find_one/aggregate/
    count_documents`` (a ``pymongo.database.Database``, or a fake in tests),
    or ``uri=`` + ``database=`` to connect lazily via pymongo.
    """

    def __init__(
        self,
        db: Any = None,
        *,
        uri: str | None = None,
        database: str | None = None,
    ):
        if db is None:
            if not uri or not database:
                raise ValueError("MongoTools needs either db= or uri= + database=")
            try:
                from pymongo import MongoClient
            except ImportError as e:  # pragma: no cover
                raise ImportError(
                    'MongoDB tools require pymongo: pip install "aigenos-flow[mongodb]"'
                ) from e
            db = MongoClient(uri)[database]
        self._db = db

    def find(
        self,
        name: str,
        collection: str,
        filter: dict[str, Any],
        *,
        projection: dict[str, Any] | None = None,
        sort: list[tuple[str, int]] | None = None,
        limit: int = 20,
        regex_escape: list[str] | None = None,
        description: str | None = None,
    ) -> Callable:
        """A tool returning ``{"rows": [...], "count": n}`` — shaped for
        ``pick:`` / ``show:`` steps to bind directly."""
        coll = self._db[collection]
        esc = set(regex_escape or [])

        def tool(args: dict[str, Any]) -> dict[str, Any]:
            bound = bind_template(filter, args, escape=esc)
            cursor = coll.find(bound, projection)
            if sort:
                cursor = cursor.sort(sort)
            rows = list(cursor.limit(limit) if hasattr(cursor, "limit") else cursor)
            return {"rows": rows[:limit], "count": len(rows[:limit])}

        return _finish(tool, name, description)

    def find_one(
        self,
        name: str,
        collection: str,
        filter: dict[str, Any],
        *,
        projection: dict[str, Any] | None = None,
        regex_escape: list[str] | None = None,
        description: str | None = None,
    ) -> Callable:
        """A tool returning ``{"found": bool, "doc": {...}}`` — shaped for
        ``check:`` gates (``check: "validate_x.found"``)."""
        coll = self._db[collection]
        esc = set(regex_escape or [])

        def tool(args: dict[str, Any]) -> dict[str, Any]:
            doc = coll.find_one(bind_template(filter, args, escape=esc), projection)
            return {"found": doc is not None, "doc": doc or {}}

        return _finish(tool, name, description)

    def count(
        self,
        name: str,
        collection: str,
        filter: dict[str, Any],
        *,
        regex_escape: list[str] | None = None,
        description: str | None = None,
    ) -> Callable:
        coll = self._db[collection]
        esc = set(regex_escape or [])

        def tool(args: dict[str, Any]) -> dict[str, Any]:
            return {"count": coll.count_documents(bind_template(filter, args, escape=esc))}

        return _finish(tool, name, description)

    def aggregate(
        self,
        name: str,
        collection: str,
        pipeline: list[dict[str, Any]],
        *,
        limit: int = 50,
        description: str | None = None,
    ) -> Callable:
        """A FIXED aggregation pipeline with ``{{arg}}`` value slots."""
        coll = self._db[collection]

        def tool(args: dict[str, Any]) -> dict[str, Any]:
            rows = list(coll.aggregate(bind_template(pipeline, args)))[:limit]
            return {"rows": rows, "count": len(rows)}

        return _finish(tool, name, description)


# --------------------------------------------------------------------------- #
# Redis
# --------------------------------------------------------------------------- #
def _decode(v: Any) -> Any:
    if isinstance(v, bytes):
        v = v.decode("utf-8", errors="replace")
    if isinstance(v, str) and v[:1] in "[{":
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return v
    return v


class RedisTools:
    """Factory for Redis-backed tools. Keys are operator templates
    (``"account:{{account_number}}:summary"``) — the model fills values, never
    key shapes. Bytes are decoded; JSON-looking strings are parsed."""

    def __init__(self, client: Any = None, *, url: str | None = None):
        if client is None:
            if not url:
                raise ValueError("RedisTools needs either client= or url=")
            try:
                import redis as _redis
            except ImportError as e:  # pragma: no cover
                raise ImportError(
                    'Redis tools require redis-py: pip install "aigenos-flow[redis]"'
                ) from e
            client = _redis.Redis.from_url(url)
        self._r = client

    def get(self, name: str, key: str, *, description: str | None = None) -> Callable:
        def tool(args: dict[str, Any]) -> dict[str, Any]:
            k = bind_template(key, args)
            v = _decode(self._r.get(k))
            return {"found": v is not None, "key": k, "value": v}

        return _finish(tool, name, description)

    def hgetall(self, name: str, key: str, *, description: str | None = None) -> Callable:
        def tool(args: dict[str, Any]) -> dict[str, Any]:
            k = bind_template(key, args)
            raw = self._r.hgetall(k) or {}
            doc = {_decode(field): _decode(val) for field, val in raw.items()}
            return {"found": bool(doc), "key": k, "doc": doc}

        return _finish(tool, name, description)

    def lrange(
        self, name: str, key: str, *, start: int = 0, stop: int = -1,
        description: str | None = None,
    ) -> Callable:
        """Returns ``{"rows": [...]}`` — list entries (JSON-decoded when
        possible), shaped for ``pick:``/``show:`` binding."""

        def tool(args: dict[str, Any]) -> dict[str, Any]:
            k = bind_template(key, args)
            rows = [_decode(v) for v in self._r.lrange(k, start, stop)]
            return {"rows": rows, "count": len(rows), "key": k}

        return _finish(tool, name, description)

    # ---- writes: declare these as gated `actions:` (requires: confirm) ----
    def set_value(
        self, name: str, key: str, *, ttl_seconds: int | None = None,
        description: str | None = None,
    ) -> Callable:
        """Write tool: SET <key-template> <args['value']> (JSON-encoded when
        not a string). SIDE EFFECT — gate it behind `requires:` in the skill."""

        def tool(args: dict[str, Any]) -> dict[str, Any]:
            k = bind_template(key, args)
            v = args.get("value")
            payload = v if isinstance(v, str) else json.dumps(v, default=str)
            if ttl_seconds:
                self._r.set(k, payload, ex=ttl_seconds)
            else:
                self._r.set(k, payload)
            return {"ok": True, "key": k}

        return _finish(tool, name, description)

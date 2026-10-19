"""SQLite workspace store — the Studio's memory.

Files remain the runtime contract (SKILL.md folders, widget JSON: the
Anthropic skills layout, git-friendly, hot-reloaded from disk). This store is
the low-code layer on top: every Save from the Studio also records an
immutable revision here, which gives the UI version history, one-click
restore, and an activity feed — with zero setup. SQLite ships in Python's
standard library, so `pip install aigenos-flow` needs nothing extra and the
whole workspace database is a single file.

Location: ``$FG_STUDIO_DB`` if set, else ``<data dir>/studio.db``. If that
directory is read-only (e.g. the packaged data dir inside site-packages), the
store falls back to ``~/.aigenos-flow/studio.db`` so a plain pip install
still gets history out of the box.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("aigenos_flow.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS revisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    version    INTEGER NOT NULL,
    body       TEXT NOT NULL,
    label      TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (kind, ref, version)
);
CREATE INDEX IF NOT EXISTS idx_revisions_ref ON revisions (kind, ref, version DESC);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    ref        TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events (id DESC);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


class StudioStore:
    """Versioned snapshots + activity log for skills, widgets, and workflows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        # A connection per operation keeps this safe under FastAPI's threadpool
        # without a lock; WAL keeps readers unblocked during writes.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- revisions ----------

    def record(
        self,
        kind: str,
        ref: str,
        body: dict[str, Any],
        label: str | None = None,
        action: str = "saved",
    ) -> int:
        """Store a snapshot; returns its version number.

        Identical back-to-back saves dedupe to the existing version (so
        autosave never floods history), but a save that carries a label
        still relabels that version.
        """
        doc = _canonical(body)
        with self._conn() as c:
            row = c.execute(
                "SELECT version, body FROM revisions WHERE kind=? AND ref=? "
                "ORDER BY version DESC LIMIT 1",
                (kind, ref),
            ).fetchone()
            if row is not None and row["body"] == doc:
                if label:
                    c.execute(
                        "UPDATE revisions SET label=? WHERE kind=? AND ref=? AND version=?",
                        (label, kind, ref, row["version"]),
                    )
                return int(row["version"])
            version = 1 if row is None else int(row["version"]) + 1
            c.execute(
                "INSERT INTO revisions (kind, ref, version, body, label, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (kind, ref, version, doc, label, _now()),
            )
            c.execute(
                "INSERT INTO events (kind, ref, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, ref, action, label or f"v{version}", _now()),
            )
        return version

    def history(self, kind: str, ref: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT version, label, created_at, length(body) AS size "
                "FROM revisions WHERE kind=? AND ref=? ORDER BY version DESC LIMIT ?",
                (kind, ref, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get(self, kind: str, ref: str, version: int) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT body, label, created_at FROM revisions "
                "WHERE kind=? AND ref=? AND version=?",
                (kind, ref, version),
            ).fetchone()
        if row is None:
            return None
        return {
            "version": version,
            "label": row["label"],
            "created_at": row["created_at"],
            "body": json.loads(row["body"]),
        }

    # ---------- activity ----------

    def log(self, kind: str, ref: str, action: str, detail: str | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (kind, ref, action, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (kind, ref, action, detail, _now()),
            )

    def activity(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT kind, ref, action, detail, created_at FROM events "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------- module singleton ----------

_store: StudioStore | None = None


def _default_path() -> Path:
    env = os.environ.get("FG_STUDIO_DB")
    if env:
        return Path(env).expanduser()
    from .engine.loader import DATA_DIR

    return DATA_DIR / "studio.db"


def get_store() -> StudioStore:
    global _store
    if _store is None:
        path = _default_path()
        try:
            _store = StudioStore(path)
        except (OSError, sqlite3.OperationalError):
            fallback = Path.home() / ".aigenos-flow" / "studio.db"
            log.warning("workspace db not writable at %s — using %s", path, fallback)
            _store = StudioStore(fallback)
        log.info("workspace store: %s", _store.path)
    return _store

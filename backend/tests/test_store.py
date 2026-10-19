"""Workspace store: versioned snapshots + activity, on plain stdlib sqlite3."""
from __future__ import annotations

from aigenos_flow.store import StudioStore


def _store(tmp_path):
    return StudioStore(tmp_path / "studio.db")


def test_record_versions_and_roundtrip(tmp_path):
    s = _store(tmp_path)
    v1 = s.record("widget", "grid", {"id": "grid", "spec": {"type": "card"}})
    v2 = s.record("widget", "grid", {"id": "grid", "spec": {"type": "form"}})
    assert (v1, v2) == (1, 2)

    rev = s.get("widget", "grid", 1)
    assert rev is not None
    assert rev["body"]["spec"]["type"] == "card"
    assert s.get("widget", "grid", 99) is None


def test_identical_save_dedupes(tmp_path):
    s = _store(tmp_path)
    body = {"id": "grid", "spec": {"type": "card"}}
    assert s.record("widget", "grid", body) == 1
    # Same content again — no new version (autosave must not flood history)
    assert s.record("widget", "grid", dict(body)) == 1
    assert len(s.history("widget", "grid")) == 1
    # ...but a labeled identical save relabels the existing version
    s.record("widget", "grid", dict(body), label="before launch")
    assert s.history("widget", "grid")[0]["label"] == "before launch"


def test_history_newest_first_and_scoped(tmp_path):
    s = _store(tmp_path)
    s.record("skill", "a", {"SKILL.md": "v1"})
    s.record("skill", "a", {"SKILL.md": "v2"})
    s.record("skill", "b", {"SKILL.md": "other"})
    hist = s.history("skill", "a")
    assert [h["version"] for h in hist] == [2, 1]
    assert len(s.history("skill", "b")) == 1


def test_activity_feed(tmp_path):
    s = _store(tmp_path)
    s.record("widget", "grid", {"id": "grid"})
    s.log("widget", "grid", "deleted")
    events = s.activity()
    assert [e["action"] for e in events] == ["deleted", "saved"]
    assert events[0]["ref"] == "grid"

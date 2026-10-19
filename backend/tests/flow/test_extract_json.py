"""Unit coverage for the host's JSON-extraction normalizer.

The reasoning-model regression: qwen3 / deepseek-r1 prepend a
``<think>...</think>`` block whose prose contains JSON-looking fragments.
The greedy object regex used to latch onto those fragments, so the real
interpretation JSON never got parsed and zero fields were extracted. The
fix strips think blocks first; these tests pin that behavior.
"""
from __future__ import annotations

import json

from aigenos_flow.hosts.langgraph_host import _extract_json


def test_strips_think_block_before_json():
    raw = (
        "<think>\nThe user wants redwood. I should emit "
        '{"field": "room"} maybe.\n</think>\n'
        '{"items": [{"type": "SetField", "field": "room", "value": "redwood"}]}'
    )
    out = _extract_json(raw)
    parsed = json.loads(out)
    assert parsed["items"][0]["field"] == "room"
    assert parsed["items"][0]["value"] == "redwood"


def test_think_block_with_fenced_json_after():
    raw = (
        "<think>reasoning with a stray { brace</think>\n"
        '```json\n{"items": []}\n```'
    )
    assert json.loads(_extract_json(raw)) == {"items": []}


def test_plain_json_unaffected():
    raw = '{"items": [{"type": "Reply", "text": "hi"}]}'
    assert json.loads(_extract_json(raw))["items"][0]["type"] == "Reply"


def test_duplicate_items_key_keeps_real_interpretation():
    # Verbatim qwen3:8b output under format=json on a confirm turn: the model
    # interprets correctly, then appends a spurious empty duplicate key.
    # Last-key-wins parsing silently discarded SetField(confirm, true) and the
    # flow re-asked forever. Pin the merge.
    raw = (
        '{"items": [{"type":"Reply","text":"Transfer confirmed."},'
        '{"type":"SetField","field":"confirm","value":true}],"items": []}'
    )
    parsed = json.loads(_extract_json(raw))
    assert len(parsed["items"]) == 2
    assert parsed["items"][1] == {
        "type": "SetField", "field": "confirm", "value": True,
    }


def test_duplicate_key_lists_concatenate():
    raw = (
        '{"items": [{"type":"Cancel"}],'
        '"items": [{"type":"Reply","text":"ok"}]}'
    )
    parsed = json.loads(_extract_json(raw))
    assert [i["type"] for i in parsed["items"]] == ["Cancel", "Reply"]


def test_duplicate_scalar_key_first_nonempty_wins():
    raw = '{"skill": "transfer-funds", "skill": null}'
    assert json.loads(_extract_json(raw))["skill"] == "transfer-funds"


def test_malformed_json_passes_through_unchanged():
    raw = '{"items": [unclosed'
    assert _extract_json(raw) == raw

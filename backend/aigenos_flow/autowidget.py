"""Auto-widgets: turn plain data into an a2ui widget spec by SHAPE.

This is what lets a skill author write

    - show: "{{account_summary}}"          # renders a card / table / timeline
    - pick: "{{find_accounts.accounts}}"   # renders a selectable grid
      save: [account_number]

with no widget JSON at all. The mapper looks at the VALUE and picks the right
primitive (all of which already exist in the @aigenos/flow renderer):

    list[dict] with a date-ish key   -> timeline
    list[dict]                       -> datatable (a grid)
    list[scalar]                     -> datatable with one column
    dict of scalars                  -> card of label/value rows
    dict with exactly one list value -> unwrap and recurse
    scalar                           -> a stat card

Every node this module emits uses the prop names the renderer's registry and
the visual designer declare (frontend/src/widget-editor/primitiveMeta.ts):
``text`` takes ``value`` + ``muted``, ``timeline`` takes ``events`` of
``{time, title, description}``, ``datatable`` takes ``rows`` + ``columns``.
A prop name nothing reads renders as an empty element, which is silent on this
side of the wire — ``backend/tests/test_wire_contract.py`` pins them.

Power users who want a custom layout keep the ``widget:`` step + a spec file;
this module covers the 90% "just show it nicely" case.
"""
from __future__ import annotations

import re
from typing import Any

# Row keys that read as "this is a moment in time" -> timeline rendering.
# Matched on WHOLE TOKENS, never as substrings: "at" is a substring of seats,
# category, status, rating, latitude and state, so a substring test routed
# ordinary tables into a timeline and the rows vanished.
_DATEISH = frozenset(
    {"date", "dates", "timestamp", "timestamps", "when", "time", "times",
     "at", "on", "occurred", "created", "updated", "datetime", "day"}
)
# Suffixes that make a key dated regardless of its leading tokens
# (created_at, due_date, start_time, posted_on).
_DATEISH_SUFFIXES = ("_at", "_date", "_time", "_on", "_datetime", "_timestamp")
_TOKEN_RE = re.compile(r"[_\-\s.]+")
_LABEL_RE = re.compile(r"[_\-]+")

_MAX_COLUMNS = 8
_MAX_ROWS = 50


def _label(key: str) -> str:
    return _LABEL_RE.sub(" ", str(key)).strip().title()


def _is_scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def is_dateish(key: Any) -> bool:
    """True when ``key`` names a moment in time.

    Token-exact, so ``seats`` / ``status`` / ``category`` / ``rating`` /
    ``latitude`` / ``state`` are NOT dates, while ``date``, ``created_at``,
    ``start_time`` and ``occurred_on`` are.
    """
    k = str(key).lower()
    if k.endswith(_DATEISH_SUFFIXES):
        return True
    return bool(_DATEISH & set(_TOKEN_RE.split(k)))


def _date_key(row: dict) -> str | None:
    for k in row:
        if is_dateish(k):
            return k
    return None


def _columns(rows: list[dict]) -> list[dict]:
    """Union of keys in first-row order, scalar-valued keys only, capped."""
    seen: list[str] = []
    for row in rows:
        for k in row:
            if k not in seen and _is_scalar(row.get(k)):
                seen.append(k)
    return [{"key": k, "label": _label(k)} for k in seen[:_MAX_COLUMNS]]


# --------------------------------------------------------------------------- #
# Display widgets (show:)
# --------------------------------------------------------------------------- #
def spec_for(value: Any, *, title: str | None = None) -> dict:
    """A display-only SpecNode tree for ``value``, chosen by shape."""
    body = _body_for(value)
    props: dict[str, Any] = {}
    if title:
        props["title"] = title
    return {"type": "card", "props": props, "children": [body]}


def _timeline_events(rows: list[dict], date_key: str) -> list[dict]:
    """Map dated rows into the ``{time, title, description}`` shape the
    Timeline primitive declares (frontend/src/widget-editor/primitiveMeta.ts).

    Emitting ``{items, dateKey}`` instead rendered an empty ``<ol>``: the
    renderer reads ``props.events``, so every row was dropped.
    """
    events: list[dict] = []
    for row in rows:
        rest = [
            (k, v) for k, v in row.items() if k != date_key and _is_scalar(v) and v is not None
        ]
        event: dict[str, Any] = {"time": _text(row.get(date_key))}
        if rest:
            event["title"] = _text(rest[0][1])
        if len(rest) > 1:
            # Remaining scalars, labelled, so nothing in the row is lost.
            event["description"] = " · ".join(
                f"{_label(k)}: {_text(v)}" for k, v in rest[1:]
            )
        events.append(event)
    return events


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _body_for(value: Any) -> dict:
    if isinstance(value, list):
        rows = [r for r in value[:_MAX_ROWS] if isinstance(r, dict)]
        if rows:
            dk = _date_key(rows[0])
            if dk is not None:
                return {
                    "type": "timeline",
                    "props": {"events": _timeline_events(rows, dk)},
                }
            return {
                "type": "datatable",
                "props": {"rows": value[:_MAX_ROWS], "columns": _columns(rows)},
            }
        # list of scalars -> one-column table
        return {
            "type": "datatable",
            "props": {
                "rows": [{"value": v} for v in value[:_MAX_ROWS]],
                "columns": [{"key": "value", "label": "Value"}],
            },
        }

    if isinstance(value, dict):
        list_vals = [(k, v) for k, v in value.items() if isinstance(v, list)]
        scalars = {k: v for k, v in value.items() if _is_scalar(v)}
        if len(list_vals) == 1 and not scalars:
            # {"accounts": [...]} -> unwrap
            return _body_for(list_vals[0][1])
        # key/value card rows (nested lists rendered after the scalars).
        # The text primitive's prop is `value` and its only emphasis prop is
        # `muted` — `text`/`variant` were read by nothing, so every label and
        # value in a `show:` card rendered as an empty <p>.
        children: list[dict] = [
            {
                "type": "row",
                "props": {},
                "children": [
                    {"type": "text", "props": {"value": _label(k), "muted": True}},
                    {"type": "text", "props": {"value": _text(v)}},
                ],
            }
            for k, v in scalars.items()
        ]
        for k, v in list_vals:
            children.append({"type": "divider", "props": {}})
            children.append({"type": "text", "props": {"value": _label(k), "muted": True}})
            children.append(_body_for(v))
        return {"type": "column", "props": {}, "children": children}

    # scalar -> a stat
    return {"type": "text", "props": {"value": _text(value)}}


# --------------------------------------------------------------------------- #
# Selection widgets (pick:)
# --------------------------------------------------------------------------- #
def _match_row_key(field: str, row_keys: list[str]) -> str | None:
    """Best row key for a save field, so common namings need no config:
    exact (``account_number``), drop-first-segment (``client_id`` -> ``id``,
    ``client_full_name`` -> ``full_name``), then unique suffix/prefix."""
    if field in row_keys:
        return field
    if "_" in field:
        tail = field.split("_", 1)[1]
        if tail in row_keys:
            return tail
    suffix = [k for k in row_keys if field.endswith(f"_{k}") or k.endswith(f"_{field}")]
    if len(suffix) == 1:
        return suffix[0]
    # An answer that is an id takes the row's id when the row has exactly one:
    # `hotel_id` from rows keyed `osm_id`.
    if field == "id" or field.endswith("_id"):
        ids = [k for k in row_keys if k == "id" or str(k).endswith("_id")]
        if len(ids) == 1:
            return ids[0]
    return None


PICK_KINDS = ("datatable", "autosuggest")


def _display_key(row_keys: list[str]) -> str | None:
    """The column an autosuggest shows: prefer name/label/title-ish keys."""
    for pref in ("name", "label", "title"):
        for k in row_keys:
            if pref in str(k).lower():
                return k
    return row_keys[0] if row_keys else None


def confirm_spec_for(field: str, question: str, runs: list[dict[str, Any]]) -> tuple[dict, dict]:
    """The card a confirmation shows, as ``(widget_spec, submit_schema)``.

    ``runs`` is what a yes will run: ``[{"title", "note", "inputs": [{"label",
    "shown"}]}]``, already worded for a person and with any secret hidden. The
    card is made of the ordinary primitives (text, row, button), so every client
    that can draw a widget can draw it.
    """
    children: list[dict[str, Any]] = []
    if runs:
        children.append({"type": "text", "props": {
            "value": "Saying yes runs this, once:" if len(runs) == 1 else "Saying yes runs these, once each:",
            "muted": True,
        }})
        for run in runs:
            children.append({"type": "text", "props": {"value": run["title"], "weight": "bold"}})
            if run.get("note"):
                children.append({"type": "text", "props": {"value": run["note"], "muted": True}})
            for item in run.get("inputs") or []:
                children.append({
                    "type": "row", "props": {"justify": "between", "gap": "md"},
                    "children": [
                        {"type": "text", "props": {"value": item["label"], "muted": True}},
                        {"type": "text", "props": {"value": item["shown"]}},
                    ],
                })
        children.append({"type": "divider", "props": {}})
    children.append({
        "type": "row", "props": {"justify": "end", "gap": "sm"},
        "children": [
            {"type": "button", "props": {
                "label": "No", "variant": "secondary", "action": "submit", "payload": {field: "false"},
            }},
            {"type": "button", "props": {
                "label": "Yes, go ahead", "variant": "primary", "action": "submit", "payload": {field: "true"},
            }},
        ],
    })
    spec = {"type": "card", "props": {"title": question}, "children": children}
    schema = {
        "type": "object",
        "properties": {field: {"type": "string", "enum": ["true", "false"]}},
        "required": [field],
    }
    return spec, schema


def pick_spec_for(
    rows: Any,
    save: list[str],
    *,
    prompt: str | None = None,
    field_map: dict[str, str] | None = None,
    kind: str = "datatable",
    display: str | None = None,
) -> tuple[dict, dict]:
    """A selection (widget_spec, submit_schema) for ``pick:``.

    ``save`` lists the fields a selection fills. Mapping to row keys is
    automatic (exact name, ``client_id`` -> ``id``-style shortening, unique
    suffix); an explicit ``map: {save_field: row_key}`` on the step wins when
    the rows use unrelated names.

    ``kind`` chooses the auto-generated primitive: ``datatable`` (a grid, the
    default) or ``autosuggest`` (type-to-filter). ``display`` names the row
    key an autosuggest shows (auto-picked from name/label/title-ish keys).
    A fully custom layout uses ``widget: <id>`` on the step instead — that
    path never reaches this function.
    """
    if kind not in PICK_KINDS:
        raise ValueError(f"pick `as:` must be one of {PICK_KINDS}, got {kind!r}")
    rows = rows if isinstance(rows, list) else []
    dict_rows = [r for r in rows if isinstance(r, dict)]
    row_keys: list[str] = list(dict_rows[0].keys()) if dict_rows else []

    fields_map: dict[str, str] = {}
    for f in save:
        explicit = (field_map or {}).get(f)
        matched = explicit or _match_row_key(f, row_keys)
        if matched is None and len(save) == 1 and row_keys:
            matched = row_keys[0]
        if matched is None:
            raise ValueError(
                f"pick: cannot map save field {f!r} to a row key "
                f"(row keys: {row_keys}). Add `map: {{{f}: <row key>}}` to the step."
            )
        fields_map[f] = matched

    if kind == "autosuggest":
        body = {
            "type": "autosuggest",
            "props": {
                "label": prompt or "Select one",
                "placeholder": "Type to filter…",
                "display": display or _display_key(row_keys),
                "options": rows[:_MAX_ROWS],
                "fields": fields_map,
            },
        }
    else:
        body = {
            "type": "datatable",
            "props": {
                "rows": rows[:_MAX_ROWS],
                "columns": _columns(dict_rows) if dict_rows else [],
                "fields": fields_map,
            },
        }

    spec = {
        "type": "card",
        "props": {"title": prompt or "Select one"},
        "children": [
            {
                "type": "form",
                "props": {"submitLabel": "Continue"},
                "children": [body],
            }
        ],
    }
    submit_schema = {
        "type": "object",
        "properties": {f: {"type": "string"} for f in save},
        "required": list(save),
    }
    return spec, submit_schema

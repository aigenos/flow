"""A skill, read back as a procedure.

    python -m aigenos_flow.procedure skills/ transfer-funds

Three things, all computed from the skill file with no model involved, so what
is read is what runs:

1. **The steps in plain words.** What is asked, what an answer must satisfy,
   what is said, what is run and when.
2. **Every way through the flow.** The conditions in a skill are closed
   expressions over a handful of answers and tool results, so the set of paths
   is finite and small. Each is listed with what is asked on it and what it
   runs.
3. **What must be true before anything changes.** For every tool that changes
   something: the yes it waits for, the conditions it runs under, the rules its
   inputs passed, and who was allowed to supply each input.

This is the page a person signs off. It cannot drift from the file: it is
generated from it.
"""
from __future__ import annotations

import ast
import itertools
import re
from typing import Any

from .engine.safe_eval import eval_strict, free_vars

#: More combinations of answers than this and the paths are not listed.
MAX_COMBINATIONS = 4096
_VERBS = ("ask", "say", "do", "confirm", "chat")
_SHAPES = {
    "^[0-9]{6}$": "6 digits", "^[0-9]{4}$": "4 digits", "^[0-9]{9}$": "9 digits", "^[0-9]+$": "digits only",
    "^[0-9]{4}-[0-9]{2}-[0-9]{2}$": "a date, YYYY-MM-DD", r"^\d{4}-\d{2}-\d{2}$": "a date, YYYY-MM-DD",
    "^([01][0-9]|2[0-3]):[0-5][0-9]$": "a time, HH:MM",
}
_KIND = {"string": "text", "number": "a number", "integer": "a whole number", "boolean": "yes or no"}


def _words(name: Any) -> str:
    return str(name).replace("_", " ").replace("-", " ").strip()


# --------------------------------------------------------------------------- conditions in words
def english(expr: str) -> str:
    """A condition the way a person would say it. Falls back to the expression
    itself for anything it does not recognise, so it is never wrong, only
    sometimes less friendly."""
    try:
        return _en(ast.parse(str(expr), mode="eval").body, top=True)
    except Exception:  # noqa: BLE001 - shown as written
        return str(expr)


def _ref(node: ast.AST) -> str | None:
    """`tool.key.sub` as "tool says key sub"; a bare name as its words."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    if not parts:
        return _words(node.id)
    return f"{_words(node.id)} says {_words(' '.join(reversed(parts)))}"


def _value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant):
        if node.value is True:
            return "yes"
        if node.value is False:
            return "no"
        if isinstance(node.value, str):
            return _words(node.value) or "blank"
        return str(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return f"-{node.operand.value}"
    return _ref(node)


def _en(node: ast.AST, top: bool = False) -> str:
    if isinstance(node, ast.BoolOp):
        joiner = " and " if isinstance(node.op, ast.And) else " or "
        text = joiner.join(_en(v) for v in node.values)
        return text if top else f"({text})"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        inner = _ref(node.operand)
        if inner is not None:
            return inner.replace(" says ", " does not say ") if " says " in inner else f"{inner} is no"
        return f"not ({_en(node.operand, top=True)})"
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left, right = _value(node.left), _value(node.comparators[0])
        if left is None or right is None:
            raise ValueError("not a simple comparison")
        op = node.ops[0]
        if isinstance(op, ast.GtE):
            return f"{left} is {right} or more"
        if isinstance(op, ast.Gt):
            return f"{left} is more than {right}"
        if isinstance(op, ast.LtE):
            return f"{left} is {right} or less"
        if isinstance(op, ast.Lt):
            return f"{left} is less than {right}"
        if isinstance(op, ast.Eq):
            return f"{left} is {right}"
        if isinstance(op, ast.NotEq):
            return f"{left} is not {right}"
        raise ValueError("comparison not worded")
    ref = _ref(node)
    if ref is not None:
        return ref
    raise ValueError("not worded")


# --------------------------------------------------------------------------- the file, flattened
def _flatten(steps: Any, conds: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Steps in order, each with every condition it sits under."""
    out: list[dict[str, Any]] = []
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        own = conds + ((str(step["when"]),) if step.get("when") is not None else ())
        if isinstance(step.get("steps"), list):
            raw = step.get("repeat")
            if isinstance(raw, dict) and raw:
                # One line for the repeat itself, then its steps, each marked
                # as asked once per item.
                name, question = next(iter(raw.items()))
                out.append({"_repeat": str(name), "_question": str(question), "_when": own})
                out.extend({**inner, "_in": str(name)} for inner in _flatten(step["steps"], own))
                continue
            out.extend(_flatten(step["steps"], own))
            continue
        out.append({**{k: v for k, v in step.items() if k != "when"}, "_when": own})
    return out


def _verb(step: dict[str, Any]) -> str:
    if "_repeat" in step:
        return "repeat"
    return next((v for v in _VERBS if v in step), "say")


def _asks(step: dict[str, Any]) -> list[tuple[str, str]]:
    raw = step.get("ask")
    if isinstance(raw, str):
        return [(raw, "")]
    if isinstance(raw, dict):
        return [(str(n), "" if q is None else str(q)) for n, q in raw.items()]
    return []


def _rules(step: dict[str, Any]) -> list[tuple[str, str]]:
    raw = step.get("check")
    if isinstance(raw, dict):
        return [(str(c), str(m)) for c, m in raw.items()]
    if isinstance(raw, list):
        return [(str(c), str(m)) for item in raw if isinstance(item, dict) for c, m in item.items()]
    return []


def _confirm_question(step: dict[str, Any]) -> str:
    raw = step.get("confirm")
    if isinstance(raw, dict) and raw:
        return str(next(iter(raw.values())))
    return str(raw or "")


def _confirm_name(step: dict[str, Any], seen: int) -> str:
    raw = step.get("confirm")
    if isinstance(raw, dict) and raw:
        return str(next(iter(raw)))
    return "confirm" if seen == 0 else f"confirm_{seen + 1}"


# --------------------------------------------------------------------------- the procedure
def describe(front: Any, skill: Any = None, tools: dict[str, dict[str, Any]] | None = None,
             skills: set[str] | None = None) -> dict[str, Any]:
    """Everything the procedure page shows, as plain data.

    ``front`` is the parsed frontmatter of a short-form skill. ``skill`` is the
    loaded skill when there is one (it knows each answer's type and who may give
    it); ``tools`` is ``{name: ToolInfo.as_dict()}``; ``skills`` the other
    skills of the agent (a ``do:`` may name one).
    """
    tools = tools or {}
    skills = skills or set()
    fields = dict(getattr(skill, "fields", None) or {})
    user_only = {
        n for n, f in fields.items()
        if f.must_elicit or f.sensitive
        or n in (getattr(skill, "confirmation_fields", None) or [])
        or n in (getattr(skill, "picked_fields", None) or [])
    }
    flat = _flatten(getattr(front, "steps", None))

    def is_skill(target: str) -> bool:
        return target not in tools and (target in skills or bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)+", target)))

    def changes(target: str) -> bool:
        return bool(tools.get(target, {}).get("changes_something", target not in tools and not is_skill(target)))

    # ---- which yes releases which tool (the same rule the loader applies)
    confirms: list[tuple[int, str, tuple[str, ...], str]] = []      # (step no, name, conditions, question)
    yes_for: dict[int, int] = {}                                    # write step no -> confirm step no
    released: dict[int, list[str]] = {}                             # confirm step no -> tools a no would stop
    for n, step in enumerate(flat, 1):
        verb = _verb(step)
        if verb == "confirm":
            confirms.append((n, _confirm_name(step, len(confirms)), step["_when"], _confirm_question(step)))
        elif verb == "do" and changes(str(step.get("do"))):
            for c_no, _name, c_when, _q in reversed(confirms):
                if set(c_when) <= set(step["_when"]):
                    yes_for[n] = c_no
                    break
            # "No" at any confirmation above stops the flow, so each of them
            # stands between the user and this tool.
            for c_no, _name, _c_when, _q in confirms:
                released.setdefault(c_no, []).append(str(step.get("do")))

    asked_at: dict[str, int] = {}
    collected_at: dict[str, int] = {}          # a `repeat:` list -> its step
    rendered: list[dict[str, Any]] = []
    lookups: dict[str, None] = {}

    def note_lookups(*texts: Any) -> None:
        for text in texts:
            if not isinstance(text, str):
                continue
            roots = set(re.findall(r"\{\{\s*([A-Za-z_]\w*)", text))
            try:
                roots |= free_vars(text) if "{{" not in text else set()
            except Exception:  # noqa: BLE001
                pass
            for root in sorted(roots):
                if root in tools and not tools[root].get("changes_something"):
                    lookups.setdefault(root)

    for n, step in enumerate(flat, 1):
        verb = _verb(step)
        notes: list[str] = []
        entry: dict[str, Any] = {
            "n": n, "kind": verb,
            "when": [{"expr": c, "text": english(c)} for c in step["_when"]],
        }
        for cond in step["_when"]:
            note_lookups(cond)
        if step.get("_in"):
            entry["in_repeat"] = step["_in"]

        if verb == "repeat":
            name = step["_repeat"]
            collected_at[name] = n
            entry["repeat"] = name
            entry["text"] = (f"Repeat the questions below once per item, until the answer to "
                             f"“{step['_question']}” is no.")
            notes.append(f"Each item's answers become one record of `{name}`, the list the steps after "
                         "it read. At least one item is collected.")
            notes.append("Only the user can say whether there is another item.")
        elif verb == "chat":
            listed = step.get("tools")
            names = ([str(t) for t in listed] if isinstance(listed, list)
                     else [t for t in str(listed or "").replace(",", " ").split() if t])
            entry["text"] = f"Answer the user's questions: “{step.get('chat')}”"
            entry["tools"] = names
            notes.append("The model may call only " + _list_of(f"`{t}`" for t in names)
                         + ", which only look things up. Nothing can change here.")
            fixed = [i["name"] for t in names for i in (tools.get(t, {}).get("inputs") or [])
                     if i["name"] in asked_at]
            if fixed:
                notes.append("Fixed to the answers above, so the model cannot change them: "
                             + ", ".join(f"`{f}`" for f in dict.fromkeys(fixed)) + ".")
            notes.append("The conversation stays here until the user moves on.")
            for t in names:
                note_lookups("{{" + t + "}}")
        elif verb == "ask":
            asks = _asks(step)
            for name, _q in asks:
                asked_at.setdefault(name, n)
            name, question = asks[0] if asks else ("", "")
            spec = fields.get(name)
            if len(asks) == 1:
                entry["text"] = f"Ask: “{question or 'What is the ' + _words(name) + '?'}”"
            else:
                entry["text"] = "Ask, one after another: " + "; ".join(f"“{q or _words(n_)}”" for n_, q in asks)
            entry["names"] = [n_ for n_, _ in asks]
            if step.get("from"):
                source = str(step["from"]).strip("{} ")
                notes.append(f"The answer is picked from a list: the rows `{source}` returns.")
                note_lookups("{{" + source + "}}")
            elif isinstance(step.get("choices"), list):
                notes.append("The answer is one of: " + ", ".join(_words(c) for c in step["choices"]) + ".")
            elif spec is not None and spec.sensitive:
                notes.append("The answer is a secret: masked on screen, never logged.")
            elif spec is not None and spec.type in _KIND and spec.type != "string":
                notes.append(f"The answer is {_KIND[spec.type]}.")
            elif step.get("type"):
                notes.append(f"The answer is {str(step['type'])}.")
            pattern = step.get("pattern") or (spec.pattern if spec is not None else None)
            if pattern:
                notes.append(f"It must look like: {_SHAPES.get(str(pattern), '`' + str(pattern) + '`')}.")
            if spec is not None and not spec.required:
                notes.append("It may be left blank.")
            for cond, sentence in _rules(step):
                notes.append(f"Refused, and asked again, unless {english(cond)}: “{sentence}”")
                note_lookups(cond, sentence)
            if step.get("widget"):
                notes.append(f"Shown with the designed widget `{step['widget']}`.")
            if step.get("_in"):
                notes.append(f"Asked once per item of `{step['_in']}`.")
            if any(n_ in user_only for n_, _ in asks):
                notes.append("Only the user can give this answer. A value the user did not type or pick is dropped.")
                entry["user_only"] = True
        elif verb == "confirm":
            who = str(step["by"]).strip() if isinstance(step.get("by"), str) else ""
            runs = released.get(n, [])
            lets = "A yes lets " + _list_of(f"`{t}`" for t in runs) + " run" if runs else ""
            if who:
                entry["text"] = f"Ask {who} for a yes: “{_confirm_question(step)}”"
                entry["asked_of"] = who
                notes.append(
                    f"The flow waits here until {who} answers. Neither the user in the conversation "
                    "nor the model can answer for them."
                )
                if lets:
                    notes.append(f"{lets}. {who.capitalize()} is shown which tool will run and with which inputs.")
                notes.append("“No” stops the flow here: nothing below runs.")
            else:
                entry["text"] = f"Ask for a yes: “{_confirm_question(step)}”"
                if lets:
                    notes.append(
                        f"{lets}. The user sees which tool will run and with which inputs before answering."
                        if not step.get("widget") else f"{lets}, on the designed page `{step['widget']}`."
                    )
                notes.append("“No” stops the flow here: nothing below runs. Only the user can answer.")
                entry["user_only"] = True
            note_lookups(*(step.get("with") or {}).values() if isinstance(step.get("with"), dict) else ())
        elif verb == "do":
            target = str(step.get("do") or "")
            info = tools.get(target, {})
            if is_skill(target):
                entry["text"] = f"Hand over to the skill “{target}”."
                entry["skill"] = target
                notes.append("It asks its own questions and keeps its own rules. When it finishes, this flow carries on.")
                notes.append(
                    f"If it is cancelled or the user says no in it, say “{step['else']}” and stop."
                    if step.get("else") else "If it is cancelled or the user says no in it, this flow stops here."
                )
            elif changes(target):
                entry["text"] = f"Run `{target}`" + (f": {info['description']}" if info.get("description") else ".")
                entry["tool"] = target
                entry["changes_something"] = True
                gate = yes_for.get(n)
                notes.append(
                    "This changes something. It runs once, and only after the yes at step "
                    f"{gate}." if gate else "This changes something. It runs once."
                )
            else:
                entry["text"] = f"Look up `{target}`" + (f": {info['description']}" if info.get("description") else ".")
                entry["tool"] = target
            if isinstance(step.get("with"), dict) and not is_skill(target):
                notes.append("Given: " + ", ".join(f"{k} = {v}" for k, v in step["with"].items()) + ".")
                note_lookups(*step["with"].values())
        else:
            text = str(step.get("say") or "")
            whole = re.fullmatch(r"\s*\{\{\s*([\w.]+)\s*\}\}\s*", text)
            if whole and step.get("title"):
                entry["text"] = f"Show `{whole.group(1)}` as a card titled “{step['title']}”."
            elif whole:
                entry["text"] = f"Say what `{whole.group(1)}` returned (a record or a list is shown as a card)."
            else:
                entry["text"] = f"Say: “{text}”"
            if step.get("widget"):
                notes.append(f"Shown with the designed widget `{step['widget']}`.")
                if isinstance(step.get("with"), dict):
                    note_lookups(*step["with"].values())
            note_lookups(text)
        entry["notes"] = notes
        rendered.append(entry)

    writes = _writes(flat, rendered, yes_for, confirms, asked_at, user_only, tools, changes, collected_at)
    return {
        "name": getattr(front, "name", ""),
        "description": " ".join(str(getattr(front, "description", "") or "").split()),
        "steps": rendered,
        "paths": _paths(flat, fields, tools, changes, is_skill),
        "writes": writes,
        "lookups": [
            {"name": t, "description": tools[t].get("description", "")} for t in lookups
        ],
    }


def _list_of(items: Any) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _writes(flat, rendered, yes_for, confirms, asked_at, user_only, tools, changes,
            collected_at=None) -> list[dict[str, Any]]:
    """For each tool that changes something: what has to be true first."""
    out: list[dict[str, Any]] = []
    for n, step in enumerate(flat, 1):
        target = str(step.get("do") or "")
        if _verb(step) != "do" or not rendered[n - 1].get("changes_something"):
            continue
        needs: list[str] = []
        for c_no, _name, c_when, c_question in confirms:
            if c_no >= n:
                continue
            asked_of = flat[c_no - 1].get("by")
            who = f"{str(asked_of).strip().capitalize()} said" if isinstance(asked_of, str) else "The user said"
            only = "" if set(c_when) <= set(step["_when"]) else (
                " (asked only when " + " and ".join(english(c) for c in c_when) + ")"
            )
            needs.append(f"{who} yes to “{c_question}” (step {c_no}){only}.")
        for cond in step["_when"]:
            needs.append(f"{english(cond).capitalize()}.")
        for m, earlier in enumerate(flat[: n - 1], 1):
            if _verb(earlier) != "ask":
                continue
            guard = "" if set(earlier["_when"]) <= set(step["_when"]) else (
                " (when " + " and ".join(english(c) for c in earlier["_when"]) + ")"
            )
            for cond, _sentence in _rules(earlier):
                needs.append(f"{english(cond).capitalize()} (step {m}){guard}.")
        info = tools.get(target, {})
        bound = step.get("with") if isinstance(step.get("with"), dict) else {}
        inputs: list[dict[str, Any]] = []
        names = [i["name"] for i in info.get("inputs") or []]
        if info.get("takes_everything"):
            names = [a for a, at in asked_at.items() if at < n]
            names += [a for a, at in (collected_at or {}).items() if at < n]
        for name in names:
            source = str(bound.get(name, "")) if name in bound else ""
            asked = asked_at.get(name)
            listed = (collected_at or {}).get(name)
            if listed and not source:
                inputs.append({"name": name, "from": f"the list collected at step {listed}",
                               "who": "the user, one item at a time"})
                continue
            inputs.append({
                "name": name,
                "from": f"passed: {source}" if source else (f"the answer at step {asked}" if asked else "not asked (the tool's own default)"),
                "who": "only the user" if name in user_only else ("the user, or the model from what the user said" if asked else ""),
            })
        out.append({
            "tool": target, "step": n, "description": info.get("description", ""),
            "needs": needs, "inputs": inputs,
        })
    return out


# --------------------------------------------------------------------------- every way through
def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(parts)]) if isinstance(node, ast.Name) and parts else None


def _paths(flat, fields, tools, changes, is_skill) -> dict[str, Any]:
    """The distinct ways through the flow, found by trying the answers that matter.

    A condition over answers is evaluated for real, on values chosen to sit on
    each side of every comparison (so two conditions that cannot both hold never
    produce a path). A condition that reads a tool's result is taken both ways.
    """
    conditions: list[str] = []
    for step in flat:
        for cond in step["_when"]:
            if cond not in conditions:
                conditions.append(cond)
    if not conditions:
        return {"conditions": [], "rows": [_row(flat, set(conditions), {}, changes, is_skill)], "exact": True}

    asked = {name for step in flat if _verb(step) == "ask" for name, _ in _asks(step)}
    confirm_names: set[str] = set()
    seen = 0
    for step in flat:
        if _verb(step) == "confirm":
            confirm_names.add(_confirm_name(step, seen))
            seen += 1

    # What each condition reads: answers (tried on real values) or tool results
    # (taken both ways, since only the tool knows).
    opaque: list[str] = []
    candidates: dict[str, list[Any]] = {}
    for cond in conditions:
        try:
            tree = ast.parse(cond, mode="eval")
            roots = free_vars(cond)
        except Exception:  # noqa: BLE001
            opaque.append(cond)
            continue
        if not roots <= asked or any(_dotted(n) for n in ast.walk(tree)):
            opaque.append(cond)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            sides = [node.left, *node.comparators]
            names = [s.id for s in sides if isinstance(s, ast.Name)]
            for side in sides:
                if isinstance(side, ast.Constant) and not isinstance(side.value, bool):
                    for name in names:
                        bucket = candidates.setdefault(name, [])
                        if isinstance(side.value, (int, float)):
                            bucket.extend([side.value - 1, side.value, side.value + 1])
                        else:
                            bucket.extend([side.value, "\u0000 something else"])
        for root in roots:
            spec = fields.get(root)
            bucket = candidates.setdefault(root, [])
            if spec is not None and spec.type == "enum" and spec.values:
                # A choice can only be one of its choices: nothing else is tried.
                candidates[root] = list(spec.values)
            elif spec is not None and spec.type == "boolean":
                candidates[root] = [True, False]
            elif not bucket:
                bucket.extend([True, False])
    for name, bucket in candidates.items():
        unique: list[Any] = []
        for value in bucket:
            if not any(value == u and type(value) is type(u) for u in unique):
                unique.append(value)
        candidates[name] = unique

    names = sorted(candidates)
    total = 2 ** len(opaque)
    for name in names:
        total *= max(1, len(candidates[name]))
    if total > MAX_COMBINATIONS:
        return {"conditions": [{"expr": c, "text": english(c)} for c in conditions], "rows": [],
                "exact": False, "too_many": total}

    seen_rows: dict[tuple[int, ...], dict[str, Any]] = {}
    for values in itertools.product(*(candidates[n] for n in names)) if names else [()]:
        env = dict(zip(names, values))
        for forced in itertools.product([True, False], repeat=len(opaque)):
            holds: dict[str, bool] = dict(zip(opaque, forced))
            for cond in conditions:
                if cond in holds:
                    continue
                try:
                    holds[cond] = bool(eval_strict(cond, env))
                except Exception:  # noqa: BLE001 - cannot hold on these answers
                    holds[cond] = False
            true = {c for c, v in holds.items() if v}
            row = _row(flat, true, holds, changes, is_skill)
            seen_rows.setdefault(tuple(row["steps"]), row)

    rows = sorted(seen_rows.values(), key=lambda r: (-len(r["steps"]), r["steps"]))
    # A condition that comes out the same on every path tells the reader nothing.
    varying = [c for c in conditions if len({r["holds"].get(c) for r in rows}) > 1]
    for row in rows:
        row["holds"] = {c: row["holds"][c] for c in varying}
    return {
        "conditions": [{"expr": c, "text": english(c)} for c in varying],
        "rows": rows,
        "exact": not opaque,
    }


def _row(flat, true: set[str], holds: dict[str, bool], changes, is_skill) -> dict[str, Any]:
    steps: list[int] = []
    asks: list[str] = []
    runs: list[str] = []
    hands: list[str] = []
    yeses = 0
    for n, step in enumerate(flat, 1):
        if not set(step["_when"]) <= true:
            continue
        steps.append(n)
        verb = _verb(step)
        if verb == "ask":
            asks.extend(name for name, _ in _asks(step))
        elif verb == "confirm":
            yeses += 1
        elif verb == "do":
            target = str(step.get("do") or "")
            if is_skill(target):
                hands.append(target)
            elif changes(target):
                runs.append(target)
    return {"steps": steps, "holds": dict(holds), "asks": asks, "yeses": yeses, "runs": runs, "hands_to": hands}


# --------------------------------------------------------------------------- as a document
def markdown(page: dict[str, Any]) -> str:
    """The procedure as a Markdown document: to paste, print or diff."""
    out = [f"# {_words(page['name']).capitalize()}", "", page["description"], "", "## The procedure", ""]
    for step in page["steps"]:
        only = ""
        if step["when"]:
            only = " *(only when " + " and ".join(w["text"] for w in step["when"]) + ")*"
        out.append(f"{step['n']}. {step['text']}{only}")
        for note in step["notes"]:
            out.append(f"   - {note}")
    paths = page["paths"]
    out += ["", "## Every way through it", ""]
    if paths.get("too_many"):
        out.append(f"There are too many combinations of answers to list ({paths['too_many']}).")
    else:
        rows = paths["rows"]
        out.append(f"{len(rows)} way{'s' if len(rows) != 1 else ''} through this flow"
                   + ("." if paths["exact"] else ", taking each tool result both ways."))
        out.append("")
        head = ["#", *[c["text"] for c in paths["conditions"]], "Asks", "Yeses", "Runs"]
        out.append("| " + " | ".join(head) + " |")
        out.append("|" + "---|" * len(head))
        for i, row in enumerate(rows, 1):
            cells = [str(i), *["yes" if row["holds"].get(c["expr"]) else "no" for c in paths["conditions"]],
                     ", ".join(row["asks"]) or "nothing", str(row["yeses"]),
                     ", ".join(row["runs"] + [f"hands to {s}" for s in row["hands_to"]]) or "nothing"]
            out.append("| " + " | ".join(cells) + " |")
        out += ["", "At every confirmation, “no” ends the flow there."]
    if page["writes"]:
        out += ["", "## Before anything changes", ""]
        for write in page["writes"]:
            out.append(f"**`{write['tool']}`** (step {write['step']})"
                       + (f": {write['description']}" if write["description"] else ""))
            for need in write["needs"]:
                out.append(f"- {need}")
            for item in write["inputs"]:
                who = f" — {item['who']}" if item["who"] else ""
                out.append(f"- Input `{item['name']}`: {item['from']}{who}.")
            out.append("- It runs at most once, and each run is on the audit trail (a secret is redacted).")
            out.append("")
    if page["lookups"]:
        out += ["## What it looks up", ""]
        for tool in page["lookups"]:
            out.append(f"- `{tool['name']}`" + (f": {tool['description']}" if tool["description"] else ""))
        out.append("")
        out.append("A lookup runs when a step first needs its result, and again when an answer it used changes.")
    return "\n".join(out).rstrip() + "\n"


def for_skill(registry: Any, name: str) -> dict[str, Any]:
    """The procedure of a loaded skill, with its tools described."""
    from .skills import parse_skill_md
    from .toolbox import list_tools

    skill = registry.get(name)
    front, _ = parse_skill_md((skill.path / "SKILL.md").read_text(encoding="utf-8"))
    if getattr(front, "steps", None) is None:
        return {
            "name": skill.name, "description": " ".join(str(skill.description or "").split()),
            "steps": [], "paths": {"conditions": [], "rows": [], "exact": True}, "writes": [], "lookups": [],
            "unsupported": (
                "This skill is not written as steps, so there is no procedure to read back. "
                "A skill written with `steps:` is rendered here from the file itself."
            ),
        }
    tools = {t["name"]: t for t in (list_tools(registry.tools) if getattr(registry, "tools", None) else [])}
    for own in getattr(skill, "own_tools", None) or []:
        tools.setdefault(own["name"], own)
    page = describe(front, skill, tools, {s.name for s in registry.all()} - {skill.name})
    page["markdown"] = markdown(page)
    return page


if __name__ == "__main__":  # pragma: no cover - a convenience for the terminal
    import sys

    from .skills import load_skills_dir

    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m aigenos_flow.procedure <skills folder> <skill name>")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(for_skill(load_skills_dir(sys.argv[1]), sys.argv[2])["markdown"])

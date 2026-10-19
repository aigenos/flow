"""Flow outlines: a canvas-renderable view of ANY skill's control flow.

Short-form skills return their ``steps:`` as one column, and long-form ones
their ``flow:`` steps verbatim (both editable — the canvas round-trips them
back into SKILL.md). Imperative skills (``@flow`` in
handlers.py) can't be round-tripped, but their ACTUAL control flow can be
shown: we AST-walk the flow function and extract the ``ctx.*`` call sequence,
annotating steps inside ``if`` blocks with the branch condition. That view is
explicitly approximate and read-only — the editable artifact for a Python
skill is handlers.py itself.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .skills import Skill, parse_skill_md

_MAX_TEXT = 80


def outline_skill(skill: Skill) -> dict[str, Any]:
    """{mode, editable, steps, note?} for the canvas."""
    return outline_folder(Path(skill.path))


def outline_folder(folder: Path) -> dict[str, Any]:
    """Outline a skill directory: declarative steps verbatim (editable), else
    a read-only AST outline of the imperative @flow, else an empty canvas."""
    skill_md = folder / "SKILL.md"
    fm = None
    if skill_md.exists():
        try:
            fm, _ = parse_skill_md(skill_md.read_text(encoding="utf-8"))
        except ValueError:
            fm = None

    if fm is not None and isinstance(fm.flow, list):
        return {
            "mode": "declarative",
            "editable": True,
            "steps": fm.flow,
        }

    if fm is not None and isinstance(getattr(fm, "steps", None), list):
        # The short form, as the canvas's column: the shape the Studio reads
        # from the file itself, so the two never disagree.
        return {
            "mode": "steps",
            "editable": True,
            "steps": _flatten_steps(fm.steps),
        }

    handlers = folder / (fm.handlers if fm else "handlers.py")
    if handlers.exists():
        steps = outline_handlers(handlers.read_text(encoding="utf-8"))
        if steps:
            return {
                "mode": "imperative",
                "editable": False,
                "steps": steps,
                "note": (
                    "Python skill — this is an approximate, read-only outline of "
                    "the @flow in handlers.py. Edit the code in the skill editor, "
                    "or author a declarative copy to edit visually."
                ),
            }

    return {"mode": "none", "editable": True, "steps": []}


def _flatten_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """Short-form steps as one column, the way the canvas draws them.

    A block at the top level is a header, ``{"_block": {"id": "b1", "repeat"?,
    "when"?}}``, followed by its steps, each marked ``"_in": "b1"``; a block
    nested deeper puts its condition on each of its steps. A column holds one
    level of blocks, so a `when:` block that holds a `repeat:` is written out as
    steps carrying its condition, with the repeat a block of its own. The loader
    reads both the same way; the file keeps its blocks.
    """
    top: list[Any] = []
    for step in steps:
        if _is_block(step) and step.get("repeat") is None and _holds_repeat(step["steps"]):
            top.extend(_hoist([step], None))
        else:
            top.append(step)
    out: list[dict[str, Any]] = []
    n = 0
    for step in top:
        if not isinstance(step, dict):
            continue
        if _is_block(step):
            n += 1
            block: dict[str, Any] = {"id": f"b{n}"}
            if isinstance(step.get("repeat"), dict):
                block["repeat"] = dict(step["repeat"])
            if step.get("when") is not None:
                block["when"] = str(step["when"])
            out.append({"_block": block})
            out.extend({**inner, "_in": block["id"]} for inner in _merge_into(step["steps"]))
            continue
        out.append(dict(step))
    return out


def _is_block(step: Any) -> bool:
    return isinstance(step, dict) and isinstance(step.get("steps"), list)


def _both(guard: str | None, own: Any) -> str | None:
    cond = str(own) if own is not None else None
    return cond if guard is None else (guard if cond is None else f"({guard}) and ({cond})")


def _merge_into(steps: list[Any], guard: str | None = None) -> list[dict[str, Any]]:
    """A block's steps, each nested block's condition put on each of its steps."""
    out: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        both = _both(guard, step.get("when"))
        if _is_block(step):
            out.extend(_merge_into(step["steps"], both))
            continue
        shown = {k: v for k, v in step.items() if k != "when"}
        if both:
            shown["when"] = both
        out.append(shown)
    return out


def _holds_repeat(steps: list[Any]) -> bool:
    return any(_is_block(s) and (s.get("repeat") is not None or _holds_repeat(s["steps"])) for s in steps)


def _hoist(steps: list[Any], guard: str | None) -> list[Any]:
    """`when:` blocks written out as top-level steps carrying their condition; a repeat stays a block."""
    out: list[Any] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        both = _both(guard, step.get("when"))
        if _is_block(step) and step.get("repeat") is None:
            out.extend(_hoist(step["steps"], both))
            continue
        shown = {k: v for k, v in step.items() if k != "when"}
        if both:
            shown["when"] = both
        out.append(shown)
    return out


# --------------------------------------------------------------------------- #
def outline_handlers(source: str) -> list[dict[str, Any]]:
    """Extract the ctx.* call sequence from the (single) @flow function."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    fn = _find_flow_def(tree)
    if fn is None:
        return []
    steps: list[dict[str, Any]] = []
    _walk(fn.body, steps, guard=None)
    return steps


def _find_flow_def(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                name = dec.func.id if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) else (
                    dec.id if isinstance(dec, ast.Name) else None
                )
                if name == "flow":
                    return node  # type: ignore[return-value]
    return None


def _txt(node: ast.AST) -> str:
    try:
        s = ast.unparse(node)
    except Exception:  # pragma: no cover — unparse is total on parsed trees
        s = "…"
    return s if len(s) <= _MAX_TEXT else s[: _MAX_TEXT - 1] + "…"


def _const_str(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _ctx_call(node: ast.AST) -> tuple[str, ast.Call] | None:
    """(method, call) when node is a ``ctx.<method>(...)`` call expression."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "ctx":
        return f.attr, node
    return None


def _step_for(method: str, call: ast.Call) -> dict[str, Any]:
    args = call.args
    kwargs = {k.arg: k.value for k in call.keywords if k.arg}

    if method in ("ask", "collect") and args:
        return {"ask": _const_str(args[0]) or _txt(args[0])}
    if method == "say" and args:
        return {"say": _const_str(args[0]) or _txt(args[0])}
    if method in ("run", "action") and args:
        name = kwargs.get("name")
        label = (_const_str(name) if name is not None else None) or _txt(args[0])
        return {"do": label}
    if method == "widget" and args:
        step: dict[str, Any] = {"widget": _const_str(args[0]) or _txt(args[0])}
        save = kwargs.get("save")
        if isinstance(save, (ast.List, ast.Tuple)):
            step["save"] = [_const_str(e) or _txt(e) for e in save.elts]
        return step
    if method == "show" and args:
        step = {"show": _txt(args[0])}
        title = kwargs.get("title")
        if title is not None:
            step["title"] = _const_str(title) or _txt(title)
        return step
    if method == "chat":
        cfg: dict[str, Any] = {}
        if args and isinstance(args[0], ast.Dict):
            for k, v in zip(args[0].keys, args[0].values):
                key = _const_str(k) if k is not None else None
                if key == "tools" and isinstance(v, (ast.List, ast.Tuple)):
                    cfg["tools"] = [_const_str(e) or _txt(e) for e in v.elts]
                elif key == "greeting":
                    cfg["greeting"] = _const_str(v) or _txt(v)
        return {"chat": cfg}
    if method == "link" and args:
        target = _const_str(args[0]) or _txt(args[0])
        return {"code": f"hand off to {target}, then come back here"}
    # set / end / anything else → generic code step
    return {"code": f"ctx.{method}({', '.join(_txt(a) for a in call.args)})"}


#: ctx calls that are steps in their own right even when written inside a
#: condition, as in ``if ctx.ask("yes_or_no"):`` or ``if not ctx.link("other"):``.
_STEPS_IN_CONDITIONS = ("ask", "collect", "link", "run", "widget")


def _steps_in_condition(test: ast.AST) -> list[tuple[str, ast.Call]]:
    hits = [hit for node in ast.walk(test) if (hit := _ctx_call(node)) and hit[0] in _STEPS_IN_CONDITIONS]
    return sorted(hits, key=lambda hit: (hit[1].lineno, hit[1].col_offset))


def _attach_guard(step: dict[str, Any], guard: str | None) -> dict[str, Any]:
    if guard:
        step["when"] = guard
    return step


def _walk(body: list[ast.stmt], steps: list[dict[str, Any]], guard: str | None) -> None:
    for stmt in body:
        # bare ctx.*(...) call, or  x = ctx.*(...)
        call_node: ast.AST | None = None
        if isinstance(stmt, ast.Expr):
            call_node = stmt.value
        elif isinstance(stmt, ast.Assign):
            call_node = stmt.value
        if call_node is not None:
            hit = _ctx_call(call_node)
            if hit:
                steps.append(_attach_guard(_step_for(*hit), guard))
                continue

        if isinstance(stmt, ast.If):
            # A question or a hand-off used AS the condition still happens, and
            # happens first: show it as its own step, then the guarded body.
            for hit in _steps_in_condition(stmt.test):
                steps.append(_attach_guard(_step_for(*hit), guard))
            cond = _txt(stmt.test)
            sub = cond if guard is None else f"{guard} and {cond}"
            _walk(stmt.body, steps, sub)
            if stmt.orelse:
                neg = f"not ({cond})"
                sub_else = neg if guard is None else f"{guard} and {neg}"
                _walk(stmt.orelse, steps, sub_else)
            continue

        if isinstance(stmt, ast.Return):
            steps.append(_attach_guard({"code": "return — flow ends here"}, guard))
            continue
        # other statements (locals, f-string prep, loops) are skipped: the
        # outline shows the conversational surface, not every line of code

"""The tool library: functions a skill names instead of defining.

A tool is a named function with typed inputs. It lives OUTSIDE any one skill —
in ``<agent folder>/tools/*.py`` (or ``tools.py``), in a directory you point
``tools_dir=`` at, or as a managed connector defined in the Studio — so that

* one function serves every skill that names it,
* a skill author never has to open the code that runs the agent, and
* the tool's own author says, once, whether it changes anything.

That last point is the safety line. A tool is treated as a WRITE unless it is
marked read-only::

    from aigenos_flow import readonly

    @readonly
    def find_order(order_id: str) -> dict: ...

    def cancel_order(order_id: str) -> dict: ...      # a write: needs a yes first

A skill cannot relabel it. Whoever writes the steps, a write does not run
without a ``confirm:`` before it, and it runs at most once.

Inputs are described by the function's own signature. Nothing is declared
twice: ``amount: float`` is a number box and a number check; ``Literal[...]``
is a choice; ``bool`` is yes/no; ``Secret`` is a masked value the model may
never supply.
"""
from __future__ import annotations

import asyncio
import collections.abc
import inspect
import json
import logging
import threading
import typing
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, Union

log = logging.getLogger(__name__)

_READONLY = "_flow_tool_readonly"
_SOURCE = "_flow_tool_source"
_BUILTIN = "_flow_tool_builtin"
_INFO = "_flow_tool_info"

#: Parameter names that mean "give me everything as one dict" (the older
#: ``def tool(args): ...`` style a ``scripts.py`` may still use).
_DICT_STYLE_NAMES = ("args", "arguments", "payload", "params")


class _SecretMarker:
    """Marks a parameter as a secret: masked, never logged, never model-supplied."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "Secret"


#: ``code: Secret`` — a one-time code, PIN or password the user holds.
Secret = Annotated[str, _SecretMarker()]


def readonly(fn: Any) -> Any:
    """Mark a tool as a lookup: it only reads, so it is safe to call again.

    Without this mark a tool is a write. That default is the safe one: a
    function nobody labelled can never run without the user's yes.
    """
    try:
        setattr(fn, _READONLY, True)
    except (AttributeError, TypeError):  # a builtin or a frozen object
        raise TypeError(f"cannot mark {fn!r} read-only; wrap it in a function") from None
    return fn


def is_readonly(fn: Any) -> bool:
    return getattr(fn, _READONLY, False) is True


# --------------------------------------------------------------------------- #
# What a tool takes
# --------------------------------------------------------------------------- #
@dataclass
class ParamInfo:
    """One input of a tool, in the terms a question needs."""

    name: str
    #: string | number | integer | boolean | enum | any
    type: str = "string"
    required: bool = True
    description: str = ""
    choices: list[str] | None = None
    secret: bool = False
    min: float | None = None
    max: float | None = None
    pattern: str | None = None


@dataclass
class ToolInfo:
    name: str
    readonly: bool
    #: "named" (``def f(order_id, amount)``), "dict" (``def f(args)``),
    #: "invoke" (a LangChain tool / managed connector, called with one dict) or
    #: "builtin" (one of ``BUILTINS``: the answers, minus secrets, and the run).
    style: str
    params: list[ParamInfo] = field(default_factory=list)
    doc: str = ""
    source: str = ""
    #: For a "dict" tool whose one parameter has no annotation and no telling
    #: name (``lambda a: ...``): that parameter's name. Such a callable has
    #: always been handed one dict; a skill that asks for an answer called
    #: exactly this means it as that single input instead (``as_named``).
    loose: str | None = None

    def param(self, name: str) -> ParamInfo | None:
        return next((p for p in self.params if p.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        """For the Studio's tool list and for a model choosing tools."""
        return {
            "name": self.name,
            "description": self.doc,
            "changes_something": not self.readonly,
            "source": self.source,
            # A tool written as `def tool(args)` gets every answer as one dict.
            "takes_everything": self.style in ("dict", "builtin"),
            "inputs": [
                {
                    "name": p.name, "type": "choice" if p.type == "enum" else p.type,
                    "required": p.required, "description": p.description,
                    **({"choices": p.choices} if p.choices else {}),
                    **({"secret": True} if p.secret else {}),
                }
                for p in self.params
            ],
        }


_SCALARS: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _param_from_annotation(name: str, annotation: Any, *, has_default: bool) -> ParamInfo:
    info = ParamInfo(name=name, required=not has_default)
    ann = annotation

    # Annotated[T, ...]: a bare string is the question, the rest are constraints.
    while typing.get_origin(ann) is Annotated:
        base, *meta = typing.get_args(ann)
        for m in meta:
            if isinstance(m, str) and not info.description:
                info.description = m
            elif isinstance(m, _SecretMarker):
                info.secret = True
            else:
                # pydantic's Field(...) and annotated-types (Ge, Le, ...) both
                # expose these names; read them without importing either.
                desc = getattr(m, "description", None)
                if isinstance(desc, str) and not info.description:
                    info.description = desc
                for inner in [m, *(getattr(m, "metadata", None) or [])]:
                    for attr, target in (("ge", "min"), ("gt", "min"), ("le", "max"), ("lt", "max")):
                        value = getattr(inner, attr, None)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            setattr(info, target, float(value))
                    pattern = getattr(inner, "pattern", None)
                    if isinstance(pattern, str):
                        info.pattern = pattern
        ann = base

    # Optional[T] / T | None: not required, and T is the type.
    origin = typing.get_origin(ann)
    if origin is Union or (origin is not None and getattr(origin, "__name__", "") == "UnionType"):
        members = [a for a in typing.get_args(ann) if a is not type(None)]
        if len(members) != len(typing.get_args(ann)):
            info.required = False if has_default else info.required
        if len(members) == 1:
            return _merge(info, _param_from_annotation(name, members[0], has_default=has_default))
        info.type = "any"
        return info

    if origin is Literal:
        values = list(typing.get_args(ann))
        if values and all(isinstance(v, bool) for v in values):
            info.type = "boolean"
        else:
            info.type = "enum"
            info.choices = [str(v) for v in values]
        return info

    if ann in _SCALARS:
        info.type = _SCALARS[ann]
    elif ann is inspect.Parameter.empty or ann is Any:
        info.type = "string"
    else:
        info.type = "any"  # a dict, a list, a class: bound from a result, never asked
    return info


def _merge(outer: ParamInfo, inner: ParamInfo) -> ParamInfo:
    """Keep what the Annotated wrapper said; take the type from inside it."""
    inner.required = outer.required
    inner.description = outer.description or inner.description
    inner.secret = outer.secret or inner.secret
    for attr in ("min", "max", "pattern"):
        if getattr(inner, attr) is None:
            setattr(inner, attr, getattr(outer, attr))
    return inner


_JSON_TYPES = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}


def params_from_json_schema(schema: dict[str, Any] | None) -> list[ParamInfo]:
    """A tool's inputs from its JSON Schema (an MCP tool's ``inputSchema``)."""
    schema = schema or {}
    required = set(schema.get("required") or [])
    out: list[ParamInfo] = []
    for name, spec in (schema.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        choices = spec.get("enum")
        kind = _JSON_TYPES.get(str(spec.get("type")), "any")
        out.append(ParamInfo(
            name=str(name),
            type="enum" if choices else kind,
            required=name in required,
            description=str(spec.get("description") or ""),
            choices=[str(c) for c in choices] if choices else None,
            min=spec.get("minimum") if isinstance(spec.get("minimum"), (int, float)) else None,
            max=spec.get("maximum") if isinstance(spec.get("maximum"), (int, float)) else None,
            pattern=spec.get("pattern") if isinstance(spec.get("pattern"), str) else None,
        ))
    return out


def _hinted_readonly(raw: Any) -> bool:
    """An MCP server's ``readOnlyHint``, as LangChain's MCP adapters carry it in ``metadata``.

    The SDK spells it ``readOnlyHint`` (1.x) or ``read_only_hint`` (2.x). Like
    ``MCPTools``, this is the server's statement about its own tool.
    """
    meta = getattr(raw, "metadata", None)
    if not isinstance(meta, dict):
        return False
    return meta.get("readOnlyHint") is True or meta.get("read_only_hint") is True


def _first_line(text: str | None) -> str:
    for line in (text or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


def describe_tool(name: str, raw: Any, descriptor: Any = None) -> ToolInfo:
    """Everything the flow needs to know about a tool, from the tool itself."""
    source = str(getattr(raw, _SOURCE, "") or "")

    if getattr(raw, _BUILTIN, False) is True:
        return ToolInfo(
            name=name, readonly=is_readonly(raw), style="builtin",
            doc=_first_line(getattr(raw, "__doc__", None)), source="built in",
        )

    # A tool that describes itself (one that came from an MCP server does).
    attached = getattr(raw, _INFO, None)
    if isinstance(attached, ToolInfo):
        return ToolInfo(
            name=name, readonly=attached.readonly or is_readonly(raw), style=attached.style,
            params=list(attached.params), doc=attached.doc, source=attached.source or source,
        )

    # A managed connector operation already carries a typed descriptor.
    if descriptor is not None and getattr(descriptor, "parameters", None) is not None:
        params = []
        for pname, spec in descriptor.parameters.items():
            ptype = getattr(spec, "type", "string")
            choices = getattr(spec, "enum", None)
            params.append(ParamInfo(
                name=pname,
                type="enum" if choices else (ptype if ptype in ("string", "number", "integer", "boolean") else "any"),
                required=bool(getattr(spec, "required", True)),
                description=getattr(spec, "description", "") or "",
                choices=[str(c) for c in choices] if choices else None,
                secret=bool(getattr(spec, "sensitive", False)),
                min=getattr(spec, "minimum", None),
                max=getattr(spec, "maximum", None),
                pattern=getattr(spec, "pattern", None),
            ))
        return ToolInfo(
            name=name, readonly=not bool(getattr(descriptor, "side_effecting", True)),
            style="invoke", params=params,
            doc=getattr(descriptor, "description", "") or "", source=source or "connector",
        )

    # A LangChain-style tool: a pydantic args schema and .invoke(dict).
    schema = getattr(raw, "args_schema", None)
    if hasattr(raw, "invoke") and schema is not None and hasattr(schema, "model_fields"):
        params = []
        for pname, f in schema.model_fields.items():
            required = f.is_required() if hasattr(f, "is_required") else True
            p = _param_from_annotation(pname, f.annotation, has_default=not required)
            p.description = p.description or (getattr(f, "description", None) or "")
            params.append(p)
        return ToolInfo(
            name=name, readonly=is_readonly(raw) or _hinted_readonly(raw), style="invoke", params=params,
            doc=_first_line(getattr(raw, "description", None)), source=source,
        )
    # ... or JSON Schema, which is what LangChain's MCP adapters hand over.
    if hasattr(raw, "invoke") and isinstance(schema, dict):
        return ToolInfo(
            name=name, readonly=is_readonly(raw) or _hinted_readonly(raw), style="invoke",
            params=params_from_json_schema(schema),
            doc=_first_line(getattr(raw, "description", None)), source=source or "langchain",
        )

    target = inspect.unwrap(raw) if callable(raw) else raw
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError):
        return ToolInfo(name=name, readonly=is_readonly(raw), style="dict", doc="", source=source)
    try:
        hints = typing.get_type_hints(target, include_extras=True)
    except Exception:  # noqa: BLE001 - one name we cannot resolve must not blank the rest
        hints = {}
        scope = dict(getattr(target, "__globals__", {}) or {})
        for pname, p in signature.parameters.items():
            if not isinstance(p.annotation, str):
                continue
            try:
                hints[pname] = eval(p.annotation, scope)  # noqa: S307 - the tool's own annotation
            except Exception:  # noqa: BLE001
                hints[pname] = inspect.Parameter.empty  # unknown: treat as text

    declared = [
        p for p in signature.parameters.values()
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    ]
    # One required input that is "everything as a dict": the older contract for
    # a callable handed in through `tools=` (`def tool(args)`, `lambda a: ...`).
    needed = [p for p in declared if p.default is inspect.Parameter.empty]
    if len(needed) == 1 and declared and declared[0] is needed[0]:
        only = needed[0]
        ann = hints.get(only.name, only.annotation)
        origin = typing.get_origin(ann) or ann
        is_mapping = origin in (dict, collections.abc.Mapping, collections.abc.MutableMapping)
        bare = ann is inspect.Parameter.empty
        if is_mapping or only.name in _DICT_STYLE_NAMES or (bare and not source):
            return ToolInfo(
                name=name, readonly=is_readonly(raw) or is_readonly(target), style="dict",
                doc=_first_line(getattr(target, "__doc__", None)), source=source,
                # Not said in so many words: a skill that asks for an answer by
                # this very name plainly means it as that one input.
                loose=None if (is_mapping or only.name in _DICT_STYLE_NAMES) else only.name,
            )
    params = [
        _param_from_annotation(
            p.name, hints.get(p.name, p.annotation),
            has_default=p.default is not inspect.Parameter.empty,
        )
        for p in declared
    ]
    return ToolInfo(
        name=name, readonly=is_readonly(raw) or is_readonly(target), style="named", params=params,
        doc=_first_line(getattr(target, "__doc__", None)), source=source,
    )


def attach_info(fn: Any, info: ToolInfo) -> Any:
    """Make ``fn`` say what it takes and whether it only reads.

    For a callable whose signature cannot say it: a function that forwards one
    dict to a remote tool, say. ``info.style`` is how it is called: ``"invoke"``
    means "with one dict holding exactly the inputs listed".
    """
    setattr(fn, _INFO, info)
    return fn


def as_named(info: ToolInfo) -> ToolInfo:
    """``info`` read as a tool with one named text input (see ``ToolInfo.loose``)."""
    assert info.loose
    return ToolInfo(
        name=info.name, readonly=info.readonly, style="named",
        params=[ParamInfo(name=info.loose)], doc=info.doc, source=info.source,
    )


def call_tool(info: ToolInfo, raw: Any, normalized: Callable[..., Any], values: dict[str, Any],
              context: Any = None) -> Any:
    """Call a tool with ``values`` in the way that tool expects to be called."""
    if info.style == "named":
        return settle(raw(**values))
    if info.style == "dict":
        return settle(raw(dict(values)))
    if info.style == "builtin":
        return settle(raw(dict(values), dict(context or {})))
    return normalized(dict(values), context)


# --------------------------------------------------------------------------- #
# Async tools
# --------------------------------------------------------------------------- #
_ASYNC_LOOP: asyncio.AbstractEventLoop | None = None
_ASYNC_THREAD: threading.Thread | None = None
_ASYNC_LOCK = threading.Lock()


def _async_loop() -> asyncio.AbstractEventLoop:
    """The one event loop, on its own thread, that every async tool runs on.

    A step calls its tool from synchronous code, often on a thread whose own
    loop is busy running the graph, where ``asyncio.run`` is not allowed. One
    long-lived loop also lets a tool keep a client it opened on an earlier call.
    """
    global _ASYNC_LOOP, _ASYNC_THREAD
    with _ASYNC_LOCK:
        if _ASYNC_LOOP is None or _ASYNC_LOOP.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="aigenos-flow-async-tools", daemon=True)
            thread.start()
            _ASYNC_LOOP, _ASYNC_THREAD = loop, thread
        return _ASYNC_LOOP


def settle(result: Any) -> Any:
    """A tool's result, waited for when the tool is async (``async def``)."""
    if not inspect.isawaitable(result):
        return result
    loop = _async_loop()
    if threading.current_thread() is _ASYNC_THREAD:
        close = getattr(result, "close", None)
        if callable(close):
            close()
        raise RuntimeError(
            "an async tool started a flow step that calls another async tool; "
            "call the second tool directly with await instead"
        )

    async def finish() -> Any:
        return await result

    return asyncio.run_coroutine_threadsafe(finish(), loop).result()


def _async_only(tool: Any) -> bool:
    """A LangChain tool that can only be awaited (``coroutine=`` and no ``func=``)."""
    return getattr(tool, "coroutine", None) is not None and getattr(tool, "func", None) is None


def _is_blocks(value: Any) -> bool:
    """LangChain message content: a list of ``{"type": "text", ...}`` blocks or strings."""
    return isinstance(value, list) and bool(value) and all(
        isinstance(b, str) or (isinstance(b, dict) and "type" in b) for b in value
    )


def _from_content(content: Any) -> Any:
    """Message content as the value a step binds: JSON text becomes its object."""
    if _is_blocks(content):
        content = "\n".join(
            b if isinstance(b, str) else str(b.get("text") or "")
            for b in content
            if isinstance(b, str) or b.get("type") == "text"
        )
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except ValueError:
            return content
        return parsed if isinstance(parsed, dict) else content
    return content


def invoke_langchain(tool: Any, args: dict[str, Any]) -> Any:
    """Call a LangChain tool with one dict of inputs and return its value.

    An async-only tool (LangChain's MCP adapters make these) is awaited on the
    background loop. A tool that answers with content and an artifact (an MCP
    tool's text plus its structured result) is called the way an agent calls it,
    with a tool call, so the structured result is not lost.
    """
    run_async = _async_only(tool)
    if getattr(tool, "response_format", None) == "content_and_artifact":
        call = {"name": getattr(tool, "name", "tool"), "args": dict(args),
                "id": "aigenos-" + uuid.uuid4().hex[:12], "type": "tool_call"}
        message = settle(tool.ainvoke(call)) if run_async else tool.invoke(call)
        content = getattr(message, "content", message)
        if getattr(message, "status", None) == "error":
            raise RuntimeError(f"the tool {getattr(tool, 'name', '')!r} reported an error: {_from_content(content)}")
        artifact = getattr(message, "artifact", None)
        structured = (artifact.get("structured_content") if isinstance(artifact, dict)
                      else getattr(artifact, "structured_content", None))
        if isinstance(structured, dict):
            return structured.get("result", structured) if set(structured) == {"result"} else structured
        return _from_content(content)
    result = settle(tool.ainvoke(dict(args))) if run_async else tool.invoke(dict(args))
    return _from_content(result) if _is_blocks(result) else result


# --------------------------------------------------------------------------- #
# Built in: what a flow can do before anyone has written a tool
# --------------------------------------------------------------------------- #
def save_request(answers: dict, context: dict) -> dict:
    """Keep a record of this request and give it a reference.

    One JSON line per request in ``records.jsonl`` next to the skill: the
    answers (a secret is never among them), the skill, the time and the
    reference the user is told. It is a stand-in that makes a new flow run end
    to end; a tool of your own named ``save_request`` replaces it.
    """
    run = str(context.get("flow_run_id") or "") or uuid.uuid4().hex
    reference = "REQ-" + run[:6].upper()
    record = {
        "reference": reference,
        "skill": context.get("skill_name"),
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **answers,
    }
    folder = Path(str(context.get("skill_dir") or "."))
    with (folder / "records.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps(record, default=str) + "\n")
    return {"reference": reference, "status": "recorded"}


setattr(save_request, _BUILTIN, True)

#: Name -> function. Lowest precedence: the host's tools and the library win.
BUILTINS: dict[str, Any] = {"save_request": save_request}


def with_builtins(registry: Any) -> Any:
    """``registry`` plus every built-in tool it does not already define."""
    from .declarative import ToolRegistry

    if registry is not None and all(registry.has(n) for n in BUILTINS):
        return registry
    merged = ToolRegistry()
    if registry is not None:
        for name in registry.names():
            merged.register(name, registry.raw(name), descriptor=registry.descriptor(name))
    for name, fn in BUILTINS.items():
        if not merged.has(name):
            merged.register(name, fn)
    return merged


# --------------------------------------------------------------------------- #
# Finding the library
# --------------------------------------------------------------------------- #
def library_files(root: Path, tools_dir: Path | str | None = None) -> list[Path]:
    """The Python files that make up the tool library for a skills folder.

    ``<root>/tools.py``, every ``<root>/tools/*.py``, and every ``*.py`` in
    ``tools_dir`` when one is given. Files starting with ``_`` are helpers, not
    tools. Only the agent's own folder is searched by default: a ``tools/``
    somewhere else in a project is often build scripts, and importing those
    because of where they sit would run code nobody asked for.
    """
    files: list[Path] = []
    single = root / "tools.py"
    if single.is_file():
        files.append(single)
    for folder in (root / "tools", Path(tools_dir) if tools_dir else None):
        if folder is not None and folder.is_dir():
            files.extend(sorted(p for p in folder.glob("*.py") if not p.name.startswith("_")))
    return files


def collect_functions(module: Any) -> list[tuple[str, Any]]:
    """A library module's tools: an explicit ``TOOLS`` dict, else every public
    function defined in that file (classes and imports are not tools)."""
    exported = getattr(module, "TOOLS", None)
    if isinstance(exported, dict):
        return list(exported.items())
    return [
        (n, f)
        for n, f in vars(module).items()
        if not n.startswith("_")
        and (inspect.isfunction(f) or hasattr(f, "invoke"))
        and getattr(f, "__module__", module.__name__) == module.__name__
    ]


def load_library(root: Path, base: Any, tools_dir: Path | str | None = None) -> Any:
    """``base`` plus every tool in the library for ``root``.

    Tools the host passed in (``FlowAgent.from_dir(tools=...)``, managed
    connectors) win a name collision, so a file in a folder can never shadow an
    operation the operator manages. Two library files defining the same name is
    an error: a skill names a tool, and the name has to mean one thing.
    """
    from .declarative import ToolRegistry
    from .skills import _import_module_file

    files = library_files(root, tools_dir)
    if not files:
        return with_builtins(base)

    merged = ToolRegistry()
    seen: dict[str, Path] = {}
    for path in files:
        module = _import_module_file(path, "_flow_tools")
        for name, fn in collect_functions(module):
            if name in seen:
                raise ValueError(
                    f"tool {name!r} is defined in both {seen[name].name} and {path.name} — "
                    "a skill refers to a tool by name, so the name must mean one thing. "
                    "Rename one of them."
                )
            try:
                setattr(fn, _SOURCE, f"{path.parent.name}/{path.name}")
            except (AttributeError, TypeError):
                pass
            seen[name] = path
            merged.register(name, fn)

    if base is not None:
        shadowed = sorted(set(seen) & set(base.names()))
        if shadowed:
            log.warning(
                "tool library in %s defines %s, which the host already provides "
                "(the host's versions win)", root, shadowed,
            )
        for name in base.names():
            merged.register(name, base.raw(name), descriptor=base.descriptor(name))
    log.info("tool library for %s: %s", root, sorted(seen))
    return with_builtins(merged)


def list_tools(registry: Any) -> list[dict[str, Any]]:
    """Every tool in a registry, described for a person or a model choosing one."""
    out = []
    for name in sorted(registry.names()):
        try:
            out.append(describe_tool(name, registry.raw(name), registry.descriptor(name)).as_dict())
        except Exception as e:  # noqa: BLE001 - one odd tool must not hide the rest
            log.warning("could not describe tool %r: %s", name, e)
    return out

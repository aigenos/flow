"""Declarative skills: run a SKILL.md `flow:`/`compute:`/`actions:` block on the
same deterministic runtime the imperative ``@flow``/``@action`` path uses.

The whole design lowers a declarative skill to the EXACT shape the loader already
produces for a hand-written skill — one ``flow_fn(ctx)`` plus an ``actions`` dict
of ``Callable[[state], Any]`` — so determinism, replay, memoization, and
correction-rearm are INHERITED from ``ctx.run`` / ``_set_field`` and never
re-implemented here.

Two kinds of "action":

  * PURE computed values (``compute:`` map of name -> expression) are NOT actions
    and are NEVER memoized. They recompute on every replay over current fields,
    so a correction that crosses a threshold re-arms any gate that reads them for
    free. They cannot side-effect (safe_eval forbids calls/attributes).

  * SIDE-EFFECTING actions (``actions:`` map) are references to a NAMED host tool
    with field-bound, already-typed args (never LLM-filled). They run only through
    ``ctx.run`` (so at-most-once per flow run), refuse to fire unless their
    ``requires:`` field is truthy, are deduped across corrections via a sticky
    ``effects_log`` keyed on a stable ``flow_run_id``, and are audited with
    sensitive fields redacted.

Load-time validation (allowlist, fail-closed gates, topo-sort, ungated-effect,
etc.) lives in ``skills.py``; this module is the runtime building blocks.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Callable

from pydantic import BaseModel, Field

from . import trace as _trace
from .decorators import flow
from .toolbox import invoke_langchain, settle
from .engine.bindings import resolve as resolve_bindings
from .engine.safe_eval import eval_strict, free_vars, is_non_finite

# Verb keys in the order the executor tests them; `run` is the legacy alias for
# `do`. Used for trace labels only.
_VERBS = ("ask", "say", "pick", "show", "check", "widget", "chat", "link", "do", "run")


def _verb_of(step: dict[str, Any]) -> str:
    for v in _VERBS:
        if v in step:
            return "do" if v == "run" else v
    return "?"

# A whole-arg reference like "{{amount}}" (binds the TYPED value); an
# interpolation like "Send {{amount}} to {{recipient}}" (str-renders, say only).
_WHOLE_RE = re.compile(r"^\s*\{\{\s*(\w+)\s*\}\}\s*$")
_INTERP_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


# --------------------------------------------------------------------------- #
# Declarative action spec (parsed from `actions:` in SKILL.md frontmatter)
# --------------------------------------------------------------------------- #
class ActionSpec(BaseModel):
    """A declared side-effecting action: a named host tool + field-bound args."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # A field that MUST be truthy before the effect fires (e.g. `confirm`). The
    # effect closure refuses to call the tool otherwise — a hard runtime guard
    # on top of the load-time "must be dominated by a confirm gate" check.
    requires: str | None = None
    # Template (usually "{{flow_run_id}}") whose bound value keys the effects_log
    # dedup. Defaults to the action name + flow_run_id when omitted.
    idempotency_key: str | None = None
    audit: bool = True


class _Recollect(Exception):
    """A failed check cleared a field that a `pick:`/`widget:` step collects."""


# --------------------------------------------------------------------------- #
# Tool registry — host tools a declarative `tool:` reference may resolve to
# --------------------------------------------------------------------------- #
def _normalize_tool(fn: Any) -> Callable[[dict[str, Any], Any | None], Any]:
    """Adapt a host tool to a uniform ``call(args: dict) -> result``.

    Supports a LangChain-style tool (has ``.invoke``; an async-only one is
    awaited) and a plain callable that takes a single args dict (``async def``
    too).
    """
    if hasattr(fn, "invoke_with_context"):
        return lambda args, context=None: settle(fn.invoke_with_context(args, context))
    if hasattr(fn, "invoke"):
        return lambda args, context=None: invoke_langchain(fn, args)
    if callable(fn):
        return lambda args, context=None: settle(fn(args))
    raise TypeError(f"tool must be callable or have .invoke(), got {type(fn)!r}")


class ToolRegistry:
    """Names the operator has explicitly granted. A declarative `tool:` that is
    not in BOTH this registry and the skill's `allowed-tools` is a LOAD ERROR —
    a SKILL.md can only invoke a capability that already exists, never create one.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Callable[[dict[str, Any], Any | None], Any]] = {}
        # The unnormalized originals — chat tool-defs need .description /
        # .args_schema, which _normalize_tool's lambda wrapper hides.
        self._raw: dict[str, Any] = {}
        self._descriptors: dict[str, Any] = {}

    def register(self, name: str, fn: Any, *, descriptor: Any = None) -> None:
        self._tools[name] = _normalize_tool(fn)
        self._raw[name] = fn
        if descriptor is not None:
            self._descriptors[name] = descriptor

    def has(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Callable[[dict[str, Any], Any | None], Any]:
        return self._tools[name]

    def raw(self, name: str) -> Any:
        return self._raw[name]

    def names(self) -> set[str]:
        return set(self._tools)

    def descriptor(self, name: str) -> Any | None:
        """Return optional provider metadata without changing legacy tools."""
        return self._descriptors.get(name)

    @classmethod
    def from_tools(cls, tools: Any) -> "ToolRegistry":
        """Build from a list of callables/LangChain tools, a name->fn dict, or an
        existing ToolRegistry (returned as-is)."""
        if tools is None:
            return cls()
        if isinstance(tools, ToolRegistry):
            return tools
        reg = cls()
        if isinstance(tools, dict):
            for name, fn in tools.items():
                reg.register(name, fn)
            return reg
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", None)
            if not name:
                raise ValueError(f"tool {t!r} has no .name/__name__; register it explicitly")
            reg.register(name, t)
        return reg


# --------------------------------------------------------------------------- #
# Audit sink — one record per fired side effect, secrets redacted
# --------------------------------------------------------------------------- #
class AuditSink:
    """Append-only record of side effects. Default is in-process; swap for a real
    sink in production. Sensitive field values are redacted before they land here.
    """

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, entry: dict[str, Any]) -> None:
        self.records.append(entry)


default_audit = AuditSink()

_REDACTED = "***REDACTED***"


# --------------------------------------------------------------------------- #
# Binding: resolve {{field}} / {{compute}} refs against an env
# --------------------------------------------------------------------------- #
def _whole_var(value: Any) -> str | None:
    if isinstance(value, str):
        m = _WHOLE_RE.match(value)
        if m:
            return m.group(1)
    return None


def bind_value(value: Any, env: dict[str, Any]) -> Any:
    """Resolve one arg template against ``env``.

    * a whole-arg ``"{{name}}"`` returns the TYPED value already in env (fields
      were coerced through their FieldSpec when set; computed values are typed),
      never a ``str()`` of it — the LLM never fills these;
    * an interpolation string str-renders each ``{{name}}``;
    * any non-string literal passes through unchanged.
    """
    name = _whole_var(value)
    if name is not None:
        if name not in env:
            raise KeyError(f"unbound binding {{{{{name}}}}}")
        return env[name]
    if isinstance(value, str) and "{{" in value:
        return _INTERP_RE.sub(lambda m: str(env.get(m.group(1), "")), value)
    return value


def _display(value: Any) -> str:
    """A bound value as a person should read it. Numeric fields are stored as
    floats, so a bare ``str()`` rendered "Send 5000.0 to mom?"."""
    if value is None:
        return ""  # an unfilled optional field is blank, never the word "None"
    if isinstance(value, float) and not isinstance(value, bool):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _render(template: str, env: dict[str, Any]) -> str:
    """Interpolate ``{{name}}`` into user-facing TEXT. Tool arguments never come
    through here — they are bound by ``bind_value`` and keep their real type."""
    return _INTERP_RE.sub(lambda m: _display(env.get(m.group(1), "")), template)


_PATH_INTERP_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def template_roots(value: Any) -> set[str]:
    """Public: the ROOT name of every ``{{name}}`` / ``{{name.key}}`` in a
    template that may read an action's result (a ``then_say:``)."""
    if not isinstance(value, str):
        return set()
    return {path.split(".", 1)[0] for path in _PATH_INTERP_RE.findall(value)}


def _render_path(template: str, env: dict[str, Any]) -> str:
    """Like ``_render``, but ``{{action.key}}`` walks into an action's result.

    Only ``then_say:`` uses it: that message is emitted the moment the action
    fires, so the result it quotes is the one that just came back. A missing
    key renders blank rather than raising — the action already happened, and a
    cosmetic gap must not turn a completed write into an error.
    """
    def lookup(match: "re.Match[str]") -> str:
        head, *rest = match.group(1).split(".")
        value: Any = env.get(head, "")
        for key in rest:
            if isinstance(value, dict):
                value = value.get(key, "")
            else:
                value = ""
                break
        return _display(value)

    return _PATH_INTERP_RE.sub(lookup, template)


def whole_var(value: Any) -> str | None:
    """Public: the single name if ``value`` is exactly ``"{{name}}"``, else None.
    Used by the loader to tell a typed whole-arg binding from an interpolation."""
    return _whole_var(value)


def dotted_refs(value: Any) -> list[str]:
    """Public: every ``{{a.b}}`` path in a string template, as written.

    ``say:`` and ``else_say:`` render whole ``{{name}}`` references only, so a
    dotted path in one of them would reach the user as literal braces. The
    loader uses this to refuse it."""
    if not isinstance(value, str):
        return []
    return [path for path in _PATH_INTERP_RE.findall(value) if "." in path]


def template_vars(value: Any) -> set[str]:
    """Public: every name referenced by ``{{...}}`` inside a string template."""
    if not isinstance(value, str):
        return set()
    return set(_INTERP_RE.findall(value))


_BIND_ROOT = re.compile(r"\{\{\s*([a-zA-Z_]\w*)")


def binding_roots(value: Any) -> set[str]:
    """Root names of every ``{{root.path}}`` binding in a value tree (str/dict/
    list). Widget ``data:`` uses dotted paths (``{{search_clients.matches}}``,
    ``{{row.account_number}}``) resolved by the binding engine; this returns the
    root (``search_clients``) so the loader can prove it is a declared field,
    compute, or action result."""
    out: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            out.update(_BIND_ROOT.findall(v))
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return out


def widget_namespace(state: dict[str, Any], compute: dict[str, str], order: list[str], field_names: Any) -> dict[str, Any]:
    """The lenient namespace widget ``data:`` binds against: fields + computed +
    flow_run_id PLUS action results by name (so a widget can show the rows a
    prior `do:` returned). Display binding, not a gate — dotted + list-aware."""
    ns = build_env(state, compute, order, field_names)
    for k, v in (state.get("results") or {}).items():
        ns.setdefault(k, v)
    return ns


# --------------------------------------------------------------------------- #
# Compute ordering + env
# --------------------------------------------------------------------------- #
def topo_sort_compute(compute: dict[str, str]) -> list[str]:
    """Order computed names so each is evaluated after its dependencies. A cycle
    or forward reference among computes is a (load-time) error."""
    names = set(compute)
    deps = {n: (free_vars(expr) & names) for n, expr in compute.items()}
    order: list[str] = []
    temp: set[str] = set()
    perm: set[str] = set()

    def visit(n: str) -> None:
        if n in perm:
            return
        if n in temp:
            raise ValueError(f"cyclic compute dependency involving {n!r}")
        temp.add(n)
        for d in deps[n]:
            visit(d)
        temp.discard(n)
        perm.add(n)
        order.append(n)

    for n in compute:
        visit(n)
    return order


def _run_id(state: dict[str, Any]) -> str:
    """Stable per-run id. reset_for_new_flow normally sets it; for callers that
    preset flow_id without it, mint one once and reuse it across turns."""
    rid = state.get("flow_run_id")
    if not rid:
        rid = uuid.uuid4().hex
        state["flow_run_id"] = rid
    return rid


def build_env(
    state: dict[str, Any],
    compute: dict[str, str],
    order: list[str],
    field_names: Any = (),
) -> dict[str, Any]:
    """fields + flow_run_id + computed values (in dependency order).

    Declared fields are seeded to ``None`` when unset so a ``{{field}}`` binding
    resolves to None (not KeyError) before the field is collected. A compute
    whose inputs are still None raises TypeError on comparison; we leave it None
    — it is only *read* by a gate after its inputs are collected (the flow asks
    them first), so a None here is never the value a reached gate sees.
    """
    fields = state.get("fields", {})
    env: dict[str, Any] = dict(fields)
    for n in field_names:
        env.setdefault(n, None)
    env["flow_run_id"] = _run_id(state)
    for name in order:
        try:
            env[name] = eval_strict(compute[name], env)
        except TypeError:
            env[name] = None  # an input is still unset; value is not yet defined
    return env


# --------------------------------------------------------------------------- #
# Side-effecting action -> a memoized, gated, deduped, audited closure
# --------------------------------------------------------------------------- #
def _make_effect(
    name: str,
    spec: ActionSpec,
    tool_call: Callable[[dict[str, Any], Any | None], Any],
    field_specs: dict[str, Any],
    compute: dict[str, str],
    order: list[str],
    audit: AuditSink,
    skill_name: str | None,
) -> Callable[[dict[str, Any]], Any]:
    field_names = list(field_specs)

    # An action with `requires:` is a GATED EFFECT — it gets the sticky
    # effects_log dedup (the no-double-spend guard survives corrections). An
    # action without `requires:` is a READ — it memoizes only via `results`,
    # which corrections clear, so a client/account switch re-fetches fresh
    # data instead of replaying a stale lookup.
    is_gated_effect = spec.requires is not None

    def _effect(state: dict[str, Any]) -> Any:
        env = build_env(state, compute, order, field_names)

        # Hard runtime guard: never fire unless the required confirmation is true.
        if spec.requires and not env.get(spec.requires):
            raise PermissionError(
                f"action {name!r} requires {spec.requires!r} to be true before it can run"
            )

        key = str(bind_value(spec.idempotency_key, env)) if spec.idempotency_key else env["flow_run_id"]
        log_key = f"{name}:{key}"
        log = state.setdefault("effects_log", {})
        if is_gated_effect and log_key in log:
            # Already fired this run (even if a later correction wiped results).
            return log[log_key]

        # build_env seeds every DECLARED field to None, so an optional field the
        # flow never asked for (a 2FA code below the threshold) binds to None.
        # Passing None into a tool arg typed `str` is a pydantic ValidationError,
        # which crashed the ordinary sub-threshold path. Omit the key instead and
        # let the tool's own default apply. Only a whole-arg "{{field}}" binding
        # is dropped — an explicit literal None a skill author wrote is kept.
        args = {}
        for arg_name, template in spec.args.items():
            bound = bind_value(template, env)
            if bound is None and _whole_var(template) is not None:
                continue
            args[arg_name] = bound
        _assert_finite_args(name, args)
        invocation_context: Any = {
            "skill_name": skill_name,
            "action_name": name,
            "flow_run_id": env["flow_run_id"],
            "idempotency_key": key,
        }
        # Managed tools accept the typed InvocationContext model. Keep that
        # optional dependency lazy so the core package remains lightweight.
        try:
            from .connectors.models import InvocationContext

            invocation_context = InvocationContext.model_validate(invocation_context)
        except ImportError:  # pragma: no cover - connector package is bundled
            pass
        result = tool_call(args, invocation_context)
        if is_gated_effect:
            log[log_key] = result

        if spec.audit:
            redacted = {
                k: _audit_value(v, spec.args.get(k), field_specs)
                for k, v in args.items()
            }
            record = {
                "skill_action": name,
                "tool": spec.tool,
                "args": redacted,
                "flow_run_id": env["flow_run_id"],
                "idempotency_key": key,
                "result_id": _result_id(result),
            }
            if spec.requires:
                # The gate's evaluated value, so the log answers "was this
                # authorized?" without the auditor having to reconstruct it.
                record["gate"] = {spec.requires: bool(env.get(spec.requires))}
            audit.record(record)
        return result

    def _what_runs(state: dict[str, Any]) -> dict[str, Any]:
        """The tool and the inputs it would get right now, without calling it."""
        env = build_env(state, compute, order, field_names)
        inputs = []
        for arg_name, template in spec.args.items():
            bound = bind_value(template, env)
            if bound is None:
                continue
            inputs.append((arg_name, bound, _is_sensitive(template, field_specs)))
        return {"tool": spec.tool, "note": "", "inputs": inputs}

    _effect._flow_what_runs = _what_runs  # type: ignore[attr-defined]
    _effect.__name__ = name  # ctx.run memoizes under this name
    # ctx.run reads these: a gated effect is at-most-once per run and must
    # re-check its gate at dispatch time (see Context.run).
    _effect._flow_effect_gated = is_gated_effect  # type: ignore[attr-defined]
    _effect._flow_effect_requires = spec.requires  # type: ignore[attr-defined]
    # run_turn reads this: a lookup is re-read when its flow resumes after
    # another flow ran (see runtime._refresh_reads).
    _effect._flow_effect_readonly = bool(spec.readonly)  # type: ignore[attr-defined]
    # The fields this action reads. A `pick:` over its result re-asks them when
    # the result is empty (a search that matched nothing).
    _effect._flow_effect_inputs = [  # type: ignore[attr-defined]
        n for n in field_names if n in binding_roots(spec.args or {})
    ]
    return _effect


def _assert_finite_args(action_name: str, args: dict[str, Any]) -> None:
    """A non-finite number NEVER reaches a tool argument.

    NaN / +-inf survive ``json.loads`` (the bare tokens are accepted) and defeat
    every ``min:``/``max:`` bound because each comparison against NaN is False.
    They are rejected at the field boundary; this is the last gate before the
    value leaves the process, and it covers an interpolated or nested binding
    too. Raises ``ValueError`` — the effect does not fire.
    """

    def walk(value: Any, path: str) -> None:
        if is_non_finite(value):
            raise ValueError(
                f"action {action_name!r}: argument {path} is {value!r}, which is not a "
                "finite number — refusing to call the tool"
            )
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    for k, v in args.items():
        walk(v, k)


def _audit_value(value: Any, template: Any, field_specs: dict[str, Any]) -> Any:
    """Redact a PRESENT secret; never manufacture the appearance of one.

    Redacting per-binding rather than per-value logged ``otp='***REDACTED***'``
    for a transfer that carried no code at all, so the log could not distinguish
    a 2FA-verified write from one that skipped the gate — precisely the event the
    log exists to detect. An absent secret is logged as its real absence.
    """
    if not _is_sensitive(template, field_specs):
        return value
    if value is None or value == "" or value is False:
        return value
    return _REDACTED


def _is_sensitive(template: Any, field_specs: dict[str, Any]) -> bool:
    fld = _whole_var(template)
    spec = field_specs.get(fld) if fld else None
    return bool(spec is not None and getattr(spec, "sensitive", False))


def _result_id(result: Any) -> Any:
    if isinstance(result, dict):
        for k in ("id", "txn_id", "reference", "ref"):
            if k in result:
                return result[k]
    # Audit records never contain result bodies. A scalar reference is safe;
    # unrecognized structured results are deliberately omitted.
    return result if isinstance(result, (str, int, float, bool)) else None


def compile_effects(
    actions: dict[str, ActionSpec],
    registry: ToolRegistry,
    field_specs: dict[str, Any],
    compute: dict[str, str],
    order: list[str],
    audit: AuditSink | None = None,
    skill_name: str | None = None,
) -> dict[str, Callable[[dict[str, Any]], Any]]:
    """Build the name -> closure dict the synthesized flow dispatches via ctx.run.
    Tools are assumed already validated (present + allowlisted) by the loader."""
    sink = audit or default_audit
    out: dict[str, Callable[[dict[str, Any]], Any]] = {}
    for name, spec in actions.items():
        out[name] = _make_effect(
            name, spec, registry.get(spec.tool), field_specs, compute, order, sink,
            skill_name,
        )
    return out


# --------------------------------------------------------------------------- #
# What a yes will run: shown on the confirmation, and held to at the call
# --------------------------------------------------------------------------- #
def _label_of(name: str) -> str:
    return str(name).replace("_", " ").strip()


def _as_shown(value: Any, secret: bool) -> str:
    """One input as the card prints it. A secret is never printed."""
    if secret:
        return "•••••• (hidden)"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        return _render("{{v}}", {"v": value})
    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
        # A list `repeat:` collected: one item per entry, its answers in order.
        text = "; ".join(
            ", ".join(_as_shown(v, False) for v in row.values() if v not in (None, ""))
            for row in value
        )
        text = f"{len(value)}: {text}"
        return text if len(text) <= 160 else text[:159] + "…"
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, default=str, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= 160 else text[:159] + "…"


def _for_card(action: str, info: dict[str, Any]) -> dict[str, Any]:
    tool = str(info.get("tool") or action)
    return {
        "title": _label_of(tool).capitalize(),
        "note": str(info.get("note") or ""),
        "inputs": [
            {"label": _label_of(name), "shown": _as_shown(value, secret)}
            for name, value, secret in info.get("inputs") or []
            if value != ""      # a blank optional answer: nothing to show (it is still compared)
        ],
    }


def _canon_inputs(info: dict[str, Any]) -> dict[str, str]:
    """Each input in a form that can be compared later and kept in state: a
    secret as a digest (state must not gain a second copy of it), a number as a
    number whatever its type, anything else as its JSON."""
    out: dict[str, str] = {}
    for name, value, secret in info.get("inputs") or []:
        if secret:
            out[name] = "sha256:" + hashlib.sha256(str(value).encode("utf-8")).hexdigest()
        elif isinstance(value, bool):
            out[name] = "true" if value else "false"
        elif isinstance(value, (int, float)):
            out[name] = repr(float(value))
        else:
            out[name] = json.dumps(value, default=str, sort_keys=True, ensure_ascii=False)
    return out


# --------------------------------------------------------------------------- #
# The synthesized @flow
# --------------------------------------------------------------------------- #
def build_declarative_flow(
    steps: list[dict[str, Any]],
    compute: dict[str, str],
    order: list[str],
    effects: dict[str, Callable[[dict[str, Any]], Any]],
    fields: Any = (),
) -> Callable[[Any], None]:
    """Turn a declarative `flow:` step list into a @flow callable that runs on the
    standard Context. The whole authoring vocabulary:

        - ask: <field>          collect a typed field (pauses if missing)
        - say: "...{{x}}..."    emit once per run (replay-safe via say_cursor)
        - do/run: <action>      dispatch a side effect via ctx.run (at-most-once)
        - pick: "{{rows}}"      auto-grid the rows; a selection fills `save:` fields
        - show: "{{value}}"     auto-render a value (card/grid/timeline by shape)
        - check: "<expr>"       gate with recovery: on false, `else_say:` +
                                `else_clear:` fields, then re-collect
        - chat: {tools, pin,    open scoped Q&A: model picks tools freely, pinned
                 greeting}      args always come from validated fields
        - when: "<expr>"        guard any step (fail-closed; sees results too)
        - when_missing: <field> guard sugar: run only while <field> is unfilled
    """

    # Accept a field-specs dict (preferred — enables good re-ask prompts) or a
    # bare name list (back-compat with earlier callers).
    if isinstance(fields, dict):
        field_specs: dict[str, Any] = dict(fields)
    else:
        field_specs = {name: None for name in fields}
    field_names = list(field_specs)

    def _reask_prompt(field: str) -> str:
        spec = field_specs.get(field)
        desc = getattr(spec, "description", None) if spec is not None else None
        return desc or f"Please provide a value for {field!r}."

    # Result roots each check: expression reads — popped on failure so the
    # producing action re-runs against the re-collected field value.
    effect_names = set(effects)
    check_roots: dict[int, set[str]] = {
        i: (free_vars(step["check"]) & effect_names)
        for i, step in enumerate(steps)
        if "check" in step
    }

    # Index of the trailing `do:` step, when the flow ends on a gated effect.
    # Skipping it is the "user declined" outcome and needs a spoken answer.
    _final_gated_do: set[int] = set()
    if steps:
        last = len(steps) - 1
        tail = steps[last]
        tail_action = tail.get("do") or tail.get("run")
        tail_fn = effects.get(tail_action) if tail_action else None
        tail_gated = bool(
            getattr(tail_fn, "_flow_effect_gated", False)
            # A handlers.py @action write names its gate with requires=.
            or getattr(tail_fn, "_flow_action_requires", None)
        )
        if tail_action and tail.get("when") and tail_gated:
            _final_gated_do.add(last)

    # Fields a `pick:` or `widget:` step collects. When a failed check clears
    # one, the flow replays so THAT step asks again (the grid, with fresh
    # rows) instead of pausing on a bare text box for the field.
    _widget_collected: set[str] = {
        f for st in steps if "pick" in st or "widget" in st for f in (st.get("save") or [])
    }

    def _gate_declined(step: dict[str, Any], env: dict[str, Any]) -> bool:
        """True when the guard is false because a confirmation was answered
        NO — not merely because it is still unanswered (that's a pause, and the
        flow has already asked)."""
        return any(env.get(v) is False for v in free_vars(step["when"]))

    _shown: set[str] = set()
    for st in steps:
        if not ("pick" in st or "show" in st or "widget" in st):
            continue
        text = " ".join(str(st.get(k)) for k in ("pick", "show", "data") if st.get(k) is not None)
        _shown |= {root for root in re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)", text) if root in effects}
    _spoken = {st.get("do") or st.get("run") for st in steps if st.get("then_say")}
    for name in _shown - _spoken:
        try:
            effects[name]._flow_effect_refresh = True  # type: ignore[attr-defined]
        except AttributeError:  # a bound method or builtin: leave it memoized
            pass

    def _requires_of(action: Any) -> str | None:
        fn = effects.get(action) if action else None
        return getattr(fn, "_flow_effect_requires", None) or getattr(fn, "_flow_action_requires", None)

    # The tools each yes lets run, in the order the flow reaches them: the ones
    # it releases itself, and the ones a refusal of it would stop (a second
    # yes, asked of a manager, sits in the tool's condition without owning it).
    releases: dict[str, list[tuple[int, str]]] = {}
    for i, st in enumerate(steps):
        released = st.get("do") or st.get("run")
        gate = _requires_of(released)
        if not gate:
            continue
        try:
            reads = free_vars(st["when"]) if st.get("when") else set()
        except Exception:  # noqa: BLE001 - reported by the loader
            reads = set()
        for name in sorted(reads | {gate}):
            if getattr(field_specs.get(name), "type", None) == "boolean":
                releases.setdefault(name, []).append((i, released))

    def _already_fired(ctx: Any, action: str) -> bool:
        return any(str(k).startswith(f"{action}:") for k in (ctx.state.get("effects_log") or {}))

    def _yes_would_run(ctx: Any, field: str, env: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """``{action: {"tool", "note", "inputs"}}`` for every tool a yes to
        ``field`` would run from here: not one that already ran, and not one
        whose own condition rules it out."""
        out: dict[str, dict[str, Any]] = {}
        for idx, action in releases.get(field, ()):
            if _already_fired(ctx, action):
                continue
            cond = steps[idx].get("when")
            if cond is not None:
                try:
                    if not eval_strict(cond, {**env, field: True}):
                        continue
                except Exception:  # noqa: BLE001 - reads something not known yet: it may run
                    pass
            describe = getattr(effects.get(action), "_flow_what_runs", None)
            info: dict[str, Any] = {"tool": action, "note": "", "inputs": []}
            if describe is not None:
                try:
                    info = describe(ctx.state)
                except Exception:  # noqa: BLE001 - shown by name rather than not at all
                    pass
            out[action] = info
        return out

    def _approval_key(ctx: Any, field: str) -> str:
        return f"{ctx.state.get('flow_run_id') or ''}:{field}"

    def _ask_someone_else(ctx: Any, field: str, who: str, env: dict[str, Any]) -> bool:
        """A yes that is not the user's to give. File the request (or find it),
        and either take the answer that has arrived or wait here.

        Returns True once ``field`` holds the answer. Otherwise it raises the
        pause: the flow stays on this step until the person named decides.
        """
        from .approvals import store_for

        would_run = _yes_would_run(ctx, field, env)
        answers = [
            {"label": _label_of(name), "shown": _as_shown(env.get(name), False)}
            for name, spec in field_specs.items()
            if env.get(name) is not None and name != field
            and not getattr(spec, "sensitive", False) and getattr(spec, "type", None) != "boolean"
        ]
        store = store_for(getattr(ctx, "_host", None))
        request = store.request(
            run_id=str(ctx.state.get("flow_run_id") or ""), field=field, who=who,
            question=_reask_prompt(field), skill=str(ctx.state.get("flow_id") or ""),
            runs=[_for_card(a, info) for a, info in would_run.items()], answers=answers,
        )
        asked_now = getattr(ctx, "_asked_of_others", None)
        if asked_now is not None:
            asked_now.add(request.id)
        notes = ctx.state.setdefault("approvals", {})
        if request.status in ("approved", "declined"):
            approved = request.status == "approved"
            ctx.fields[field] = approved
            name = (request.decided_by or who).strip()
            said = f"{name[:1].upper()}{name[1:]} said {'yes' if approved else 'no'}"
            if request.note:
                said += f": {request.note.rstrip('.')}"
            ctx._emit(said + "." if approved else said + ". Nothing was done.")
            return True
        # Held to what the approver is shown, like a yes on the card.
        notes[_approval_key(ctx, field)] = {a: _canon_inputs(info) for a, info in would_run.items()}
        first_time = notes.get(_approval_key(ctx, field) + "#sent") != request.id
        notes[_approval_key(ctx, field) + "#sent"] = request.id
        card = {
            "type": "card",
            "props": {"title": f"Waiting for {who}", "subtitle": _reask_prompt(field)},
            "children": [
                {"type": "text", "props": {"value": f"Request {request.id}. This carries on when they answer.", "muted": True}},
                *[
                    {"type": "row", "props": {"justify": "between", "gap": "md"}, "children": [
                        {"type": "text", "props": {"value": item["label"], "muted": True}},
                        {"type": "text", "props": {"value": item["shown"]}},
                    ]}
                    for run in request.runs for item in [{"label": run["title"], "shown": ""}, *run["inputs"]]
                ],
            ],
        }
        ctx.widget(
            f"approval:{request.id}", save=[field],
            data={"approval_id": request.id, "who": who, "runs": request.runs},
            # Nothing on it can be submitted: the answer is not the user's.
            submit_schema={"type": "object", "properties": {}},
            prompt=(
                f"This needs a yes from {who}, so I have sent it to them ({request.id}). "
                "I will carry on when they answer." if first_time
                else f"Still waiting for {who} ({request.id})."
            ),
            spec=card,
        )
        return False

    def _wrote_this_run(ctx: Any) -> bool:
        """Has a gated write of this flow already fired in this run?"""
        results = ctx.state.get("results") or {}
        run_id = str(ctx.state.get("flow_run_id") or "")
        if any(
            name in results and getattr(fn, "_flow_effect_gated", False)
            for name, fn in effects.items()
        ):
            return True
        return bool(run_id) and any(run_id in str(k) for k in (ctx.state.get("effects_log") or {}))

    def _replay(ctx: Any, recollecting: bool = False) -> None:
        from .context import NeedField  # local: avoid import cycle at module load

        said_no = False  # a gated write was skipped because its own confirmation is False

        # An index, not a for-loop: `repeat:` goes back to the start of an item.
        i = -1
        while True:
            i += 1
            if i >= len(steps):
                break
            step = steps[i]
            # Namespace for conditions/bindings: fields + computes + flow_run_id
            # + prior action results (results are deterministic tool outputs).
            env = widget_namespace(ctx.state, compute, order, field_names)

            verb = _verb_of(step)

            # A step that speaks through the say cursor keeps its place in line
            # when a guard skips it. `say` is "said once" by POSITION (the nth
            # line of the replay); if a skipped line gave its place up, every
            # later line would move up one and the next of them would be taken
            # for one already said. (`when_missing:` flips from true to false
            # the moment its field is filled, so this is the ordinary case.)
            speaks = "say" in step or "show" in step or "_display" in step or (
                "link" in step and (step.get("then_say") or step.get("else_say"))
            )

            wm = step.get("when_missing")
            if wm is not None:
                wm_fields = [wm] if isinstance(wm, str) else list(wm)
                if all(env.get(f) is not None for f in wm_fields):
                    _trace.emit(
                        step=i, verb=verb, outcome="skipped", guard="when_missing",
                        detail=f"{', '.join(wm_fields)} already set",
                    )
                    if speaks:
                        ctx._say_counter += 1
                    continue
            cond = step.get("when")
            if cond is not None and not eval_strict(cond, env):
                _trace.emit(
                    step=i, verb=verb, outcome="skipped", guard="when", detail=str(cond),
                )
                if speaks:
                    ctx._say_counter += 1
                # A DECLINED side effect must say so. When the flow's last step
                # is a gated `do:` and its guard is false, the user is told the
                # write did not happen: a declined transfer never ends in
                # silence.
                #
                # "Declined" means the write's OWN confirmation is False, not
                # that some name in the guard is: `when: confirm and wants_car`
                # skipped because the user wanted no car is not a refusal. And
                # it is never said after a write of this run has fired: "no
                # changes were made" one line after "Hotel booked." is false.
                requires = _requires_of(step.get("do") or step.get("run"))
                if requires and env.get(requires) is False:
                    said_no = True
                    if i in _final_gated_do and not _wrote_this_run(ctx):
                        ctx.say("Okay — no changes were made.")
                continue

            if "_display" in step:
                # A designed widget, shown and not answered (`say:` + `widget:`).
                data = resolve_bindings(step.get("data") or {}, env)
                _trace.emit(step=i, verb="say", outcome="ran", detail=f"widget {step['_display']}")
                ctx.show_widget(str(step["_display"]), data if isinstance(data, dict) else {})
                continue

            if "_repeat" in step:
                # The start of one item of a `repeat:`. When the user has said
                # there are no more, and nothing of a new item is answered, the
                # repeat is over: on to the step after it.
                name, more = step["_repeat"], step["more"]
                items = list(step.get("items") or [])
                collected = (ctx.state.get("results") or {}).get(name) or []
                answer = ctx.fields.get(more)
                in_progress = any(ctx.fields.get(f) is not None for f in items)
                if answer is False and collected and not in_progress:
                    _trace.emit(step=i, verb="repeat", outcome="skipped",
                                detail=f"{name}: {len(collected)} collected")
                    i = int(step["end"])
                    continue
                if answer is not None and not in_progress:
                    # A yes that starts another item (or a no before anything
                    # was collected: a repeat collects at least one item).
                    ctx.fields.pop(more, None)
                _trace.emit(step=i, verb="repeat", outcome="ran", detail=f"{name}: item {len(collected) + 1}")
                continue

            if "_collect" in step:
                # The end of one item: every question in it is answered, or the
                # replay would have paused on it. Is there another?
                name, more = step["_collect"], step["more"]
                items = list(step.get("items") or [])
                answer = ctx.fields.get(more)
                if answer is None:
                    _trace.emit(step=i, verb="repeat", outcome="paused", detail=more)
                    ctx.ask(more)
                results = ctx.state.setdefault("results", {})
                collected = list(results.get(name) or [])
                collected.append({f: ctx.fields.get(f) for f in items})
                results[name] = collected
                for f in items:
                    ctx.fields.pop(f, None)
                log = ctx.state.get("effects_log") or {}
                for root in step.get("reset") or ():
                    # A lookup that read this item's answers starts afresh.
                    results.pop(root, None)
                    for k in [k for k in log if k.startswith(f"{root}:")]:
                        log.pop(k, None)
                _trace.emit(step=i, verb="repeat", outcome="ran", detail=f"{name}: {len(collected)} collected")
                if answer:
                    ctx.fields.pop(more, None)
                    i = int(step["begin"]) - 1      # the next item, from the start of the repeat
                continue

            if "ask" in step:
                # ctx.ask raises NeedField when the field is still empty; report
                # that as a pause rather than a completed step.
                filled = ctx.fields.get(step["ask"]) is not None
                _trace.emit(
                    step=i, verb="ask", outcome="ran" if filled else "paused",
                    detail=step["ask"],
                )
                asked = step["ask"]
                who = getattr(field_specs.get(asked), "asked_of", None)
                if who and not filled:
                    filled = _ask_someone_else(ctx, asked, str(who), env)
                if not filled and asked in releases and not getattr(field_specs.get(asked), "widget", None):
                    # A yes that releases a tool is given to a card the engine
                    # builds: which tool, with which inputs. The skill's own
                    # sentence is the title, not the evidence.
                    would_run = _yes_would_run(ctx, asked, env)
                    if would_run:
                        from .autowidget import confirm_spec_for

                        ctx.state.setdefault("approvals", {})[_approval_key(ctx, asked)] = {
                            action: _canon_inputs(info) for action, info in would_run.items()
                        }
                        runs = [_for_card(a, info) for a, info in would_run.items()]
                        spec, schema = confirm_spec_for(asked, _reask_prompt(asked), runs)
                        ctx.widget(
                            # `data.runs` is the same evidence as the card, for a
                            # client that draws its own confirmation.
                            f"confirm:{asked}", save=[asked], data={"runs": runs}, submit_schema=schema,
                            prompt=_reask_prompt(asked), spec=spec,
                        )
                ctx.ask(asked)

            elif "say" in step:
                _trace.emit(step=i, verb="say", outcome="ran")
                ctx.say(_render_path(step["say"], env))

            elif "link" in step:
                # Hand the user to another skill and come back. The first time
                # through, ctx.link() raises and the runtime starts the target;
                # once it has run, the same call returns whether it did its job.
                target = step["link"]
                _trace.emit(step=i, verb="link", outcome="ran", detail=target)
                completed = ctx.link(target)
                line = step.get("then_say") if completed else step.get("else_say")
                if line:
                    ctx.say(_render_path(line, env))
                elif speaks:
                    ctx._say_counter += 1  # the other line's place stays taken
                if not completed:
                    # Cancelled, or the user said no in there. Nothing after a
                    # hand-off that did nothing may run as if it had: this flow
                    # stops here. Told to run_turn, so a flow that linked to
                    # THIS one hears the same thing.
                    if not _wrote_this_run(ctx):
                        ctx.state["flow_declined"] = "handoff"
                    return

            elif "pick" in step:
                save = list(step.get("save") or [])
                if all(ctx.fields.get(f) is not None for f in save):
                    _trace.emit(
                        step=i, verb="pick", outcome="skipped", guard="save-complete",
                        detail=f"{', '.join(save)} already set",
                    )
                    continue
                _trace.emit(step=i, verb="pick", outcome="ran", detail=", ".join(save))
                rows = resolve_bindings(step["pick"], env)
                if rows is None or (isinstance(rows, list) and not rows):
                    # Nothing to choose from: a search that matched nothing. That
                    # is the user's situation, not an authoring error. Say so, and
                    # ask again for whatever the lookup was given.
                    roots = [r for r in binding_roots(step["pick"]) if r in effect_names]
                    inputs = [
                        f for r in roots for f in getattr(effects[r], "_flow_effect_inputs", [])
                        if ctx.fields.get(f) is not None
                    ]
                    if not recollecting:
                        ctx._emit("Nothing matched that, so there is nothing to choose from.")
                    if inputs:
                        results = ctx.state.get("results") or {}
                        log = ctx.state.get("effects_log") or {}
                        for f in inputs:
                            ctx.fields.pop(f, None)
                        for root in roots:
                            results.pop(root, None)
                            for k in [k for k in log if k.startswith(f"{root}:")]:
                                log.pop(k, None)
                        if not recollecting and inputs[0] in _widget_collected:
                            raise _Recollect()
                        raise NeedField(inputs[0], _reask_prompt(inputs[0]))
                    raise NeedField(save[0], _reask_prompt(save[0]))
                submit_schema = {
                    "type": "object",
                    "properties": {f: {"type": "string"} for f in save},
                    "required": list(save),
                }
                if step.get("widget"):
                    # Custom a2ui widget from the catalog: the frontend/server
                    # resolves the spec by id. Data carries the rows under both
                    # conventional binding names so either style of widget file
                    # ({{rows}} or {{options}}) works unchanged.
                    ctx.widget(
                        step["widget"],
                        save=save,
                        data={"rows": rows, "options": rows},
                        submit_schema=step.get("submit_schema") or submit_schema,
                        prompt=step.get("prompt"),
                    )
                else:
                    from .autowidget import pick_spec_for

                    spec, schema = pick_spec_for(
                        rows, save, prompt=step.get("prompt"),
                        field_map=step.get("map"),
                        kind=step.get("as") or "datatable",
                        display=step.get("display"),
                    )
                    ctx.widget(
                        f"pick:{save[0]}",
                        save=save,
                        data={"rows": rows},
                        submit_schema=schema,
                        prompt=step.get("prompt"),
                        spec=spec,
                    )

            elif "show" in step:
                title = step.get("title")
                _trace.emit(step=i, verb="show", outcome="ran", detail=str(step["show"]))
                value = resolve_bindings(step["show"], env)
                if isinstance(value, (str, int, float, bool)) and not title:
                    # A sentence or a number is said, not framed: a card is for
                    # data. (Same place in line either way: one slot.)
                    ctx.say(_display(value))
                else:
                    ctx.show(value, title=_render(title, env) if title else None)

            elif "check" in step:
                passed = eval_strict(step["check"], env)
                _trace.emit(
                    step=i, verb="check", outcome="passed" if passed else "failed",
                    detail=str(step["check"]),
                )
                if passed:
                    continue
                msg = step.get("else_say")
                if msg and not recollecting:
                    # Per-failure message (host emit, not say-cursor): each bad
                    # value deserves the explanation again.
                    ctx._emit(_render_path(msg, env))
                cleared = list(step.get("else_clear") or [])
                flds = ctx.fields
                for f in cleared:
                    flds.pop(f, None)
                # Drop the producing action's memo AND its effects_log entries
                # so the validation re-runs against the re-collected value.
                # (results alone isn't enough: a declared tool action would
                # short-circuit to its sticky effects_log record.)
                results = ctx.state.get("results") or {}
                log = ctx.state.get("effects_log") or {}
                for root in check_roots.get(i, ()):
                    results.pop(root, None)
                    for k in [k for k in log if k.startswith(f"{root}:")]:
                        log.pop(k, None)
                if not recollecting and cleared[0] in _widget_collected:
                    raise _Recollect()
                raise NeedField(cleared[0], _reask_prompt(cleared[0]))

            elif "widget" in step:
                data = resolve_bindings(step.get("data") or {}, env)
                _trace.emit(step=i, verb="widget", outcome="ran", detail=str(step["widget"]))
                # A yes given on a designed page (a checkout) is held to what
                # would run just like one given on the built-in card.
                for saved in step.get("save") or []:
                    if saved in releases and ctx.fields.get(saved) is None:
                        would_run = _yes_would_run(ctx, saved, env)
                        if would_run:
                            ctx.state.setdefault("approvals", {})[_approval_key(ctx, saved)] = {
                                action: _canon_inputs(info) for action, info in would_run.items()
                            }
                ctx.widget(
                    step["widget"],
                    save=list(step.get("save") or []),
                    data=data,
                    submit_schema=step.get("submit_schema"),
                    prompt=step.get("prompt"),
                )

            elif "chat" in step:
                cfg = dict(step["chat"] or {})
                _trace.emit(
                    step=i, verb="chat", outcome="ran",
                    detail=", ".join(cfg.get("tools") or []) or "no tools",
                )
                ctx.chat(cfg)

            elif "do" in step or "run" in step:
                action = step.get("do") or step.get("run")
                _trace.emit(step=i, verb="do", outcome="ran", detail=str(action))
                describe = getattr(effects[action], "_flow_what_runs", None)
                yeses = [g for g, runs in releases.items() if any(a == action for _, a in runs)]
                if yeses and describe is not None and not _already_fired(ctx, action):
                    # Each yes was given to everything it lets run, so everything
                    # still to run is compared, not only this tool: a change to
                    # the second booking's input must stop the first as well.
                    gate, changed = "", []
                    for candidate in yeses:
                        shown = (ctx.state.get("approvals") or {}).get(_approval_key(ctx, candidate)) or {}
                        if action not in shown:
                            continue
                        differs: list[str] = []
                        for other, approved in shown.items():
                            say_what = getattr(effects.get(other), "_flow_what_runs", None)
                            if not isinstance(approved, dict) or say_what is None or _already_fired(ctx, other):
                                continue
                            now = _canon_inputs(say_what(ctx.state))
                            differs.extend(k for k, v in approved.items() if now.get(k) != v)
                        if differs:
                            gate, changed = candidate, sorted(set(differs))
                            break
                    if changed:
                        # What would run is no longer what the user said yes to.
                        # Nothing runs; the yes is asked for again, with a card
                        # that shows the inputs as they are now.
                        _trace.emit(step=i, verb="do", outcome="refused", detail=f"changed: {changed}")
                        if not recollecting:
                            ctx._emit(
                                "Something changed after you said yes ("
                                + ", ".join(_label_of(k) for k in changed)
                                + "), so nothing has run. Please look again."
                            )
                        ctx.fields.pop(gate, None)
                        raise _Recollect()
                after = step.get("then_say")
                speaks = bool(getattr(effects[action], "_flow_tool_wrapper", False))
                if not after and not speaks:
                    ctx.run(effects[action], name=action)
                else:
                    # `then_say:` reports the outcome — ONCE, at the moment the
                    # action fires. The flow replays from the top every turn, so
                    # "it fired" cannot mean "this step ran": a gated write shows
                    # up as a new effects_log entry, and a read that is not logged
                    # as a result that was not there before.
                    prefix = f"{action}:"
                    log = ctx.state.get("effects_log") or {}
                    logged_before = {k for k in log if k.startswith(prefix)}
                    had_result = action in (ctx.state.get("results") or {})
                    result = ctx.run(effects[action], name=action)
                    log = ctx.state.get("effects_log") or {}
                    logged_after = {k for k in log if k.startswith(prefix)}
                    fired = bool(logged_after - logged_before) or (
                        not logged_after and not had_result
                    )
                    if speaks and isinstance(result, dict) and result.get("ask_again"):
                        # A library tool sent the user back to a question:
                        #     return {"ask_again": "amount", "say": "That is more than your balance."}
                        # The same recovery as a failed `check:`, decided in the
                        # tool instead of the file.
                        again = result["ask_again"]
                        names = [again] if isinstance(again, str) else list(again)
                        unknown = [n for n in names if n not in field_specs]
                        if unknown or not names:
                            raise ValueError(
                                f"tool {action!r} returned ask_again={again!r}, but "
                                f"{unknown or 'that'} is not a question this flow asks "
                                f"(it asks: {sorted(field_names)})"
                            )
                        if result.get("say") and not recollecting:
                            ctx._emit(str(result["say"]))
                        for n in names:
                            ctx.fields.pop(n, None)
                        # A changed answer needs a fresh yes.
                        for gate in {_requires_of(a) for a in effects} - {None}:
                            ctx.fields.pop(gate, None)
                        results = ctx.state.get("results") or {}
                        results.pop(action, None)
                        for k in [k for k in log if k.startswith(prefix)]:
                            log.pop(k, None)
                        if not recollecting and names[0] in _widget_collected:
                            raise _Recollect()
                        raise NeedField(names[0], _reask_prompt(names[0]))
                    if fired and after:
                        ctx._emit(_render_path(after, {**env, action: result}))
                    elif fired and speaks and isinstance(result, dict) and result.get("say"):
                        ctx._emit(str(result["say"]))

        # The flow reached its end. If the user said no at a confirmation and
        # no write fired, a parent that got here through ctx.link() is told so
        # (read once by run_turn). Decided from the run, not from where the
        # gated step sits, so a closing `say:` after it cannot hide the refusal.
        if said_no and not _wrote_this_run(ctx):
            ctx.state["flow_declined"] = True

    _asks_others = any(getattr(spec, "asked_of", None) for spec in field_specs.values())

    @flow
    def _run(ctx: Any) -> None:
        ctx._asked_of_others = set()
        try:
            try:
                _replay(ctx)
            except _Recollect:
                # A check failed and cleared a field that a grid or widget step
                # collects. Replay once from the top: the step that owns the field
                # sees it missing and presents its widget again. If no step pauses
                # for it on this pass, the check falls back to a plain re-ask.
                ctx.rewind()
                _replay(ctx, recollecting=True)
        finally:
            if _asks_others:
                # A request this turn did not arrive at is one the flow is no
                # longer waiting on: an answer above it changed, so a yes to it
                # would be a yes to what will not run. It leaves the inbox now,
                # not whenever the flow next reaches the step.
                from .approvals import store_for

                store_for(getattr(ctx, "_host", None)).withdraw(
                    str(ctx.state.get("flow_run_id") or ""), keep=ctx._asked_of_others,
                )

    return _run

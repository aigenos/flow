"""The ``ctx`` API — what every flow function uses.

The flow function is re-executed from the top on every user turn. That works
because each ``ctx.*`` call is idempotent given state:

  - ``ctx.ask(field)`` returns the value if filled; otherwise raises
    NeedField (caught by runtime, surfaces a prompt, pauses the host).
  - ``ctx.run(fn)`` returns the cached result if already run; otherwise runs
    the function once and caches.
  - ``ctx.say(text)`` emits exactly once across all turns of a flow. We
    track that with a monotonically-advancing ``say_cursor`` in state.

That replay semantic is the entire trick. The dev writes Python with normal
control flow; the runtime gets determinism for free.

Constraint: the flow function MUST be side-effect-free at the top level. All
side effects belong inside ``@action`` functions (which are memoized).
Violate this and the same email gets sent on every turn.
"""
from __future__ import annotations

from typing import Any, Callable

from .fields import FieldSpec, FieldValidationError, coerce_and_validate


class NeedField(Exception):
    """Raised by ``ctx.ask`` when a field is unfilled. The runtime catches
    this and turns it into a user-facing prompt + pause."""

    def __init__(self, field: str, prompt: str):
        self.field = field
        self.prompt = prompt
        super().__init__(f"need field: {field}")


class NeedFields(Exception):
    """Raised by ``ctx.ask_group`` when one or more fields are unfilled.
    The runtime catches this and renders a multi-field form."""

    def __init__(self, fields: list[str], prompts: list[str]):
        self.fields = fields
        self.prompts = prompts
        super().__init__(f"need fields: {fields}")


class FlowDone(Exception):
    """Raised by ``ctx.end()`` when the dev explicitly terminates a flow."""


class FlowLink(Exception):
    """Raised by ``ctx.link()`` to hand off to another flow (programmatic
    digression). Caught by the runtime, which pushes the current frame and
    switches active flow. ``index`` is the link's position in this replay, so
    the runtime can mark exactly this one done when the target finishes."""

    def __init__(self, target_flow_id: str, index: int = 0):
        self.target_flow_id = target_flow_id
        self.index = index
        super().__init__(f"link to flow: {target_flow_id}")


class NeedWidget(Exception):
    """Raised by ``ctx.widget`` when a rich, data-bound widget must be rendered
    and the fields it collects aren't filled yet. The runtime catches this,
    stashes the (already data-bound) widget payload in ``state['pending_widget']``,
    and pauses — exactly like NeedField, but carrying a whole widget instead of a
    single scalar prompt. On resume the widget's submit fills the fields and the
    flow replays past this point."""

    def __init__(
        self,
        *,
        widget_id: str,
        data: dict[str, Any],
        fields: list[str],
        submit_schema: dict[str, Any] | None = None,
        prompt: str | None = None,
        spec: dict[str, Any] | None = None,
    ):
        self.widget_id = widget_id
        self.data = data
        self.fields = fields
        self.submit_schema = submit_schema
        self.prompt = prompt
        # Auto-generated widgets (pick:) carry their SpecNode tree inline so
        # the frontend needs no registered widget file for them.
        self.spec = spec
        super().__init__(f"need widget: {widget_id}")


class ChatMode(Exception):
    """Raised by ``ctx.chat`` — the flow has reached its open-conversation
    step. The runtime catches this and switches turn routing: subsequent user
    messages go interpreter-first (corrections / cancel / flow-switch stay
    deterministic), and anything that isn't a structured command is answered
    by a scoped tool-calling loop whose pinned args come from validated flow
    state, never from the model."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        super().__init__("chat mode")


def _text_fallback(value: Any, title: str | None) -> str:
    """Compact text rendering of ``value`` for hosts without widget support."""
    lines: list[str] = [title] if title else []
    if isinstance(value, list):
        for row in value[:20]:
            if isinstance(row, dict):
                lines.append("  " + "  |  ".join(f"{k}: {v}" for k, v in row.items()))
            else:
                lines.append(f"  - {row}")
        if isinstance(value, list) and len(value) > 20:
            lines.append(f"  … {len(value) - 20} more")
    elif isinstance(value, dict):
        for k, v in value.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append(str(value))
    return "\n".join(lines) if lines else str(value)


class Context:
    """The object passed to every ``@flow`` function. Holds a live reference
    to state, the active skill's field specs, and the host for emitting
    messages."""

    def __init__(
        self,
        state: dict[str, Any],
        field_specs: dict[str, FieldSpec],
        emit: Callable[[str], None],
        host: Any = None,
    ):
        self.state = state
        self._field_specs = field_specs
        self._emit = emit
        # Optional richer host surface (emit_widget etc.). ``emit`` remains
        # the only REQUIRED channel so existing callers are unaffected.
        self._host = host
        self._say_counter = 0
        self._link_counter = 0

    # ----------------------------------------------------------------- fields
    @property
    def fields(self) -> dict[str, Any]:
        """Convenience accessor. Mutations should go through ``ctx.ask`` /
        ``ctx.set`` — direct writes here skip validation."""
        return self.state.setdefault("fields", {})

    @property
    def results(self) -> dict[str, Any]:
        """Memoized action results."""
        return self.state.setdefault("results", {})

    def ask(self, field: str, prompt: str | None = None) -> Any:
        """Return the field's value if filled; otherwise pause execution.

        The flow function calls this once per field it needs. On the first
        turn, the call raises ``NeedField`` — the host pauses, asks the
        user, and on resume the runtime re-runs the flow from the top with
        the field now filled. The second call to ``ctx.ask(field)`` returns
        the value immediately.
        """
        fields = self.fields
        if field in fields and fields[field] is not None:
            return fields[field]

        spec = self._field_specs.get(field)
        effective_prompt = (
            prompt
            or (spec.description if spec else None)
            or f"Please provide a value for {field!r}."
        )
        raise NeedField(field, effective_prompt)

    def set(self, field: str, value: Any) -> None:
        """Programmatically set a field. Used by the runtime when applying
        SetField items, and occasionally by the dev to seed a field from
        an action result."""
        spec = self._field_specs.get(field)
        if spec is not None:
            coerced = coerce_and_validate(spec, value)
        else:
            coerced = value
        self.fields[field] = coerced
        self.state.setdefault("field_history", {}).setdefault(field, []).append(coerced)

    # ----------------------------------------------------------------- actions
    def run(self, fn: Callable[[dict[str, Any]], Any], name: str | None = None) -> Any:
        """Run a registered action; memoize the result.

        Pass a function (preferred) — its ``__name__`` (or ``@action(name=...)``
        override) is the cache key.

        Actions run ONCE per flow run; the result lives in ``state.results``.
        To force re-execution after the dev knows state has changed in a way
        that invalidates the result, pop the key from ``state.results``.

        A GATED WRITE — a declarative ``actions:`` entry with ``requires:``, or a
        Python ``@action(requires="...")`` — gets two extra properties that a
        read does not:

          * its gate is re-checked here, at dispatch, so the Python escape hatch
            is held to the same rule as a declarative ``do:`` step;
          * its result is recorded in the sticky ``effects_log``, not only in
            ``results``. ``results`` is cleared by every correction (that is what
            re-arms downstream gates), so an at-most-once guarantee that lived
            only there charged the same card twice: correct the amount, the memo
            is gone, the action runs again. The log survives corrections, flow
            switches and digressions, so a confirmed intent fires exactly one
            side effect.
        """
        key = name or getattr(fn, "_flow_action_name", None) or fn.__name__
        results = self.results
        if key in results:
            return results[key]

        requires = getattr(fn, "_flow_effect_requires", None) or getattr(
            fn, "_flow_action_requires", None
        )
        gated = bool(requires) or bool(getattr(fn, "_flow_effect_gated", False))

        log_key = None
        if gated:
            log_key = f"{key}:{self._run_id()}"
            log = self.state.setdefault("effects_log", {})
            if log_key in log:
                # Already fired this run. Return the recorded result — never
                # re-execute, even though `results` was cleared by a correction.
                # Checked BEFORE the gate: it WAS authorized when it fired, and a
                # correction that has since voided the confirmation must not turn
                # a replay of a completed effect into an error.
                results[key] = log[log_key]
                return log[log_key]

        if requires and not self.fields.get(requires):
            raise PermissionError(
                f"action {key!r} requires {requires!r} to be true before it can run"
            )

        out = fn(self.state)
        results[key] = out
        if log_key is not None:
            self.state.setdefault("effects_log", {})[log_key] = out
        return out

    def _run_id(self) -> str:
        """The stable id of this flow run. ``reset_for_new_flow`` normally sets
        it; mint one once for callers that preset ``flow_id`` without it."""
        rid = self.state.get("flow_run_id")
        if not rid:
            import uuid

            rid = uuid.uuid4().hex
            self.state["flow_run_id"] = rid
        return rid

    # ----------------------------------------------------------------- widgets
    def widget(
        self,
        widget_id: str,
        *,
        save: list[str],
        data: dict[str, Any] | None = None,
        submit_schema: dict[str, Any] | None = None,
        prompt: str | None = None,
        spec: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Render a rich, data-bound widget (a form, a table, a timeline, …) and
        pause until the user submits it.

        ``save`` is the list of fields the widget's submit fills. Replay-safe: on
        the turn after the user submits, all ``save`` fields are filled, so this
        returns immediately and the flow continues to the next step. ``data`` is
        already resolved (e.g. the rows/options bound from a prior tool result);
        the host surfaces it to the frontend, which renders the widget and posts
        the ``save`` fields back as a structured (LLM-free) submission.
        """
        fields = self.fields
        if all(fields.get(f) is not None for f in save):
            return {f: fields.get(f) for f in save}
        raise NeedWidget(
            widget_id=widget_id,
            data=data or {},
            fields=list(save),
            submit_schema=submit_schema,
            prompt=prompt,
            spec=spec,
        )

    def rewind(self) -> None:
        """Start this turn's replay again from the first step.

        ``say`` and ``show`` are emitted once per run by comparing a position
        counted during the replay against the ``say_cursor`` in state. A second
        pass in the same turn has to count from zero again, or every message
        already said would look new and be said twice.
        """
        self._say_counter = 0
        self._link_counter = 0

    def show(self, value: Any, *, title: str | None = None) -> None:
        """Display ``value`` as an auto-chosen widget (card / grid / timeline
        by shape). Display-only — never pauses, never collects. Replay-safe:
        shares the say-cursor, so each show emits exactly once per flow run.

        If the host can't render widgets (no ``emit_widget``), degrades to a
        compact text rendering so CLI/test hosts still show the data.
        """
        idx = self._say_counter
        self._say_counter += 1
        cursor = self.state.get("say_cursor", 0)
        if idx < cursor:
            return
        self.state["say_cursor"] = idx + 1

        from .autowidget import spec_for  # local: keep context import-light

        spec = spec_for(value, title=title)
        emit_widget = getattr(self._host, "emit_widget", None) if self._host else None
        if emit_widget is not None:
            emit_widget(spec)
        else:
            self._emit(_text_fallback(value, title))

    def show_widget(self, widget_id: str, data: dict[str, Any] | None = None) -> None:
        """Display a designed widget from the catalog with ``data`` bound into
        it. Display-only, and replay-safe like ``show``: it shares the
        say-cursor. A widget the catalog does not have shows its data as a card.
        """
        idx = self._say_counter
        self._say_counter += 1
        cursor = self.state.get("say_cursor", 0)
        if idx < cursor:
            return
        self.state["say_cursor"] = idx + 1

        data = dict(data or {})
        spec: dict[str, Any] | None = None
        try:
            from .engine import bindings
            from .engine.loader import load_widgets

            chosen = load_widgets().get(widget_id)
            if chosen is not None:
                fields = dict(self.fields)
                spec = bindings.resolve(
                    chosen.spec.model_dump(exclude_none=True), {"fields": fields, **fields, **data},
                )
        except Exception:  # noqa: BLE001 - a broken catalog shows the data plainly
            spec = None
        emit_widget = getattr(self._host, "emit_widget", None) if self._host else None
        if spec is None:
            from .autowidget import spec_for

            spec = spec_for(data, title=widget_id.replace("_", " "))
        if emit_widget is not None:
            emit_widget(spec)
        else:
            self._emit(_text_fallback(data, widget_id.replace("_", " ")))

    def chat(self, config: dict[str, Any]) -> None:
        """Enter the flow's open-conversation step (the agentic leaf).

        Emits the greeting once (replay-safe), records the chat config in
        state, and pauses. From here the runtime routes each user turn:
        structured commands (corrections, cancel, switching flows) stay
        deterministic; everything else is answered by a tool-calling loop
        restricted to ``config["tools"]`` with ``config["pin"]`` args
        injected from validated fields — the model never chooses those.
        """
        greeting = config.get("greeting")
        if greeting:
            self.say(greeting)
        raise ChatMode(config)

    # ----------------------------------------------------------------- emit
    def say(self, text: str) -> None:
        """Emit an assistant text message. Replay-safe: a message is emitted
        exactly once per flow run even across re-executions."""
        idx = self._say_counter
        self._say_counter += 1
        cursor = self.state.get("say_cursor", 0)
        if idx < cursor:
            return
        self.state["say_cursor"] = idx + 1
        self._emit(text)

    # ----------------------------------------------------------------- control
    def link(self, flow_id: str) -> bool:
        """Programmatic hand-off to another flow. The current frame is pushed;
        when the target flow finishes (or is cancelled), the runtime pops back
        and this flow carries on from the line after the link.

        Returns ``True`` when the target ran to its end and ``False`` when the
        user cancelled it, left it part-way, or said no at a confirmation and
        none of its writes fired (decided for YAML flows; a Python flow that is
        declined still returns ``True``), so the caller decides what an
        unfinished step means::

            if not ctx.link("add-payee"):
                ctx.say("No payee was added, so there is nothing to pay.")
                return
            ctx.link("pay-someone")

        Nothing else comes back: the two flows do not see each other's fields.

        A link runs once. The flow replays from the top on every turn, so a
        link that has already been taken is recognised by its position and its
        target (``links_done`` in state) and skipped — otherwise the parent
        would hand off afresh each time it reached this line. A DIFFERENT
        target at the same position (the branch condition changed) is a new
        link and is taken.
        """
        idx = self._link_counter
        self._link_counter += 1
        done = self.state.get("links_done") or []
        if idx < len(done) and done[idx].get("flow") == flow_id:
            return bool(done[idx].get("completed"))
        raise FlowLink(flow_id, index=idx)

    def end(self) -> None:
        """End the current flow early. Raises FlowDone."""
        raise FlowDone()

    # ----------------------------------------------------------------- helpers
    def has(self, field: str) -> bool:
        """True if a field has been filled."""
        return self.fields.get(field) is not None

    def ask_group(self, *field_names: str, prompts: list[str] | None = None) -> tuple[Any, ...]:
        """Collect multiple fields in parallel.

        If ALL requested fields are filled, returns a tuple of their values.
        If ANY are unfilled, raises ``NeedFields`` with the list of unfilled
        field names and their prompts so the runtime can render a multi-field
        form.
        """
        fields = self.fields
        unfilled: list[str] = []
        unfilled_prompts: list[str] = []
        for i, name in enumerate(field_names):
            if name not in fields or fields[name] is None:
                unfilled.append(name)
                # Determine prompt for this field
                if prompts and i < len(prompts):
                    unfilled_prompts.append(prompts[i])
                else:
                    spec = self._field_specs.get(name)
                    unfilled_prompts.append(
                        (spec.description if spec else None)
                        or f"Please provide a value for {name!r}."
                    )
        if unfilled:
            raise NeedFields(unfilled, unfilled_prompts)
        return tuple(fields[name] for name in field_names)

    # ----------------------------------------------------------------- back-compat
    # Old API names — kept as thin shims so handler files written against
    # the slot-era API don't break in this refactor. New code should use
    # ``ask`` / ``run`` / ``set`` / ``fields``.
    def collect(self, slot: str, prompt: str | None = None) -> Any:
        return self.ask(slot, prompt)

    def action(self, fn, name: str | None = None) -> Any:
        return self.run(fn, name)

    def set_slot(self, slot: str, value: Any) -> None:
        self.set(slot, value)

    @property
    def slots(self) -> dict[str, Any]:
        return self.fields

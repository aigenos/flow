"""The short form: a skill is a list of steps.

    steps:
      - ask: { amount: "How much? (USD)" }
        check: { quote_payment.within_balance: "That is more than your balance." }
      - ask: { code: "Enter the 6-digit code." }
        when: quote_payment.needs_code
      - confirm: Send this payment?
      - do: send_payment
      - say: "Sent. Reference {{send_payment.reference}}."

Five kinds of step, two condition words and one for repetition:

    ask      get an answer, stored under a name
    say      tell the user something
    do       run a tool from the library, or another skill
    confirm  get a yes; "no" stops the flow, and nothing after it runs
    chat     the last step: answer the user's questions with lookups only

    when     run this step (or a block of steps) only if a condition holds
    check    what an answer must satisfy, with the sentence to say when it
             does not; the question is then asked again
    repeat   ask the steps under it once per item, collecting a list

Everything else is worked out:

* a tool gets the values whose names match its parameters;
* an answer's type comes from the tool that takes it (``amount: float``);
* a lookup is not a step: mention ``quote_payment.needs_code`` and the lookup
  is run when it is first needed;
* whether a tool changes anything is the TOOL's statement (``@readonly``), so
  a skill cannot run a write without a ``confirm`` before it.

Nothing here is a second engine. The steps are expanded into the long form
(``fields:`` / ``flow:``) and handed to the same loader, so every load-time
check and every runtime guarantee is the existing one.
"""
from __future__ import annotations

import ast
import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .declarative import (
    AuditSink,
    ToolRegistry,
    _assert_finite_args,
    template_roots,
    widget_namespace,
)
from .decorators import action
from .engine.bindings import resolve as resolve_bindings
from .engine.safe_eval import free_vars
from .fields import FieldSpec
from .toolbox import ToolInfo, as_named, call_tool, describe_tool

VERBS = ("ask", "say", "do", "confirm", "chat")
_OPTIONS: dict[str, frozenset[str]] = {
    "ask": frozenset({"choices", "from", "check", "type", "pattern", "widget", "with", "when"}),
    "say": frozenset({"when", "title", "widget", "with"}),
    "do": frozenset({"with", "as", "else", "when"}),
    "confirm": frozenset({"by", "widget", "with", "when"}),
    "chat": frozenset({"tools", "when"}),
}
#: The words that hold steps of their own: a `when:` block's `steps:`, and `repeat:`.
BLOCKS = ("steps", "repeat")
_ALL_KEYS = set(VERBS) | set().union(*_OPTIONS.values()) | set(BLOCKS)
#: Every word a skill written as steps may use.
WORDS = frozenset(_ALL_KEYS)

#: What a person calls an answer's type -> (field type, is a secret).
_TYPES: dict[str, tuple[str, bool]] = {
    "text": ("string", False), "string": ("string", False),
    "number": ("number", False),
    "whole number": ("integer", False), "integer": ("integer", False),
    "yes/no": ("boolean", False), "boolean": ("boolean", False),
    "secret": ("string", True),
}

#: Long-form words someone may reach for, and what the short form does instead.
_LONG_FORM: dict[str, str] = {
    "then_say": "put a `say:` step after the `do:`",
    "else_say": "write `check: { <condition>: \"<what to say>\" }` on the `ask:`",
    "else_clear": "a failed `check:` asks its own question again, so nothing needs clearing",
    "save": "`ask: { name: \"question\" }` already says where the answer goes",
    "pick": "use `ask:` with `from: <tool>` to choose from a list",
    "show": "use `say: \"{{tool.result}}\"`; data is shown as a card",
    "link": "use `do: other-skill`",
    "requires": "put a `confirm:` step before the `do:`",
    "when_missing": "an `ask:` is skipped by itself once its answer is known",
    "prompt": "the question goes in the `ask:` itself",
    "run": "use `do:`",
    "get": "a lookup is not a step: just use its result, e.g. `when: find_order.found`",
    "pin": "a chat's tools are given the answers named after their inputs, fixed; list the tools in `tools:`",
    "greeting": "the `chat:` sentence is what is said first",
    "loop": "use `repeat: { items: \"Add another?\" }` with the steps to repeat under it in `steps:`",
    "for_each": "use `repeat: { items: \"Add another?\" }` with the steps to repeat under it in `steps:`",
    "foreach": "use `repeat: { items: \"Add another?\" }` with the steps to repeat under it in `steps:`",
    "while": "use `repeat: { items: \"Add another?\" }`: its question decides whether there is another item",
    "until": "use `repeat: { items: \"Add another?\" }`: its question decides whether there is another item",
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: "Anything we should know? (optional)": the user is told they may skip it,
#: so they may. The words are the switch; there is no separate keyword.
_OPTIONAL_RE = re.compile(r"\(\s*optional\s*\)\s*[.?!]?\s*$", re.IGNORECASE)
_WHOLE_REF_RE = re.compile(r"^\s*\{\{\s*[\w.]+\s*\}\}\s*$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DECLINED = "Okay — no changes were made."


class ShortFormError(ValueError):
    """A problem in a ``steps:`` block, worded for the person who wrote it."""


def _near(bad: Any, valid: Any) -> str:
    match = difflib.get_close_matches(str(bad), sorted(str(v) for v in valid), n=1, cutoff=0.6)
    return f" (did you mean {match[0]!r}?)" if match else ""


def _label(name: str) -> str:
    return name.replace("_", " ").strip()


@dataclass
class _Ask:
    name: str
    question: str | None
    where: str
    terms: tuple[str, ...]
    choices: list[str] | None = None
    type: str | None = None
    secret: bool = False
    picked: bool = False
    pattern: str | None = None
    optional: bool = False
    asked_of: str | None = None     # a yes that someone else gives
    repeat: str | None = None       # one item's answer, inside `repeat:` of this list


@dataclass
class _Repeat:
    """A `repeat:` the walk has passed: the list it collects."""

    name: str
    question: str
    more: str                       # the yes/no answer that starts another item
    where: str
    items: list[str]                # the answers each item holds


@dataclass
class _Check:
    """A `check:` waiting for the answers it reads."""

    condition: str
    message: str
    names: list[str]
    terms: tuple[str, ...]
    where: str
    roots: set[str]


@dataclass
class _Gate:
    """A `confirm:` the walk has passed."""

    name: str
    conditions: tuple[str, ...]   # the `when:`s it sits under (the author's, not other yeses)
    where: str

    @property
    def term(self) -> str:
        # Asked on every path: later steps need its yes. Asked only on some:
        # later steps need it NOT to have been refused (unasked blocks nothing).
        return self.name if not self.conditions else f"{self.name} != False"


@dataclass
class _Use:
    """One tool, as this skill uses it."""

    action: str            # the name its result goes by
    tool: str
    info: ToolInfo
    where: str
    terms: tuple[str, ...] | None   # None once it is known to run unconditionally
    bindings: dict[str, Any] = field(default_factory=dict)
    requires: str | None = None


def _expr(terms: tuple[str, ...] | list[str]) -> str | None:
    terms = [t for t in terms if t]
    if not terms:
        return None
    if len(terms) == 1:
        return terms[0]
    return " and ".join(f"({t})" for t in terms)


def _guarded(step: dict[str, Any], terms: tuple[str, ...] | list[str]) -> dict[str, Any]:
    expr = _expr(terms)
    return {"when": expr, **step} if expr else step


class _Expander:
    def __init__(self, frontmatter: Any, registry: ToolRegistry, sink: AuditSink,
                 skill_dir: Any = None) -> None:
        self.fm = frontmatter
        self.skill = frontmatter.name
        self.registry = registry
        self.sink = sink
        self.skill_dir = skill_dir
        self.compute: dict[str, str] = dict(frontmatter.compute or {})
        self.out: list[dict[str, Any]] = []
        self.asks: dict[str, _Ask] = {}
        self.uses: dict[str, _Use] = {}
        self.confirms: list[str] = []
        self.gates: list[_Gate] = []        # every confirm passed so far, in order
        self.gate_terms: tuple[str, ...] = ()   # what "nobody said no above" means here
        self.gate_reads: set[str] = set()   # names a when:/check: reads
        self.when_reads: set[str] = set()   # names a `when:` reads: these decide a branch
        self.rules: dict[str, list[tuple[str, str]]] = {}   # rules that read only their own answer
        self.conditions: list[tuple[str, str]] = []   # every condition, and where it is
        self.pending: list[_Check] = []     # checks whose inputs are not all asked yet
        self._infos: dict[str, ToolInfo] = {}
        self._later: dict[str, str] = {}
        self._passed: dict[str, set[str]] = {}   # tool -> the inputs some `with:` names
        self.lists: dict[str, _Repeat] = {}      # what each `repeat:` collects
        self.scope: list[str] = []               # the repeat being walked, if any
        self.loop_answers: set[str] = set()      # "add another?": only the user answers
        self.item_lookups: dict[str, str] = {}   # lookup -> the repeat whose item it reads
        self.chat_tools: list[str] = []          # what a closing `chat:` may call
        self.pins: list[tuple[str, str, str]] = []   # (tool, input fixed to an answer, where)

    # ------------------------------------------------------------------ errors
    def fail(self, where: str, message: str) -> ShortFormError:
        return ShortFormError(f"skill {self.skill!r}: {where}: {message}")

    # ------------------------------------------------------------------ tools
    def info(self, tool: str) -> ToolInfo:
        if tool not in self._infos:
            info = describe_tool(tool, self.registry.raw(tool), self.registry.descriptor(tool))
            # `def find_order(order_id)` handed in through `tools=` with no
            # annotation could be "one dict" or "one input". A skill that asks
            # for `order_id`, or passes it with `with:`, settles it.
            if info.style == "dict" and info.loose and (
                info.loose in self._later or info.loose in self.compute
                or info.loose in self._passed.get(tool, ())
            ):
                info = as_named(info)
            self._infos[tool] = info
        return self._infos[tool]

    def _known_names(self) -> set[str]:
        return set(self.asks) | set(self.uses) | set(self.compute) | set(self.registry.names()) | set(self.lists)

    def _outside_item(self, name: str) -> str | None:
        """The repeat ``name`` is one item's answer of, when read from outside it."""
        ask = self.asks.get(name)
        if ask is not None and ask.repeat and ask.repeat not in self.scope:
            return ask.repeat
        lookup = self.item_lookups.get(name)
        if lookup and lookup not in self.scope:
            return lookup
        return None

    def _can_run(self, tool: str, terms: tuple[str, ...]) -> bool:
        """Could ``tool`` run here, or is something it needs still to be asked?"""
        use = self.uses.get(tool)
        info = use.info if use is not None else self.info(tool)
        for p in info.params:
            if not p.required or (use is not None and p.name in use.bindings):
                continue
            if p.name in self.compute or p.name == "flow_run_id" or p.name in self.uses:
                continue
            if p.name in self.lists:
                continue                # the list a `repeat:` above collected
            if self._outside_item(p.name):
                return False
            ask = self.asks.get(p.name)
            if ask is None and p.name in self._later:
                return False            # asked further down: wait for it
            if ask is not None and not set(ask.terms) <= set(terms):
                return False
        return True

    def flush_checks(self, *, final: bool = False) -> None:
        """Place every waiting check whose answers have now all been asked.

        A check is written under the answer it judges and runs as soon as
        everything it reads exists: "is this routing number valid?" can sit on
        the routing question although the lookup also needs the account number
        asked two lines later.
        """
        waiting: list[_Check] = []
        for c in self.pending:
            tools = [r for r in c.roots if r in self.uses or self.registry.has(r)]
            if not final and not all(self._can_run(t, c.terms) for t in tools):
                waiting.append(c)
                continue
            self.need(c.roots, c.terms, c.where)
            self.out.append(_guarded(
                {"check": c.condition, "else_say": c.message, "else_clear": list(c.names)}, c.terms
            ))
        self.pending = waiting

    def _inputs_ready(self, use: _Use, terms: tuple[str, ...], where: str) -> None:
        """Every value the tool needs has been asked for by this point."""
        for p in use.info.params:
            if p.name in use.bindings or not p.required:
                continue
            if p.name in self.compute or p.name == "flow_run_id":
                continue
            if p.name in self.uses:
                continue  # the result of an earlier tool, passed by name
            if p.name in self.lists:
                continue  # the list a `repeat:` above collected
            outside = self._outside_item(p.name)
            if outside:
                raise self.fail(
                    where,
                    f"`{use.tool}` needs `{p.name}`, which is one item's answer inside `repeat: "
                    f"{outside}`. After the repeat a tool takes the whole list: name its input "
                    f"`{outside}`.",
                )
            ask = self.asks.get(p.name)
            if ask is None:
                later = self._later.get(p.name)
                if later:
                    hint = f"It is asked later ({later}); move that question above this step."
                else:
                    hint = (
                        f"Ask for it first with `- ask: {{ {p.name}: \"...\" }}`"
                        f"{_near(p.name, self.asks)}, or pass it with `with: {{ {p.name}: ... }}`."
                    )
                raise self.fail(where, f"`{use.tool}` needs `{p.name}`. {hint}")
            if not set(ask.terms) <= set(terms):
                only = _expr(ask.terms)
                raise self.fail(
                    where,
                    f"`{use.tool}` needs `{p.name}`, but `{p.name}` is only asked when "
                    f"`{only}` ({ask.where}). Give this step the same `when:`, or make "
                    f"`{p.name}` optional in the tool (a default value).",
                )

    def place(self, tool: str, terms: tuple[str, ...], where: str, *, explicit: bool,
              alias: str | None = None, bindings: dict[str, Any] | None = None,
              gate: str | None = None) -> None:
        """Make sure ``tool`` has run by this point, under these conditions."""
        action_name = alias or tool
        existing = self.uses.get(action_name)
        info = existing.info if existing is not None else self.info(tool)
        if action_name in self.asks:
            raise self.fail(
                where,
                f"`{action_name}` is both a tool and the name of an answer. A tool's result "
                "is read by the tool's name, so the two cannot share one. Rename the answer"
                + (", or name this result with `as:`." if not alias else "."),
            )

        if existing is not None:
            if not info.readonly and explicit:
                raise self.fail(
                    where,
                    f"`{tool}` already runs at {existing.where}, and a tool that changes "
                    "something runs once. To run it again with other inputs, give this "
                    "step its own name with `as:`.",
                )
            if existing.terms is None or set(existing.terms) <= set(terms):
                return  # it has certainly run by now
            if not info.readonly:
                return  # a write runs at its own step and nowhere else
            self._inputs_ready(existing, terms, where)
            self.out.append(_guarded({"do": action_name}, terms))
            if not terms:
                existing.terms = None
            return

        if not info.readonly:
            if not explicit:
                raise self.fail(
                    where,
                    f"uses the result of `{tool}` before `{tool}` has run. `{tool}` changes "
                    f"something, so it only runs at its own `- do: {tool}` step. Move this "
                    "below that step.",
                )
            if gate is None:
                raise self.fail(
                    where,
                    f"`{tool}` changes something (it is not marked @readonly), so it needs "
                    "the user's yes first. Put a `- confirm: \"...?\"` step before it. "
                    f"If `{tool}` only looks something up, mark it `@readonly` where it "
                    "is defined.",
                )

        use = _Use(action=action_name, tool=tool, info=info, where=where,
                   terms=terms or None, bindings=dict(bindings or {}),
                   requires=None if info.readonly else gate)
        for pname in use.bindings:
            if info.style == "named" and info.param(pname) is None:
                raise self.fail(
                    where,
                    f"`with:` passes `{pname}`, but `{tool}` takes "
                    f"{[p.name for p in info.params] or 'no inputs'}{_near(pname, [p.name for p in info.params])}",
                )
        for value in use.bindings.values():
            self.need(template_roots(value) if isinstance(value, str) else set(), terms, where)
        self._inputs_ready(use, terms, where)
        self.uses[action_name] = use
        self.out.append(_guarded({"do": action_name}, terms))

    def need(self, roots: set[str], terms: tuple[str, ...], where: str) -> None:
        """Every name a condition or a sentence reads must exist by this point."""
        for root in sorted(roots):
            outside = self._outside_item(root)
            if outside:
                raise self.fail(
                    where,
                    f"reads `{root}`, which belongs to one item of `repeat: {outside}`. After the "
                    f"repeat, read `{outside}`: a list with one record per item.",
                )
            if root in self.asks or root in self.compute or root == "flow_run_id" or root in self.lists:
                continue
            if root in self.uses or self.registry.has(root):
                self.place(root, terms, where, explicit=False)
                continue
            later = self._later.get(root)
            if later:
                raise self.fail(where, f"reads `{root}`, which is only asked later ({later}). "
                                       "Move that question above this step.")
            raise self.fail(
                where,
                f"reads `{root}`, which is not an answer asked above, a tool in the library "
                f"or a `compute:` name{_near(root, self._known_names())}",
            )

    # ------------------------------------------------------------------ steps
    def _scan_later(self, steps: list[Any], prefix: str) -> None:
        for n, step in enumerate(steps, 1):
            if not isinstance(step, dict):
                continue
            where = f"step {prefix}{n}"
            ask = step.get("ask")
            for name in ([ask] if isinstance(ask, str) else list(ask or {}) if isinstance(ask, dict) else []):
                self._later.setdefault(str(name), where)
            if isinstance(step.get("do"), str) and isinstance(step.get("with"), dict):
                self._passed.setdefault(step["do"].strip(), set()).update(map(str, step["with"]))
            if isinstance(step.get("steps"), list):
                self._scan_later(step["steps"], f"{prefix}{n}.")

    def walk(self, steps: Any, terms: tuple[str, ...], prefix: str = "") -> None:
        if not isinstance(steps, list) or not steps:
            raise self.fail(f"steps{(' under step ' + prefix.rstrip('.')) if prefix else ''}",
                            "must be a list with at least one step")
        for n, step in enumerate(steps, 1):
            where = f"step {prefix}{n}"
            if not isinstance(step, dict):
                raise self.fail(where, f"must look like `- ask: ...`, `- say: ...`, `- do: ...` "
                                       f"or `- confirm: ...` (got {step!r})")
            for key in step:
                if key not in _ALL_KEYS:
                    if key in _LONG_FORM:
                        raise self.fail(where, f"`{key}:` is not used in `steps:`. Instead, "
                                               f"{_LONG_FORM[key]}.")
                    raise self.fail(where, f"unknown word `{key}`{_near(key, _ALL_KEYS)}. A step is "
                                           f"one of {', '.join(VERBS[:-1])} or {VERBS[-1]}; `when:` makes it "
                                           "conditional, and `repeat:` holds steps asked once per item.")
            verbs = [v for v in VERBS if v in step]

            # ---- `repeat:`: the steps under it, once per item of a list
            if "repeat" in step:
                self.repeat(step, terms, where, f"{prefix}{n}.")
                continue

            # ---- a `when:` block holding its own steps
            if "steps" in step:
                if verbs or set(step) - {"when", "steps"}:
                    raise self.fail(where, "a `when:` block holds only `when:` and its own `steps:`")
                if not step.get("when"):
                    raise self.fail(where, "nested `steps:` need a `when:` saying when they run")
                self.need(self.condition(step["when"], where, decides=True), terms + self.gate_terms, where)
                self.walk(step["steps"], terms + (str(step["when"]),), f"{prefix}{n}.")
                continue

            if len(verbs) != 1:
                raise self.fail(
                    where,
                    "a step does one thing; write one of ask / say / do / confirm"
                    + (f" (this one has {' and '.join(verbs)}: split it in two)" if verbs else ""),
                )
            verb = verbs[0]
            for key in step:
                if key != verb and key not in _OPTIONS[verb]:
                    raise self.fail(where, f"`{key}:` does not go with `{verb}:`"
                                           f"{' (it goes with ' + ', '.join(sorted(_OPTIONS[verb])) + ')' if _OPTIONS[verb] else ''}")
            if "widget" in step and not str(step.get("widget") or "").strip():
                raise self.fail(where, "`widget:` names a widget you designed. Choose one, or remove `widget:`.")
            if self.scope and verb != "ask":
                raise self.fail(where, self._not_in_repeat(verb))
            if verb == "chat" and (prefix or n != len(steps)):
                raise self.fail(where, "a `chat:` step is the last step of a skill, outside any block: "
                                       "the conversation stays in it until the user moves on")

            own = terms
            if step.get("when") is not None:
                roots = self.condition(step["when"], where, decides=True)
                self.need(roots, terms + self.gate_terms, where)
                own = terms + (str(step["when"]),)
            live = own + self.gate_terms   # nobody has said no to anything above

            if verb == "ask":
                self.ask(step, live, where)
            elif verb == "say":
                self.say(step, live, where)
            elif verb == "do":
                self.do(step, live, own, where)
            elif verb == "chat":
                self.chat(step, live, where)
            else:
                self.confirm(step, live, own, where)

    def _not_in_repeat(self, verb: str) -> str:
        list_name = self.scope[-1]
        reason = {
            "say": "Put the `say:` before or after the repeat.",
            "do": f"Run the tool once, after the repeat, and give it the whole list: name its input `{list_name}`.",
            "confirm": "Confirm once, after the repeat: the confirmation shows the whole list.",
            "chat": "A `chat:` step is the last step of a skill.",
        }.get(verb, "")
        return f"inside `repeat:`, the steps ask for one item's answers. {reason}".rstrip()

    # ---- repeat
    def repeat(self, step: dict[str, Any], terms: tuple[str, ...], where: str, inner: str) -> None:
        """`repeat: { travellers: "Add another traveller?" }` with the steps to repeat.

        The steps ask for one item's answers. When they are all answered the
        question is asked, and a yes starts the next item. Each item becomes one
        record of the list, which the steps after the repeat (and the tools they
        run) read by its name.
        """
        if self.scope:
            raise self.fail(where, "a `repeat:` cannot hold another `repeat:`. Collect one list, then the next.")
        extra = sorted(set(step) - {"repeat", "steps", "when"})
        if extra:
            raise self.fail(where, f"`{extra[0]}:` does not go with `repeat:`. It holds its own `steps:`, "
                                   "and may have a `when:`.")
        raw = step["repeat"]
        if not (isinstance(raw, dict) and len(raw) == 1):
            raise self.fail(where, 'write `repeat: { travellers: "Add another traveller?" }`: the name of the '
                                   "list, and the question that starts another item")
        name, question = next(iter(raw.items()))
        name = str(name)
        if not _IDENT_RE.match(name) or name == "flow_run_id":
            raise self.fail(where, f"`{name}` is not a usable name for a list: letters, digits and "
                                   "underscores, like `travellers`")
        if name in self.asks or name in self.lists or name in self.compute or name in self.uses \
                or self.registry.has(name):
            raise self.fail(where, f"`{name}` is already the name of an answer, a list or a tool. "
                                   "Call this list something else.")
        question = self.text(question, where, f"the question for `repeat: {name}`")
        if "{{" in question:
            raise self.fail(where, 'the repeat question is fixed text, like "Add another traveller?"')
        if not isinstance(step.get("steps"), list) or not step["steps"]:
            raise self.fail(where, "`repeat:` needs its own `steps:`: the questions asked once per item")
        own = terms
        if step.get("when") is not None:
            roots = self.condition(step["when"], where, decides=True)
            self.need(roots, terms + self.gate_terms, where)
            own = terms + (str(step["when"]),)
        live = own + self.gate_terms
        more = f"{name}_more"
        while more in self.asks or self.registry.has(more) or more in self.compute:
            more += "_"

        begin: dict[str, Any] = {"_repeat": name, "more": more, "items": []}
        self.out.append(_guarded(begin, live))
        before = list(self.asks)
        uses_before = set(self.uses)
        self.scope.append(name)
        try:
            self.walk(step["steps"], own, inner)
        finally:
            self.scope.pop()
        items = [a for a in self.asks if a not in before]
        if not items:
            raise self.fail(where, "the steps under `repeat:` ask nothing, so there is nothing to collect")
        for check in self.pending:
            if set(check.names) & set(items):
                raise self.fail(check.where, "this rule reads something only asked after the `repeat:`. "
                                             "Inside a repeat, a rule can read the answers asked above it.")
        begin["items"] = list(items)
        # A lookup that reads an item's answers starts afresh for the next item,
        # and means nothing after the repeat.
        reset: list[str] = []
        for action, use in self.uses.items():
            reads = {p.name for p in use.info.params} | {
                root for value in use.bindings.values() if isinstance(value, str)
                for root in template_roots(value)
            }
            if reads & set(items):
                reset.append(action)
                if action not in uses_before:
                    self.item_lookups[action] = name
        self.asks[more] = _Ask(name=more, question=question, where=where, terms=live, type="boolean")
        self.loop_answers.add(more)
        self.out.append(_guarded(
            {"_collect": name, "more": more, "items": list(items), "reset": sorted(reset)}, live,
        ))
        self.lists[name] = _Repeat(name=name, question=question, more=more, where=where, items=list(items))

    # ---- chat
    def chat(self, step: dict[str, Any], terms: tuple[str, ...], where: str) -> None:
        """`chat: "Ask me anything about this order."` with the `tools:` it may use.

        The conversation stays here: the model answers the user's questions,
        calling only the tools listed, and every one of them only looks
        something up. A tool's input with an answer of the same name above is
        given that answer, fixed; the model chooses the rest.
        """
        greeting = self.text(step["chat"], where, "`chat:`")
        if "{{" in greeting:
            raise self.fail(where, "the chat greeting is fixed text. Say anything with details in a "
                                   "`say:` step just before it.")
        tools = step.get("tools")
        if isinstance(tools, str):
            tools = [t for t in tools.replace(",", " ").split() if t]
        if not isinstance(tools, list) or not tools:
            raise self.fail(where, "`tools:` lists the lookups the chat may use, like "
                                   "`tools: [get_order, list_invoices]`")
        pins: dict[str, str] = {}
        names: list[str] = []
        for raw in tools:
            tool = str(raw).strip()
            if not self.registry.has(tool):
                raise self.fail(where, f"`{tool}` is not a tool in the library"
                                       f"{_near(tool, self.registry.names())}")
            info = self.info(tool)
            if not info.readonly:
                raise self.fail(
                    where,
                    f"`{tool}` changes something (it is not marked @readonly). In a chat the model "
                    "chooses which tool to call and with what, so a chat can only use tools that "
                    "look things up. Run it from a `do:` step after a `confirm:` instead.",
                )
            for p in info.params:
                ask = self.asks.get(p.name)
                if ask is None or ask.repeat or p.name in self.confirms:
                    continue
                if not set(ask.terms) <= set(terms):
                    raise self.fail(
                        where,
                        f"`{tool}` takes `{p.name}`, which is only asked when `{_expr(ask.terms)}` "
                        f"({ask.where}). The chat fixes it to that answer, so it has to be asked "
                        "on every way to the chat.",
                    )
                pins[p.name] = p.name
                self.pins.append((tool, p.name, where))
            names.append(tool)
            if tool not in self.chat_tools:
                self.chat_tools.append(tool)
        cfg: dict[str, Any] = {"tools": names, "greeting": greeting}
        if pins:
            cfg["pin"] = pins
        self.out.append(_guarded({"chat": cfg}, terms))

    def condition(self, expr: Any, where: str, *, decides: bool = False) -> set[str]:
        from .skills import _validate_expr

        if not isinstance(expr, str) or not expr.strip():
            raise self.fail(where, f"a condition is a short expression like `amount >= 1000` "
                                   f"or `find_order.found` (got {expr!r})")
        try:
            _validate_expr(expr, skill=self.skill, where=where)
            roots = free_vars(expr)
        except ValueError as e:
            raise self.fail(where, f"`{expr}` is not a condition the flow can check. Use names, "
                                   f"numbers, quoted text, comparisons and and/or/not. ({e})") from None
        self.gate_reads |= roots
        if decides:
            self.when_reads |= roots
        self.conditions.append((expr, where))
        return roots

    def _usage(self) -> tuple[dict[str, str], dict[str, str]]:
        """What the conditions say about each name: ``(numbers, yes_or_no)``.

        ``amount >= 1000`` can only be asked of a number, and a bare
        ``is_gift`` (or ``not is_gift``) only of a yes/no. Each maps the name to
        the step whose condition says so.
        """
        numbers: dict[str, str] = {}
        booleans: dict[str, str] = {}

        def is_number(node: ast.AST) -> bool:
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                node = node.operand
            return (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
                    and not isinstance(node.value, bool))

        def truthy(node: ast.AST) -> set[str]:
            if isinstance(node, ast.Name):
                return {node.id}
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
                return truthy(node.operand)
            if isinstance(node, ast.BoolOp):
                return set().union(*(truthy(v) for v in node.values))
            return set()

        for expr, where in self.conditions:
            try:
                tree = ast.parse(expr, mode="eval")
            except SyntaxError:  # already reported by condition()
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Compare):
                    sides = [node.left, *node.comparators]
                    if any(is_number(side) for side in sides):
                        for side in sides:
                            if isinstance(side, ast.Name):
                                numbers.setdefault(side.id, where)
            for name in truthy(tree.body):
                booleans.setdefault(name, where)
        return numbers, booleans

    def text(self, value: Any, where: str, what: str) -> str:
        if isinstance(value, dict):
            # YAML reads an unquoted {{name}} as a mapping.
            raise self.fail(where, f"{what} needs quotes when it starts with a brace: "
                                   "`say: \"{{name}}\"`")
        if not isinstance(value, str) or not value.strip():
            raise self.fail(where, f"{what} must be a sentence (got {value!r})")
        return value.strip()

    # ---- ask
    def ask(self, step: dict[str, Any], terms: tuple[str, ...], where: str) -> None:
        raw = step["ask"]
        if isinstance(raw, str):
            asked = {raw.strip(): None}
        elif isinstance(raw, dict) and raw:
            asked = dict(raw)
        else:
            raise self.fail(where, "write `ask: { name: \"Question?\" }` (or just `ask: name`)")
        names = list(asked)
        for name in names:
            if not isinstance(name, str) or not _IDENT_RE.match(name):
                raise self.fail(where, f"`{name}` is not a usable name for an answer: letters, "
                                       "digits and underscores, like `order_id`")
            if name == "flow_run_id":
                raise self.fail(where, "`flow_run_id` is the engine's own name; pick another")
            if name in self.uses or self.registry.has(name):
                raise self.fail(where, f"`{name}` is already the name of a tool, and a tool's "
                                       "result is read by that name. Call the answer something else.")
            question = asked[name]
            if question is not None:
                question = self.text(question, where, f"the question for `{name}`")
                if "{{" in question:
                    raise self.fail(where, "a question is fixed text. Put the details in a `say:` "
                                           "step just before it.")

        for name in names:
            owner = self.asks.get(name)
            if name in self.lists:
                raise self.fail(where, f"`{name}` is the list `repeat:` collects. Call this answer "
                                       "something else.")
            if self.scope and owner is not None and owner.repeat != self.scope[-1]:
                raise self.fail(where, f"`{name}` is already asked at {owner.where}. An answer inside "
                                       "`repeat:` is one item's, so give it its own name.")
            if not self.scope and owner is not None and owner.repeat:
                raise self.fail(where, f"`{name}` is one item's answer inside `repeat: {owner.repeat}`. "
                                       "Give this question its own name.")

        single = len(names) == 1
        choices = step.get("choices")
        source = step.get("from")
        if (choices is not None or source is not None) and not single:
            raise self.fail(where, "`choices:` and `from:` apply to one question; split this `ask:`")
        if choices is not None and source is not None:
            raise self.fail(where, "use `choices:` (a fixed list) or `from:` (rows from a tool), not both")
        if choices is not None and (not isinstance(choices, list) or not choices):
            raise self.fail(where, "`choices:` is a list, like `choices: [refund, exchange]`")

        ftype, secret = None, False
        if step.get("type") is not None:
            key = str(step["type"]).strip().lower()
            if key not in _TYPES:
                raise self.fail(where, f"`type: {step['type']}` is not a type{_near(key, _TYPES)}. "
                                       "Use text, number, whole number, yes/no or secret.")
            ftype, secret = _TYPES[key]
        if secret and self.scope:
            raise self.fail(where, "a secret cannot be one item of a list: the list is shown and handed "
                                   "on whole. Ask it before or after the `repeat:`.")

        pattern = step.get("pattern")
        if pattern is not None:
            try:
                re.compile(str(pattern))
            except re.error as e:
                raise self.fail(where, f"`pattern: {pattern}` is not a valid pattern ({e})") from None
            if ftype not in (None, "string") or choices is not None or source is not None:
                raise self.fail(where, "`pattern:` is the shape of a typed answer (a code, an id). "
                                       "It does not go with a number, a yes/no, `choices:` or `from:`.")
            pattern = str(pattern)

        optional = {n for n in names if asked[n] and _OPTIONAL_RE.search(str(asked[n]))}
        if optional and (choices is not None or source is not None or secret
                         or ftype not in (None, "string") or pattern is not None):
            raise self.fail(
                where,
                "a question marked (optional) may be left blank, and only a plain text answer "
                "can be blank. For a number, a choice or a code, ask a yes/no first and put "
                "`when:` on this question.",
            )

        for name in names:
            previous = self.asks.get(name)
            self.asks[name] = _Ask(
                name=name, question=asked[name] or (previous.question if previous else None),
                where=previous.where if previous else where,
                # asked on two branches: known wherever either branch ran
                terms=terms if previous is None else tuple(t for t in previous.terms if t in terms),
                choices=[str(c) for c in choices] if choices else (previous.choices if previous else None),
                type=ftype or (previous.type if previous else None),
                secret=secret or bool(previous and previous.secret),
                picked=bool(source) or bool(previous and previous.picked),
                pattern=pattern or (previous.pattern if previous else None),
                optional=name in optional or bool(previous and previous.optional),
                repeat=self.scope[-1] if self.scope else (previous.repeat if previous else None),
            )

        widget = step.get("widget")
        if source is not None:
            name = names[0]
            path = self.text(source, where, "`from:`").strip("{} ")
            tool = path.split(".", 1)[0]
            if not self.registry.has(tool):
                raise self.fail(where, f"`from: {path}` names `{tool}`, which is not a tool in "
                                       f"the library{_near(tool, self.registry.names())}")
            if not self.info(tool).readonly:
                raise self.fail(where, f"`from: {tool}` would run `{tool}` to build a list, but "
                                       f"`{tool}` is not marked @readonly. Only a lookup can fill a list.")
            if tool not in self.uses:
                # Looked up only while the answer is still unknown, so a later
                # step that reads the list must look it up for itself.
                use = _Use(action=tool, tool=tool, info=self.info(tool), where=where,
                           terms=(f"<{name} not chosen yet>",))
                self._inputs_ready(use, terms, where)
                self.uses[tool] = use
                self.out.append(_guarded({"when_missing": name, "do": tool}, terms))
            pick: dict[str, Any] = {
                "when_missing": name, "pick": "{{" + path + "}}", "save": [name],
                "prompt": self.asks[name].question or f"Choose the {_label(name)}.",
            }
            if widget:
                pick["widget"] = str(widget)
            self.out.append(_guarded(pick, terms))
        elif widget:
            shown: dict[str, Any] = {"widget": str(widget), "save": names}
            data = self._widget_data(step, terms, where)
            if data:
                shown["data"] = data
            self.out.append(_guarded(shown, terms))
        else:
            if step.get("with") is not None:
                raise self.fail(where, "`with:` on an `ask:` is the data for a designed widget, so it "
                                       "goes with `widget:`.")
            for name in names:
                self.out.append(_guarded({"ask": name}, terms))

        checks = step.get("check")
        if checks is None:
            self.flush_checks()
            return
        if isinstance(checks, list) and all(isinstance(c, dict) and len(c) == 1 for c in checks):
            pairs = [next(iter(c.items())) for c in checks]
        elif isinstance(checks, dict) and checks:
            pairs = list(checks.items())
        else:
            raise self.fail(where, "write `check: { <condition>: \"<what to say if it is not met>\" }`")
        for cond, message in pairs:
            roots = self.condition(cond, where)
            message = self.text(message, where, f"the sentence for `check: {cond}`")
            if single and roots == {names[0]} and template_roots(message) <= {names[0]}:
                # A rule that reads only the answer it judges (`amount > 0`) is
                # a property of the answer, like its type: it is applied the
                # moment a value arrives, whoever supplied it.
                self.rules.setdefault(names[0], []).append((str(cond), message))
                continue
            self.pending.append(_Check(
                condition=str(cond), message=message, names=list(names), terms=terms,
                where=where, roots=roots | template_roots(message),
            ))
        self.flush_checks()

    def _widget_data(self, step: dict[str, Any], terms: tuple[str, ...], where: str) -> dict[str, Any]:
        """`with:` beside `widget:`: what the designed widget is given to show."""
        data = step.get("with")
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise self.fail(where, "`with:` gives a widget its data, like "
                                   "`with: { total: \"{{quote.total}}\" }`")
        for value in data.values():
            if isinstance(value, str):
                self.need(template_roots(value), terms, where)
        return dict(data)

    # ---- say
    def say(self, step: dict[str, Any], terms: tuple[str, ...], where: str) -> None:
        text = self.text(step["say"], where, "`say:`")
        self.need(template_roots(text), terms, where)
        title = step.get("title")
        widget = step.get("widget")
        if widget:
            # A designed widget, shown and not answered: the sentence is said
            # above it, and `with:` gives it its data.
            if title is not None:
                raise self.fail(where, "`title:` names a card of data. A designed widget has its own heading.")
            if _WHOLE_REF_RE.match(text):
                raise self.fail(where, "with `widget:`, `say:` is the sentence said above the widget. "
                                       "Give the widget its data with `with:`.")
            self.out.append(_guarded({"say": text}, terms))
            shown: dict[str, Any] = {"_display": str(widget)}
            data = self._widget_data(step, terms, where)
            if data:
                shown["data"] = data
            self.out.append(_guarded(shown, terms))
            return
        if step.get("with") is not None:
            raise self.fail(where, "`with:` on a `say:` is the data for a designed widget, so it goes "
                                   "with `widget:`.")
        if _WHOLE_REF_RE.match(text):
            card: dict[str, Any] = {"show": text}               # data: shown as a card
            if title is not None:
                card["title"] = self.text(title, where, "`title:`")
            self.out.append(_guarded(card, terms))
        elif title is not None:
            raise self.fail(where, "`title:` names a card of data, like `say: \"{{quote.summary}}\"`. "
                                   "A sentence has no title.")
        else:
            self.out.append(_guarded({"say": text}, terms))

    # ---- do
    def _yes_for(self, own: tuple[str, ...]) -> _Gate | None:
        """The nearest confirm above that was certainly asked on this path."""
        for gate in reversed(self.gates):
            if set(gate.conditions) <= set(own):
                return gate
        return None

    def do(self, step: dict[str, Any], terms: tuple[str, ...], own: tuple[str, ...],
           where: str) -> None:
        target = step["do"]
        if not isinstance(target, str) or not target.strip():
            raise self.fail(where, "`do:` names a tool from the library or another skill")
        target = target.strip()
        bindings = step.get("with")
        if bindings is not None and not isinstance(bindings, dict):
            raise self.fail(where, "`with:` maps a tool's input to a value, like `with: { to: \"{{email}}\" }`")
        alias = step.get("as")
        if alias is not None and (not isinstance(alias, str) or not _IDENT_RE.match(alias)):
            raise self.fail(where, f"`as: {alias}` must be a plain name, like `second_quote`")

        if self.registry.has(target):
            if step.get("else") is not None:
                raise self.fail(where, f"`else:` goes with `do: <another skill>`: it is what to say "
                                       f"when that skill does not finish. `{target}` is a tool; to "
                                       "refuse an answer use `check:` on the question.")
            gate = None
            if not self.info(target).readonly:
                yes = self._yes_for(own)
                if yes is None and self.gates:
                    asked = self.gates[-1]
                    raise self.fail(
                        where,
                        f"`{target}` changes something, so it needs the user's yes on every "
                        f"path that reaches it. The `confirm:` above ({asked.where}) is only "
                        f"asked when `{_expr(asked.conditions)}`. Give this step the same "
                        "`when:`, or put a `confirm:` right before it.",
                    )
                if yes is not None:
                    gate = yes.name
                    # This step needs the yes itself, not merely "not refused".
                    terms = tuple(yes.name if t == yes.term else t for t in terms)
                    if yes.name not in terms:
                        terms = terms + (yes.name,)
            self.place(target, terms, where, explicit=True, alias=alias,
                       bindings=bindings, gate=gate)
            return

        close = difflib.get_close_matches(target, sorted(self.registry.names()), n=1, cutoff=0.75)
        if close and "-" not in target:
            raise self.fail(where, f"`{target}` is not a tool in the library (did you mean "
                                   f"{close[0]!r}?)")
        if not _SKILL_NAME_RE.match(target):
            raise self.fail(where, f"`{target}` is not a tool in the library"
                                   f"{_near(target, self.registry.names())}. To run another skill, "
                                   "use its name, like `do: add-payee`.")
        if target == self.skill:
            raise self.fail(where, "a skill cannot run itself; let it end and it can be started again")
        if bindings or alias:
            raise self.fail(where, f"`{target}` is another skill. It asks its own questions, so "
                                   "`with:` and `as:` do not apply.")
        link: dict[str, Any] = {"link": target}
        if step.get("else") is not None:
            # Said when that skill was cancelled or the user said no in it.
            # Either way this flow stops there.
            sentence = self.text(step["else"], where, "`else:`")
            self.need(template_roots(sentence), terms, where)
            link["else_say"] = sentence
        self.out.append(_guarded(link, terms))

    # ---- confirm
    def confirm(self, step: dict[str, Any], terms: tuple[str, ...], own: tuple[str, ...],
                where: str) -> str:
        raw = step["confirm"]
        named: str | None = None
        if isinstance(raw, dict) and len(raw) == 1 and not str(next(iter(raw))).startswith("{"):
            # `confirm: { pay: "Pay for this trip?" }`: the yes goes by a name
            # of your choosing (a designed widget submits it under that name).
            named, raw = next(iter(raw.items()))
            named = str(named)
            if not _IDENT_RE.match(named):
                raise self.fail(where, f"`{named}` is not a usable name for a confirmation: letters, "
                                       "digits and underscores, like `pay`")
            if named in self.asks or self.registry.has(named) or named in self.compute or named == "flow_run_id":
                raise self.fail(where, f"`{named}` is already the name of an answer or a tool. "
                                       "Call this confirmation something else.")
        question = self.text(raw, where, "`confirm:`")
        if "{{" in question:
            raise self.fail(where, "the confirm question is fixed text. Say what is about to happen "
                                   "in a `say:` step just before it.")
        name = named or ("confirm" if not self.confirms else f"confirm_{len(self.confirms) + 1}")
        while named is None and (name in self.asks or self.registry.has(name) or name in self.compute):
            name += "_"
        by = step.get("by")
        if by is not None:
            # A yes from someone else: the flow waits for the person named.
            if not isinstance(by, str) or not by.strip():
                raise self.fail(where, "`by:` names who is asked for the yes, like `by: manager`")
            if step.get("widget"):
                raise self.fail(where, "`widget:` is a page for the user in the conversation, and `by:` asks "
                                       "someone else. Use one or the other.")
            by = by.strip()
        self.confirms.append(name)
        self.asks[name] = _Ask(name=name, question=question, where=where, terms=terms, type="boolean",
                               asked_of=by)
        widget = step.get("widget")
        if widget:
            # A designed page for the yes (a checkout, a review screen). It is
            # still the yes: nothing after it runs without it.
            shown: dict[str, Any] = {"widget": str(widget), "save": [name]}
            data = self._widget_data(step, terms, where)
            if data:
                shown["data"] = data
            self.out.append(_guarded(shown, terms))
        elif step.get("with") is not None:
            raise self.fail(where, "`with:` on a `confirm:` is the data for a designed widget, so it "
                                   "goes with `widget:`.")
        else:
            self.out.append(_guarded({"ask": name}, terms))
        if by is None:
            # (When someone else says no, the flow says so in their name.)
            self.out.append({"_declined": name, "_terms": terms})
        gate = _Gate(name=name, conditions=tuple(own), where=where)
        self.gates.append(gate)
        # Asked on every path: its yes implies every yes above it, so it stands
        # alone. Asked on some: it joins the ones before it.
        self.gate_terms = (gate.term,) if not gate.conditions else self.gate_terms + (gate.term,)
        return name

    # ------------------------------------------------------------------ result
    def fields(self) -> dict[str, FieldSpec]:
        explicit: dict[str, FieldSpec] = dict(self.fm.fields or {})
        specs: dict[str, FieldSpec] = {}
        numbers, booleans = self._usage()
        for name, ask in self.asks.items():
            takers = []
            for use in self.uses.values():
                for p in use.info.params:
                    bound = use.bindings.get(p.name)
                    if (bound is None and p.name == name) or (
                        isinstance(bound, str) and bound.strip("{} ") == name
                    ):
                        takers.append((use, p))
            if name in explicit:
                spec = explicit[name].model_copy()
                if not spec.description:
                    spec.description = ask.question
                specs[name] = spec
                continue

            ftype = ask.type
            values = ask.choices
            if values:
                ftype = "enum"
            typed = [(u, p) for u, p in takers if p.type != "any"]
            if ftype is None and typed:
                kinds = {p.type for _, p in typed}
                if kinds == {"integer", "number"}:
                    kinds = {"number"}
                if len(kinds) > 1:
                    detail = ", ".join(f"`{u.tool}` takes it as {p.type}" for u, p in typed)
                    raise self.fail(ask.where, f"`{name}` is passed to tools that disagree about "
                                               f"what it is ({detail}). Say which with `type:`.")
                ftype = kinds.pop()
                if ftype == "enum":
                    options = {tuple(p.choices or ()) for _, p in typed}
                    if len(options) > 1:
                        raise self.fail(ask.where, f"`{name}` is a choice, and the tools that take "
                                                   "it list different options. Give it `choices:`.")
                    values = list(options.pop())
            if ftype is None and name in numbers:
                ftype = "number"       # `amount >= 1000` is only asked of a number
            if ftype is None and name in booleans:
                ftype = "boolean"      # `when: is_gift` is only asked of a yes/no
            ftype = ftype or "string"
            if ask.optional and ftype != "string":
                kind = {"number": "a number", "integer": "a whole number", "boolean": "a yes/no",
                        "enum": "a choice"}.get(ftype, ftype)
                raise self.fail(
                    ask.where,
                    f"`{name}` is marked (optional), but it is {kind}, and only a plain text "
                    "answer can be left blank. Ask a yes/no first and put `when:` on this question.",
                )
            if name in numbers and ftype not in ("number", "integer"):
                kind = {"string": "text", "boolean": "a yes/no", "enum": "a choice"}.get(ftype, ftype)
                taker = next((u.tool for u, p in typed), None)
                raise self.fail(
                    numbers[name],
                    f"compares `{name}` with a number, but `{name}` is {kind}"
                    + (f" (`{taker}` takes it that way)" if taker and ask.type is None and not ask.choices else "")
                    + f". Give its question `type: number`, or compare it with quoted text.",
                )

            def first(attr: str) -> Any:
                return next((getattr(p, attr) for _, p in takers if getattr(p, attr) is not None), None)

            question = ask.question or next((p.description for _, p in takers if p.description), None)
            if not question:
                question = "Is that right?" if ftype == "boolean" else f"What is the {_label(name)}?"
            numeric = ftype in ("number", "integer")
            specs[name] = FieldSpec(
                type=ftype, description=question, values=values if ftype == "enum" else None,
                min=first("min") if numeric else None, max=first("max") if numeric else None,
                pattern=(ask.pattern or first("pattern")) if ftype == "string" else None,
                sensitive=ask.secret or any(p.secret for _, p in takers),
                # Once asked, a question is owed an answer, on whichever branch
                # it sits. Only one that says "(optional)" may be left blank.
                required=not ask.optional,
                rules=list(self.rules.get(name, [])),
                asked_of=ask.asked_of,
            )
            if ask.repeat and specs[name].sensitive:
                raise self.fail(ask.where, f"`{name}` is a secret (its tool says so), and a secret cannot "
                                           "be one item of a list. Ask it before or after the `repeat:`.")

        # A value that decides something must be the user's own. A `when:` in
        # the file marks the answers it reads (the loader does that). When the
        # deciding is done inside a tool (`when: quote.needs_code`), the tool's
        # numbers, choices and yes/no inputs are what decide. A `check:` judges
        # an answer; it does not choose a branch, so it marks nothing.
        for use in self.uses.values():
            if use.action not in self.when_reads:
                continue
            for p in use.info.params:
                target = use.bindings.get(p.name)
                name = target.strip("{} ") if isinstance(target, str) else p.name
                spec = specs.get(name)
                if spec is not None and spec.type in ("number", "integer", "enum", "boolean") \
                        and name not in self.confirms:
                    spec.must_elicit = True
        # An answer with a declared shape (a code, an id, a date) is one the
        # user gives themselves: the model has no business inventing one.
        for name, ask in self.asks.items():
            if ask.pattern and name in specs and name not in self.confirms:
                specs[name].must_elicit = True
        # Whether there is another item decides how often the steps run, so it
        # is the user's own answer, like any other that decides something.
        for name in self.loop_answers:
            if name in specs:
                specs[name].must_elicit = True
        for name in explicit:
            specs.setdefault(name, explicit[name])
        return specs

    def flow(self) -> list[dict[str, Any]]:
        """The long-form steps, with what the flow says when the user declines."""
        steps = [s for s in self.out if "_declined" not in s]
        # The engine says it itself when the flow ENDS on the write that was
        # declined. Everywhere else it has to be a step.
        engine_says = None
        if steps:
            last = steps[-1]
            use = self.uses.get(last.get("do", ""))
            if use is not None and use.requires and last.get("when"):
                engine_says = use.requires
        out: list[dict[str, Any]] = []
        for s in self.out:
            if "_declined" not in s:
                out.append(s)
            elif s["_declined"] != engine_says:
                # `terms` already carries every yes above this one, so this line
                # is said for the confirm that was refused and no other.
                terms = tuple(s["_terms"]) + (f"{s['_declined']} == False",)
                out.append({"when": _expr(terms), "say": DECLINED})
        # Each repeat knows where it begins and ends: one item is the steps in between.
        begins: dict[str, int] = {}
        for index, s in enumerate(out):
            if "_repeat" in s:
                begins[s["_repeat"]] = index
            elif "_collect" in s:
                start = begins[s["_collect"]]
                s["begin"] = start
                out[start]["end"] = index
        return out

    def actions(self, field_specs: dict[str, FieldSpec]) -> dict[str, Callable[[dict[str, Any]], Any]]:
        order: list[str] = []
        if self.compute:
            from .declarative import topo_sort_compute

            order = topo_sort_compute(self.compute)
        return {
            # A yes, and "add another?", are the flow's business, not a tool's input.
            name: _wrap(use, self.registry, field_specs, self.compute, order, self.sink,
                        self.skill, set(self.confirms) | set(self.loop_answers), self.skill_dir,
                        set(self.lists))
            for name, use in self.uses.items()
        }


def _wrap(use: _Use, registry: ToolRegistry, field_specs: dict[str, FieldSpec],
          compute: dict[str, str], order: list[str], sink: AuditSink,
          skill_name: str, confirms: set[str] | None = None,
          skill_dir: Any = None, lists: set[str] | None = None) -> Callable[[dict[str, Any]], Any]:
    """A library tool as the action the flow dispatches.

    Reads are marked read-only; writes name the confirm that gates them, which
    is what gives them the dispatch-time gate check and the at-most-once record
    (``Context.run``). The tool itself is called the way it was written.
    """
    info = use.info
    raw = registry.raw(use.tool)
    normalized = registry.get(use.tool)
    names = list(field_specs)
    param_names = [p.name for p in info.params]
    secret_answers = {n for n in names if getattr(field_specs.get(n), "sensitive", False)}

    def is_secret(param: str) -> bool:
        """Is what this input carries a secret? By its own name, by the tool's
        say-so, or because `with:` passes a secret answer under another name."""
        declared = info.param(param)
        template = use.bindings.get(param)
        passed = template.strip("{} ") if isinstance(template, str) and _WHOLE_REF_RE.match(template) else None
        return param in secret_answers or bool(declared and declared.secret) or passed in secret_answers

    def arguments(state: dict[str, Any], *, strict: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        """``(inputs, namespace)``: what the tool would be called with now."""
        env = widget_namespace(state, compute, order, names)
        values: dict[str, Any] = {}
        if info.style in ("dict", "builtin"):
            # An older `def tool(args)` gets every answer so far. The yes that
            # released it is the flow's business, not an argument. A built-in
            # (it writes a record) is never handed a secret either.
            values = {
                n: env[n] for n in names
                if env.get(n) is not None and n not in (confirms or ())
                and not (info.style == "builtin" and getattr(field_specs.get(n), "sensitive", False))
            }
            # The lists `repeat:` collected are answers too, one record per item.
            for n in sorted(lists or ()):
                if env.get(n) is not None:
                    values[n] = env[n]
        for p in info.params:
            if p.name in use.bindings:
                continue
            value = env.get(p.name)
            if value is not None:
                values[p.name] = value
            elif p.required and strict:
                raise RuntimeError(
                    f"tool {use.tool!r} needs {p.name!r}, which has no value at this point "
                    f"in skill {skill_name!r}"
                )
        for pname, template in use.bindings.items():
            bound = resolve_bindings(template, env)
            if bound is not None or strict:
                values[pname] = bound
        return values, env

    def what_runs(state: dict[str, Any]) -> dict[str, Any]:
        """For the confirmation card, and for holding the call to it."""
        values, _ = arguments(state, strict=False)
        return {
            "tool": use.tool, "note": info.doc,
            "inputs": [(k, v, is_secret(k)) for k, v in values.items() if v is not None],
        }

    def call(state: dict[str, Any]) -> Any:
        values, env = arguments(state)
        _assert_finite_args(use.action, values)
        context = {
            "skill_name": skill_name, "action_name": use.action,
            "flow_run_id": env.get("flow_run_id"), "idempotency_key": env.get("flow_run_id"),
            "skill_dir": str(skill_dir) if skill_dir is not None else None,
        }
        result = call_tool(info, raw, normalized, values, context)
        if not info.readonly:
            sink.record({
                "skill_action": use.action,
                "tool": use.tool,
                "args": {k: ("***REDACTED***" if is_secret(k) else v) for k, v in values.items()},
                "flow_run_id": env.get("flow_run_id"),
                "idempotency_key": env.get("flow_run_id"),
                "gate": {use.requires: bool(env.get(use.requires))} if use.requires else None,
            })
        return result

    call.__name__ = use.action
    call.__doc__ = info.doc
    wrapped = (
        action(use.action, readonly=True)(call) if info.readonly
        else action(use.action, requires=use.requires)(call)
    )
    # The step runner reads these: a result may speak (`say`) or send the user
    # back to a question (`ask_again`), and an empty list re-asks its inputs.
    wrapped._flow_tool_wrapper = True  # type: ignore[attr-defined]
    wrapped._flow_what_runs = what_runs  # type: ignore[attr-defined]
    wrapped._flow_effect_inputs = [  # type: ignore[attr-defined]
        n for n in names if n in param_names or any(
            isinstance(t, str) and t.strip("{} ") == n for t in use.bindings.values()
        )
    ]
    return wrapped


def expand(frontmatter: Any, registry: ToolRegistry | None, sink: AuditSink | None = None,
           skill_dir: Any = None) -> tuple[Any, dict[str, Any]]:
    """``steps:`` -> the same frontmatter in the long form, plus its tool actions.

    Returns ``(frontmatter, actions)``: ``frontmatter`` now carries ``fields:``
    and ``flow:`` and is loaded exactly like a hand-written long-form skill.
    """
    from .declarative import default_audit

    fm = frontmatter
    if fm.flow is not None:
        raise ShortFormError(
            f"skill {fm.name!r}: has both `steps:` and `flow:`. `steps:` is the short form and "
            "`flow:` the long one; a skill uses one of them."
        )
    if fm.actions:
        raise ShortFormError(
            f"skill {fm.name!r}: `steps:` names tools directly (`- do: {next(iter(fm.actions))}`), "
            "so it has no `actions:` block. Remove it, or write the skill with `flow:`."
        )
    from .toolbox import with_builtins

    ex = _Expander(fm, with_builtins(registry), sink or default_audit, skill_dir)
    ex._scan_later(fm.steps if isinstance(fm.steps, list) else [], "")
    ex.walk(fm.steps, ())
    ex.flush_checks(final=True)     # anything still waiting reports what it is missing
    if not ex.asks and not ex.uses and not any("link" in s or "say" in s or "chat" in s for s in ex.out):
        raise ShortFormError(f"skill {fm.name!r}: `steps:` does nothing yet")

    used = {use.tool for use in ex.uses.values()} | set(ex.chat_tools)
    if fm.allowed_tools:
        from .skills import _parse_allowed_tools

        allowed = _parse_allowed_tools(fm.allowed_tools)
        extra = sorted(used - allowed)
        if extra:
            raise ShortFormError(
                f"skill {fm.name!r}: the steps use {extra}, which `allowed-tools` "
                f"({sorted(allowed)}) does not list. Add them, or drop the line: in the short "
                "form the steps themselves are the list."
            )
    specs = ex.fields()
    # A chat fixes a tool's input to an answer: only a checked value may be
    # handed to a tool that way (the same rule as anywhere else in the engine).
    from .skills import _field_unconstrained_reason

    for tool, param, where in ex.pins:
        # A pick is one of the rows a lookup returned: checked by where it came from.
        reason = None if ex.asks[param].picked else _field_unconstrained_reason(specs[param])
        if reason:
            raise ShortFormError(
                f"skill {fm.name!r}: {where}: the chat gives `{tool}` the answer `{param}`, fixed, but "
                f"`{param}` is free text. Give its question `choices:`, a `pattern:`, a list to pick "
                "from (`from:`) or a type such as `number`, so that only a checked value reaches the tool."
            )
    # Which of them only look things up is the TOOL's statement, recorded here
    # so the rest of the system (what a model may call directly, what counts as
    # a governed write) reads it from the skill like any other.
    reads = {use.tool for use in ex.uses.values() if use.info.readonly} | set(ex.chat_tools)
    expanded = fm.model_copy(update={
        "steps": None,
        "fields": specs,
        "flow": ex.flow(),
        "allowed_tools": fm.allowed_tools or (", ".join(sorted(used)) or None),
        "readonly_tools": fm.readonly_tools or (", ".join(sorted(reads)) or None),
    })
    return expanded, ex.actions(specs)


def tools_named(frontmatter: Any) -> set[str]:
    """Every name in a ``steps:`` block that could be a tool.

    Not checked against a library: a caller that knows the tool names (the
    connector manager, an editor's "where is this used?") intersects with them.
    """
    found: set[str] = set()

    def scan(steps: Any) -> None:
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            target = step.get("do")
            if isinstance(target, str):
                found.add(target.strip())
            listed = step.get("tools")
            if isinstance(listed, list):
                found.update(str(t).strip() for t in listed)
            elif isinstance(listed, str):
                found.update(t for t in listed.replace(",", " ").split() if t)
            source = step.get("from")
            if isinstance(source, str):
                found.add(source.strip("{} ").split(".", 1)[0])
            texts: list[Any] = [step.get("say"), step.get("else"), step.get("title")]
            conditions: list[Any] = [step.get("when")]
            checks = step.get("check")
            pairs = (
                list(checks.items()) if isinstance(checks, dict)
                else [p for c in checks if isinstance(c, dict) for p in c.items()]
                if isinstance(checks, list) else []
            )
            for cond, message in pairs:
                conditions.append(cond)
                texts.append(message)
            if isinstance(step.get("with"), dict):
                texts.extend(step["with"].values())
            for text in texts:
                if isinstance(text, str):
                    found.update(template_roots(text))
            for cond in conditions:
                if isinstance(cond, str):
                    try:
                        found.update(free_vars(cond))
                    except Exception:  # a bad condition is the loader's to report
                        pass
            scan(step.get("steps"))

    scan(getattr(frontmatter, "steps", None))
    return found


def explain(frontmatter: Any, registry: ToolRegistry | None) -> dict[str, Any]:
    """What a ``steps:`` skill expands to, as plain data (for a person to read)."""
    expanded, actions = expand(frontmatter, registry)
    return {
        "fields": {
            name: spec.model_dump(exclude_defaults=True) for name, spec in expanded.fields.items()
        },
        "tools": {
            name: {"changes_something": getattr(fn, "_flow_action_requires", None) is not None,
                   "needs": getattr(fn, "_flow_action_requires", None)}
            for name, fn in actions.items()
        },
        "flow": expanded.flow,
    }

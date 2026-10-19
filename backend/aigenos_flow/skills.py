"""SKILL.md parser, loader, and registry.

A skill is a folder with SKILL.md (YAML frontmatter + markdown body) and a
handlers.py (the @flow function and @action callables). Frontmatter
defines the typed schema; the markdown body is fed to the interpreter
LLM as flow context.
"""
from __future__ import annotations

import ast
import difflib
import importlib.machinery
import importlib.util
import logging
import re
import sys
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger("aigenos_flow.skills")

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .autowidget import PICK_KINDS
from .decorators import action_name, is_action, is_flow
from .declarative import (
    ActionSpec,
    AuditSink,
    ToolRegistry,
    binding_roots,
    build_declarative_flow,
    compile_effects,
    default_audit,
    template_roots,
    template_vars,
    topo_sort_compute,
    whole_var,
    dotted_refs,
)
from .engine.safe_eval import free_vars
from .decorators import (
    action_depends_on,
    action_name,
    action_readonly,
    action_requires,
    is_action,
    is_flow,
)
from .fields import FieldSpec


# ---------------------------------------------------------------------------
# Teaching errors: every rejection names the offending key and the nearest
# valid one. "Silently ignored" is the bug class this module exists to remove.
# ---------------------------------------------------------------------------


def _near(bad: Any, valid: Iterable[str]) -> str:
    """`` (did you mean 'when'?)`` when ``bad`` is a near-miss of a valid key."""
    match = difflib.get_close_matches(str(bad), sorted(valid), n=1, cutoff=0.6)
    return f" (did you mean {match[0]!r}?)" if match else ""


# ---------------------------------------------------------------------------
# Frontmatter schema
# ---------------------------------------------------------------------------


# Anthropic Skills spec: name is kebab-case only — `[a-z0-9-]+`, no spaces,
# no capitals, no underscores. Source: "The Complete Guide to Building Skills
# for Claude", Reference B and "Critical rules".
_KEBAB_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_RESERVED_NAMES = {"anthropic", "claude"}


class GuidelineSpec(BaseModel):
    """One natural-language behavior rule.

    Guidelines steer the conversational surface — the interpreter's replies
    and the chat/assist answerers follow them. They are *followed by the
    model*, not enforced by the engine: anything that must HOLD (side
    effects, gate inputs, which widget renders) belongs in typed
    ``fields:`` / ``actions:`` / ``flow:``. That split is the design:
    guidelines shape behavior; structure owns policy.

    YAML forms accepted::

        guidelines:
          - Always answer in the user's language.          # bare string
          - when: the user sounds frustrated
            then: apologize once, then offer a human handoff
            tools: [create_support_ticket]                 # read-tools only
    """

    model_config = {"extra": "forbid"}

    when: str | None = None   # natural-language condition; None = always
    then: str = Field(..., min_length=1, max_length=2000)
    tools: list[str] = Field(default_factory=list)

    @field_validator("when", "then")
    @classmethod
    def _no_xml(cls, v: str | None) -> str | None:
        # Same injection guard as description: guideline text lands in the
        # interpreter system prompt.
        if v and ("<" in v or ">" in v):
            raise ValueError("guideline text must not contain '<' or '>'")
        return v


class SkillActionSpec(ActionSpec):
    """An ``actions:`` entry exactly as a SKILL.md may write it.

    Two rules on top of the runtime :class:`ActionSpec`:

    * **Every declared action is a WRITE unless it declares ``readonly: true``.**
      A write must name its confirmation field with ``requires:``, must keep
      auditing on, and every ``do:`` step that dispatches it must be guarded by
      a ``when:`` that proves the confirmation is true. A lookup or search
      declares ``readonly: true`` and then needs no gate.
    * **Unknown keys are rejected.** ``require:`` instead of ``requires:`` used
      to be swallowed in silence, which deleted the gate — it is now a load
      error naming the nearest valid key.
    """

    model_config = {"extra": "forbid"}

    readonly: bool = False


class SkillFrontmatter(BaseModel):
    """The YAML frontmatter of a SKILL.md.

    Implements the Anthropic Agent Skills spec ("The Complete Guide to
    Building Skills for Claude") for the common fields:

      Required:  name, description
      Optional:  license, compatibility, allowed-tools, metadata

    On top of the spec, aigenos flow adds these optional fields for typed
    deterministic flows (ignored by Anthropic-spec-strict tooling):

      handlers, triggers, fields, kind

    The aigenos flow-specific keys are kept at the top level for backward
    compatibility with existing SKILL.md files. Spec-strict consumers (e.g.
    Claude.ai) will silently ignore them.
    """

    # -------- Anthropic spec: required --------
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=1024)

    # -------- Anthropic spec: optional --------
    license: str | None = Field(default=None, max_length=128)
    compatibility: str | None = Field(default=None, min_length=1, max_length=500)
    # Accept both `allowed-tools` (Anthropic key) and `allowed_tools` (Python-friendly).
    allowed_tools: str | None = Field(default=None, alias="allowed-tools")
    metadata: dict[str, Any] = Field(default_factory=dict)

    # -------- write/read declaration for conversational leaves --------
    # `chat:`, `assist:` and `guidelines:` hand tool choice to the model, so
    # they may only use tools that are PROVEN read-only. A tool is proven
    # read-only when the host descriptor says so (managed connector operations)
    # or when this line names it. Same syntax as `allowed-tools`, and it is a
    # subset of it: naming a tool here that `allowed-tools` doesn't grant is a
    # load error.
    readonly_tools: str | None = Field(default=None, alias="readonly-tools")

    # -------- aigenos flow extensions (not in Anthropic spec) --------
    handlers: str = "./handlers.py"
    triggers: list[str] = Field(default_factory=list)
    # `fields` is the canonical key; `slots` is accepted as a deprecated alias
    # for existing SKILL.md files in the wild.
    fields: dict[str, FieldSpec] = Field(default_factory=dict)
    # `workflow` marks a SKILL.md as a aigenos flow-managed flow (typed fields,
    # replay semantics, push/pop parent state on mid-flow invocation). Default
    # is True — every SKILL.md the aigenos flow loader sees is a workflow unless
    # explicitly opted out. Setting workflow: false reserves the SKILL.md for
    # plain Anthropic-spec skills (instructions only, no flow runtime).
    #
    # All workflow: true skills are routable from cold start AND mid-flow.
    # The runtime preserves parent state by default when one workflow invokes
    # another; explicit task switches use Cancel + StartFlow instead.
    workflow: bool = True

    # Optional id of a pre-authored widget from the Widgets catalog. Acts as
    # a skill-level fallback when multiple fields are pending at the same
    # interrupt and per-field widgets are not set. If unset, the integration
    # auto-generates a form from the pending fields' FieldSpecs.
    widget: str | None = None

    # -------- declarative skills (run on the same runtime, no Python) --------
    # When `flow:` is present, this is a DECLARATIVE skill: the loader
    # synthesizes the @flow from these steps and a handlers.py is optional
    # (only for an @action escape hatch). When `flow:` is absent, the skill is
    # imperative (a hand-written @flow in handlers.py) — unchanged.
    flow: list[dict[str, Any]] | None = None
    # -------- the short form --------
    # `steps:` is the same skill written as a list of four kinds of step
    # (ask / say / do / confirm) over tools from the library. It is expanded
    # into `fields:` + `flow:` before anything else looks at it (see
    # ``shortform.expand``), so it is checked and run exactly like the long form.
    steps: list[Any] | None = None
    # Pure computed values: name -> safe expression over fields/earlier computes.
    # Never memoized; recomputed each replay so gates re-arm on correction.
    compute: dict[str, str] = Field(default_factory=dict)
    # Declared actions: name -> SkillActionSpec (a host-tool reference). A write
    # unless it declares `readonly: true`.
    actions: dict[str, SkillActionSpec] = Field(default_factory=dict)
    # Mid-flow side-question answering: {tools?, pin?, system?}. While the flow
    # is waiting on a field/widget, a user message that isn't a command gets a
    # real answer (scoped tool loop, same rules as chat:) and the replay then
    # re-prompts the pending step — free conversation, deterministic return.
    assist: dict[str, Any] | None = None
    # Natural-language behavior rules (see GuidelineSpec). Injected into the
    # interpreter and chat/assist prompts; never into the deterministic policy.
    guidelines: list[GuidelineSpec] = Field(default_factory=list)

    # Allow population by either alias or python name so `allowed_tools=...`
    # works programmatically and `allowed-tools:` works in YAML.
    #
    # `extra="forbid"`: an unknown top-level key is a LOAD ERROR. `flowz:` used
    # to be ignored, which silently demoted a gated skill to prompt text and
    # handed the write tool straight to the model — the exact fail-open this
    # loader exists to prevent.
    model_config = {"populate_by_name": True, "extra": "forbid"}

    @field_validator("guidelines", mode="before")
    @classmethod
    def _bare_string_guidelines(cls, v: Any) -> Any:
        # A bare string is shorthand for an unconditional rule.
        if isinstance(v, list):
            return [{"then": item} if isinstance(item, str) else item for item in v]
        return v

    @field_validator("name")
    @classmethod
    def _name_chars(cls, v: str) -> str:
        # Anthropic spec: kebab-case only. Spec excerpt:
        #   "kebab-case only / No spaces or capitals / Should match folder name"
        # Underscores explicitly disallowed (the spec gives `notion_project_setup`
        # as a ❌ example).
        if not _KEBAB_NAME_RE.match(v):
            raise ValueError(
                f"Skill name {v!r} must be kebab-case: lowercase letters, "
                "digits, and single hyphens between segments only. "
                "Examples: 'book-meeting-room', 'list-rooms'."
            )
        if v in _RESERVED_NAMES:
            raise ValueError(f"{v!r} is a reserved Skill name (Anthropic spec)")
        if "<" in v or ">" in v:
            raise ValueError("Skill name must not contain '<' or '>' (Anthropic spec security restriction)")
        return v

    @field_validator("description")
    @classmethod
    def _description_no_xml(cls, v: str) -> str:
        # Anthropic spec security restriction: frontmatter appears in Claude's
        # system prompt; XML angle brackets could inject instructions.
        if "<" in v or ">" in v:
            raise ValueError(
                "Skill description must not contain '<' or '>' "
                "(Anthropic spec security restriction)."
            )
        return v

    @classmethod
    def _accept_slots_alias(cls, data: dict[str, Any]) -> dict[str, Any]:
        # Back-compat: `slots:` in older SKILL.md files maps to `fields:`.
        if "slots" in data and "fields" not in data:
            data = dict(data)
            data["fields"] = data.pop("slots")
        return data

    @classmethod
    def _accept_kind_alias(cls, data: dict[str, Any], *, source: str | None = None) -> dict[str, Any]:
        """Back-compat: translate legacy `kind:` field to `workflow:` boolean.

        - ``kind: workflow``  -> ``workflow: True`` (default, no-op)
        - ``kind: digression`` -> ``workflow: True`` (digressions are now
          regular workflows; preserve-parent-state is the default behavior
          for any workflow invoked mid-flow)
        - ``kind: <anything else>`` -> ``workflow: True`` + deprecation warning

        Logs a DeprecationWarning so existing SKILL.md files keep loading
        while authors migrate. Slated for removal in a future release.
        """
        if "kind" not in data:
            return data
        data = dict(data)
        kind_value = data.pop("kind")
        where = f" (in {source})" if source else ""
        warnings.warn(
            f"SKILL.md field `kind: {kind_value}` is deprecated{where}. "
            "Remove it — every aigenos flow skill is now a workflow and uses "
            "preserve-parent-state semantics when invoked mid-flow. To opt a "
            "SKILL.md out of the aigenos flow runtime entirely, use `workflow: false`.",
            DeprecationWarning,
            stacklevel=3,
        )
        # Only override if caller didn't already set `workflow:` explicitly.
        if "workflow" not in data:
            data["workflow"] = True
        return data


class Skill(BaseModel):
    """A loaded skill: frontmatter + body + the imported handlers module."""

    # Anthropic spec fields
    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: str | None = None
    # The subset of allowed_tools the SKILL.md declares read-only — the only
    # tools a conversational leaf (chat:/assist:/guidelines:) may use.
    readonly_tools: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    # aigenos flow extensions
    triggers: list[str]
    fields: dict[str, FieldSpec]
    body: str  # markdown text after frontmatter — interpreter LLM context
    path: Path
    flow_fn: Any = None  # callable(ctx) -> None — set on load
    actions: dict[str, Any] = Field(default_factory=dict)
    # Maps action name -> list of field names it depends on (None = unknown/all).
    action_deps: dict[str, list[str] | None] = Field(default_factory=dict)
    # True when the SKILL.md opts into the aigenos flow runtime. Plain Anthropic
    # skills (workflow: false) are loaded into the registry but never routed
    # to by the deterministic policy.
    workflow: bool = True
    # Optional skill-level widget id (see SkillFrontmatter.widget).
    widget: str | None = None
    # Raw tool objects (unnormalized — keep .description/.args_schema) that the
    # skill's `chat:` step or `assist:` may call. Allowlisted + registry-validated.
    chat_tools: dict[str, Any] = Field(default_factory=dict)
    # Boolean fields an action names in `requires:` — i.e. the CONFIRMATIONS a
    # side effect is gated on. They are gate inputs exactly like a `must_elicit`
    # field: a value for one of them is only valid if the USER supplied it, so
    # the interpreter's must_elicit backstop must cover them too.
    confirmation_fields: list[str] = Field(default_factory=list)
    # Fields a `pick:` step saves: an id the user chose from a list the flow
    # showed. The model can read that list (it is in the conversation's record,
    # so it can answer "who did I pick?"), and reading it must not become
    # choosing from it. Like a `must_elicit` field, a value for one of these
    # counts only when the user clicked it or typed it.
    picked_fields: list[str] = Field(default_factory=list)
    # True when the file was written as `steps:` (the short form).
    short_form: bool = False
    # The tools this skill carries itself (`scripts.py` / `scripts/`), described
    # the way the library's are. Only this skill can name them.
    own_tools: list[dict[str, Any]] = Field(default_factory=list)
    # Rules that read nothing but the answer they judge (`check: amount > 0`),
    # as ``{field: [(condition, sentence), ...]}``. They need no other state, so
    # a value can be judged while it is typed: see ``answer_problem``.
    answer_checks: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)
    # The skills this one hands off to with a `link:` step, in flow order. Known
    # at load time, so a link to a skill that is not there fails when the folder
    # loads and not in the middle of a conversation (see ``load_skills_dir``).
    links: list[str] = Field(default_factory=list)
    # Each `repeat:` of a skill written as steps: the list it collects ->
    # the yes/no answer that starts another item. The list is the user's
    # answers, item by item, so a correction keeps it (see runtime
    # ``invalidate_dependents``).
    repeats: dict[str, str] = Field(default_factory=dict)
    # Mid-flow side-question config (see SkillFrontmatter.assist).
    assist: dict[str, Any] | None = None
    # Natural-language behavior rules (see GuidelineSpec).
    guidelines: list[GuidelineSpec] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# SKILL.md parsing
# ---------------------------------------------------------------------------


_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)$",
    re.DOTALL,
)


def _frontmatter_keys() -> set[str]:
    out: set[str] = set()
    for name, info in SkillFrontmatter.model_fields.items():
        out.add(name)
        if info.alias:
            out.add(info.alias)
    out.update({"slots", "kind"})  # accepted aliases, translated before validation
    return out


def _valid_keys_at(loc: tuple[Any, ...]) -> set[str]:
    """The keys legal at a pydantic error location, for the near-miss hint."""
    if len(loc) == 1:
        return _frontmatter_keys()
    head = loc[0]
    if head == "fields":
        return set(FieldSpec.model_fields)
    if head == "actions":
        return set(SkillActionSpec.model_fields)
    if head == "guidelines":
        return set(GuidelineSpec.model_fields)
    return set()


def _where_at(loc: tuple[Any, ...]) -> str:
    """A human location for a pydantic error inside the frontmatter."""
    if len(loc) >= 2 and loc[0] in {"fields", "actions"}:
        return f"{str(loc[0])[:-1]} {loc[1]!r}"
    if len(loc) >= 2 and loc[0] == "guidelines":
        return f"guideline {loc[1] + 1 if isinstance(loc[1], int) else loc[1]}"
    return "frontmatter"


def _teach(exc: ValidationError, *, source: str | None) -> ValueError:
    """Turn a pydantic ValidationError on the frontmatter into a teaching error.

    Unknown keys are the important case: ``sensitve:`` silently dropped audit
    redaction and ``maximum:`` silently removed a cap, so the message names the
    bad key, where it sits, and the closest valid spelling.
    """
    problems: list[str] = []
    for err in exc.errors():
        loc = tuple(err.get("loc") or ())
        if err.get("type") == "extra_forbidden" and loc:
            bad = loc[-1]
            problems.append(
                f"{_where_at(loc)} has unknown key {str(bad)!r}"
                f"{_near(bad, _valid_keys_at(loc))}"
            )
        else:
            dotted = ".".join(str(p) for p in loc) or "frontmatter"
            problems.append(f"{dotted}: {err.get('msg')}")
    where = f" in {source}" if source else ""
    return ValueError(
        f"SKILL.md{where} is not loadable: " + "; ".join(problems)
    )


def parse_skill_md(text: str, *, source: str | None = None) -> tuple[SkillFrontmatter, str]:
    """Split SKILL.md text into validated frontmatter + raw body.

    ``source`` is an optional label (e.g. a path) used purely to make
    deprecation warnings traceable to the offending file.

    Unknown keys anywhere in the frontmatter (top level, a field spec, an action
    spec, a guideline) are load errors — see :class:`SkillActionSpec`.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(
            "SKILL.md must start with YAML frontmatter delimited by '---' lines"
        )
    try:
        fm_dict = yaml.safe_load(m.group("fm")) or {}
    except yaml.YAMLError as e:
        # A YAML parser's own words ("found unhashable key") help nobody. Show
        # the line, and name the two slips that cause most of these.
        mark = getattr(e, "problem_mark", None)
        lines = m.group("fm").splitlines()
        line = lines[mark.line].strip() if mark is not None and mark.line < len(lines) else ""
        hint = ""
        if "{{" in line and not re.search(r":\s*[\"'|>]", line):
            hint = (" A value that starts with {{ needs quotes: write "
                    + re.sub(r":\s*(\{\{.*)$", r': "\1"', line) + ".")
        elif line.count(": ") > 1:
            hint = " A sentence with a colon in it needs quotes around it."
        where = f" (line {mark.line + 2} of {source or 'SKILL.md'}: `{line}`)" if line else ""
        raise ValueError(
            f"SKILL.md frontmatter is not valid YAML{where}.{hint or ' ' + str(getattr(e, 'problem', e)) + '.'}"
        ) from None
    if not isinstance(fm_dict, dict):
        raise ValueError("SKILL.md frontmatter must be a YAML mapping")
    fm_dict = SkillFrontmatter._accept_slots_alias(fm_dict)
    fm_dict = SkillFrontmatter._accept_kind_alias(fm_dict, source=source)
    try:
        frontmatter = SkillFrontmatter.model_validate(fm_dict)
    except ValidationError as e:
        raise _teach(e, source=source) from None
    body = m.group("body").strip()
    return frontmatter, body


# ---------------------------------------------------------------------------
# Directory loader
# ---------------------------------------------------------------------------


class _CurrentSourceLoader(importlib.machinery.SourceFileLoader):
    """Compile a skill's Python file as it is on disk now.

    The standard loader trusts a cached ``.pyc`` whose recorded size and
    modification second match the source, so a file saved twice within one
    second at the same size would come back as the old code. These files are
    edited while the server runs, so their bytecode cache is never read or
    written.
    """

    def get_code(self, fullname: str) -> Any:
        return self.source_to_code(self.get_data(self.path), self.path)


def _import_current_source(mod_name: str, path: Path) -> Any:
    loader = _CurrentSourceLoader(mod_name, str(path))
    spec = importlib.util.spec_from_file_location(mod_name, path, loader=loader)
    if spec is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    loader.exec_module(module)
    return module


def _import_handlers(handlers_path: Path) -> Any:
    """Import handlers.py at runtime with a unique module name so multiple
    skills with the same filename don't collide in sys.modules."""
    return _import_current_source(f"_flow_handlers_{handlers_path.parent.name}", handlers_path)


def _import_module_file(path: Path, prefix: str) -> Any:
    """Import a standalone .py by path under a collision-proof module name."""
    stem = f"{path.parent.name}_{path.stem}".replace("-", "_")
    return _import_current_source(f"{prefix}_{stem}", path)


def _collect_tools(module: Any) -> list[tuple[str, Any]]:
    """A scripts module's exported tools: an explicit ``TOOLS`` dict, else every
    public module-level callable defined in that file."""
    exported = getattr(module, "TOOLS", None)
    if isinstance(exported, dict):
        return list(exported.items())
    return [
        (n, f)
        for n, f in vars(module).items()
        if callable(f)
        and not n.startswith("_")
        and getattr(f, "__module__", None) == module.__name__
    ]


def _load_skill_scripts(skill_dir: Path, base: ToolRegistry | None) -> ToolRegistry | None:
    """Anthropic-spec ``scripts/``: a skill's tools live WITH the skill.

    ``<skill>/scripts.py`` or ``<skill>/scripts/*.py`` may define plain
    callables (REST clients, MCP wrappers with their auth headers, mocks).
    They are registered for THIS skill only — a sibling skill cannot see them,
    so the ``allowed-tools`` line stays the complete audit answer.

    Trust boundary: unchanged. A skill folder could already run arbitrary code
    via ``handlers.py``; ``scripts/`` is the same operator-owned code, better
    organized.

    Precedence: explicitly injected / operator-managed tools (the ``base``
    registry — connectors, or ``FlowAgent(tools=...)``) WIN on a name
    collision, so a skill folder can never shadow a managed operation and a
    test can always inject a double. Shadowing attempts are logged.
    """
    candidates: list[Path] = []
    single = skill_dir / "scripts.py"
    folder = skill_dir / "scripts"
    if single.exists():
        candidates.append(single)
    if folder.is_dir():
        candidates.extend(sorted(p for p in folder.glob("*.py") if not p.name.startswith("_")))
    if not candidates:
        return base

    overlay = ToolRegistry()
    loaded: list[str] = []
    for path in candidates:
        module = _import_module_file(path, "_flow_scripts")
        for tname, fn in _collect_tools(module):
            overlay.register(tname, fn)
            loaded.append(tname)

    if base is not None:
        shadowed = sorted(set(loaded) & base.names())
        if shadowed:
            log.warning(
                "skill %s: scripts define %s, which are already provided by the host "
                "(managed/injected tools win) — the script versions are ignored",
                skill_dir.name, shadowed,
            )
        for tname in base.names():
            overlay.register(tname, base.raw(tname), descriptor=base.descriptor(tname))

    log.info("skill %s: loaded %d script tool(s): %s",
             skill_dir.name, len(loaded), sorted(set(loaded)))
    return overlay


def _validate_folder_layout(skill_dir: Path, frontmatter: SkillFrontmatter) -> None:
    """Enforce Anthropic Skills spec layout rules on a skill folder.

    Spec excerpts ("Critical rules"):
      - Folder name kebab-case: `notion-project-setup` ✅
        No spaces / underscores / capitals.
      - `name` field should match folder name.
      - No `README.md` inside the skill folder (use SKILL.md + references/).

    These are spec-strict requirements. We raise rather than warn because
    silent deviations defeat the point of the audit.
    """
    folder = skill_dir.name
    if not _KEBAB_NAME_RE.match(folder):
        raise ValueError(
            f"Skill folder {folder!r} must be kebab-case (Anthropic spec). "
            f"Rename {skill_dir} so the folder uses only lowercase letters, "
            "digits, and single hyphens (no underscores / capitals / spaces). "
            f"Example: rename to {folder.replace('_', '-').lower()!r}."
        )

    if folder != frontmatter.name:
        raise ValueError(
            f"Skill name {frontmatter.name!r} does not match folder name {folder!r} "
            "(Anthropic spec: 'Should match folder name'). Rename one to match the other."
        )

    readme = skill_dir / "README.md"
    if readme.exists():
        raise ValueError(
            f"Skill folder {skill_dir} contains README.md, which the Anthropic spec "
            "explicitly forbids ('No README.md' rule). Move human-facing docs to a "
            "repo-level README outside the skill folder, or to references/."
        )


def _parse_allowed_tools(value: str | None) -> set[str]:
    """``allowed-tools: a, b c`` -> {"a", "b", "c"}. Exact-member checks only."""
    if not value:
        return set()
    return {t for t in re.split(r"[,\s]+", value.strip()) if t}


# ---------------------------------------------------------------------------
# Expression verification (load time)
# ---------------------------------------------------------------------------

# The AST node set ``engine.safe_eval`` can actually evaluate. Anything outside
# it raises UnsafeExpressionError at RUNTIME — mid-conversation — which is
# exactly the failure the loader exists to move to load time. Keep in step with
# ``engine.safe_eval._eval_node``; tests/test_safe_eval_loadtime.py cross-checks
# the two.
_SAFE_EXPR_NODES: tuple[type, ...] = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    ast.Subscript,
    ast.Attribute,
    ast.Compare,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.Tuple,
    ast.List,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Is,
    ast.IsNot,
)


def _validate_expr(expr: Any, *, skill: str, where: str) -> ast.Expression:
    """Parse and whitelist-check one gate/compute expression at LOAD time.

    Returns the parsed tree. Raises a teaching ``ValueError`` for a syntax error
    (never a bare ``SyntaxError`` with no skill name) and for any construct the
    evaluator would refuse mid-conversation.
    """
    if not isinstance(expr, str) or not expr.strip():
        raise ValueError(
            f"skill {skill!r}: {where} must be a non-empty expression string (got {expr!r})"
        )
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(
            f"skill {skill!r}: {where} is not a valid expression: {expr!r} ({e.msg})"
        ) from None
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_EXPR_NODES):
            raise ValueError(
                f"skill {skill!r}: {where} uses {type(node).__name__}, which the "
                f"expression evaluator cannot run: {expr!r}. A condition may use "
                "names, literals, comparisons, and/or/not, and [key]/.key lookups "
                "— no arithmetic, calls, f-strings, or comprehensions."
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(
                f"skill {skill!r}: {where} uses dunder access {node.attr!r}: {expr!r}"
            )
    return tree


def _implied_true(node: ast.AST) -> set[str]:
    """Names guaranteed truthy whenever ``node`` evaluates truthy.

    Used to prove a ``when:`` guard really dominates the gated action it runs.
    ``confirm`` and ``confirm and amount > 0`` prove ``confirm``; ``not confirm``
    and ``confirm is not None`` prove nothing.
    """
    if isinstance(node, ast.Expression):
        return _implied_true(node.body)
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.BoolOp):
        parts = [_implied_true(v) for v in node.values]
        if not parts:
            return set()
        if isinstance(node.op, ast.And):
            return set().union(*parts)
        return set.intersection(*parts)
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
    ):
        left, right = node.left, node.comparators[0]
        if isinstance(left, ast.Name) and isinstance(right, ast.Constant) and right.value is True:
            return {left.id}
        if isinstance(right, ast.Name) and isinstance(left, ast.Constant) and left.value is True:
            return {right.id}
    return set()


# ---------------------------------------------------------------------------
# Binding verification: only CONSTRAINED values may reach a tool argument
# ---------------------------------------------------------------------------


def _field_unconstrained_reason(spec: FieldSpec) -> str | None:
    """None when this field's value is safe in a tool argument, else why not."""
    if spec.type in {"number", "integer", "boolean", "enum"}:
        return None
    if spec.type == "string":
        if spec.pattern or spec.values:
            return None
        return (
            "an unconstrained string field — add a `pattern:` or use an enum "
            "(prevents injection through a free-text value)"
        )
    return f"a {spec.type!r} field, which is unconstrained"


def _binding_unconstrained_reason(
    name: str,
    field_specs: dict[str, FieldSpec],
    compute: dict[str, str],
    _seen: frozenset[str] = frozenset(),
) -> str | None:
    """None when ``name`` provably resolves to a constrained value.

    Constraint provenance follows aliases: a ``compute:`` that is a bare name
    inherits that name's constraint, so ``payee_alias: "recipient"`` can never
    launder a free-text field into a tool argument.
    """
    if name == "flow_run_id":
        return None
    if name in field_specs:
        return _field_unconstrained_reason(field_specs[name])
    if name in compute:
        if name in _seen:
            return "part of a compute cycle"
        return _compute_unconstrained_reason(name, field_specs, compute, _seen | {name})
    return "not a declared field or compute"


def _compute_unconstrained_reason(
    name: str,
    field_specs: dict[str, FieldSpec],
    compute: dict[str, str],
    _seen: frozenset[str],
) -> str | None:
    body = ast.parse(compute[name], mode="eval").body
    if isinstance(body, ast.Constant):
        return None  # an author-written literal
    if isinstance(body, (ast.Compare, ast.BoolOp)) or (
        isinstance(body, ast.UnaryOp) and isinstance(body.op, ast.Not)
    ):
        return None  # a boolean
    if isinstance(body, ast.Name):
        return _binding_unconstrained_reason(body.id, field_specs, compute, _seen)
    return (
        f"compute {name!r}, a derived value the loader cannot prove is constrained "
        "(bind a field with a `pattern:`/enum, or a boolean/numeric compute)"
    )


def _check_pinned_field(
    argname: str,
    field: str,
    spec: FieldSpec,
    *,
    skill: str,
    where: str,
) -> None:
    """A ``pin:`` is a tool argument the engine fills, so the same rule applies:
    only a constrained value may reach it.

    (The model's own arguments to a conversational tool are inherently free text
    — what bounds them is that the tool is proven read-only.)
    """
    reason = _field_unconstrained_reason(spec)
    if reason:
        raise ValueError(
            f"skill {skill!r}: {where} {argname!r} pins field {field!r}, which is {reason}. "
            "Only constrained values (pattern, enum, number, integer, boolean) may reach a "
            "tool argument."
        )


def _arg_templates(
    value: Any,
    *,
    skill: str,
    action: str,
    arg: str,
    nested: bool = False,
) -> list[str]:
    """Every ``{{...}}``-bearing string inside one action argument value.

    A binding NESTED in a dict/list is rejected: the runtime binder resolves a
    whole-arg template and a top-level interpolation only, so a ``{{name}}``
    inside a container would be handed to the tool as the literal template text.
    """
    out: list[str] = []

    def walk(v: Any, inside: bool) -> None:
        if isinstance(v, str):
            if "{{" not in v:
                return
            if inside:
                raise ValueError(
                    f"skill {skill!r}: action {action!r} arg {arg!r} puts the binding "
                    f"{v!r} inside a nested structure; the runtime does not resolve "
                    "bindings below the top level of an argument, so flatten it into "
                    "its own argument (or pass the container as a fixed literal)"
                )
            out.append(v)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item, True)
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item, True)

    walk(value, nested)
    return out


def _managed_descriptor(registry: ToolRegistry, tool_name: str) -> Any | None:
    descriptor = registry.descriptor(tool_name)
    return descriptor if descriptor is not None and getattr(descriptor, "operation_id", None) else None


# ---------------------------------------------------------------------------
# The flow-step vocabulary — closed, so a typo is a load error
# ---------------------------------------------------------------------------

# One verb per step (`run` is the legacy alias of `do`).
_STEP_VERBS = ("ask", "say", "do", "run", "widget", "pick", "show", "check", "chat", "link")
# Guards any step may carry.
_STEP_GUARDS = frozenset({"when", "when_missing"})
# The extra keys each verb actually reads in declarative.build_declarative_flow.
# A key its verb never reads is a silent no-op, which is the whole bug class.
_STEP_MODIFIERS: dict[str, frozenset[str]] = {
    "ask": frozenset(),
    "say": frozenset(),
    # `then_say:` is what the user is told once the action has fired; it may
    # quote the action's own result ({{action.key}}).
    "do": frozenset({"then_say"}),
    "run": frozenset({"then_say"}),
    "check": frozenset({"else_say", "else_clear"}),
    "show": frozenset({"title"}),
    "widget": frozenset({"data", "save", "submit_schema", "prompt"}),
    "pick": frozenset({"save", "prompt", "as", "map", "display", "submit_schema", "widget"}),
    "chat": frozenset(),
    # `link: other-skill` hands the user to another skill and comes back.
    # `then_say:` is said when that skill did its job; `else_say:` when it did
    # not (cancelled, or the user said no), and THIS flow then stops there.
    "link": frozenset({"then_say", "else_say"}),
}
_ALL_STEP_KEYS = (
    set(_STEP_VERBS) | set(_STEP_GUARDS) | set().union(*_STEP_MODIFIERS.values())
)


# ---------------------------------------------------------------------------
# Conversational leaves (chat:/assist:/guidelines:) — reads only
# ---------------------------------------------------------------------------

# The keys a chat:/assist: block actually uses (runtime._chat_answer /
# Context.chat). Anything else is a typo, and a typo'd `tools:` would silently
# widen or narrow what the model can call.
_LEAF_KEYS = {"tools", "pin", "system", "greeting", "context"}


def _check_leaf_keys(cfg: dict[str, Any], *, skill: str, where: str) -> None:
    for key in cfg:
        if key not in _LEAF_KEYS:
            raise ValueError(
                f"skill {skill!r}: {where} has unknown key {key!r}"
                f"{_near(key, _LEAF_KEYS)}"
            )
    # `tools: search_rows` (a bare string) would iterate its characters and
    # produce a nonsense error about a tool named 's'.
    for key in ("tools", "context"):
        if key in cfg and not isinstance(cfg[key], list):
            raise ValueError(
                f"skill {skill!r}: {where} {key}: must be a list, got "
                f"{type(cfg[key]).__name__}"
            )
    if "pin" in cfg and not isinstance(cfg["pin"], dict):
        raise ValueError(
            f"skill {skill!r}: {where} pin: must map tool-arg -> field, got "
            f"{type(cfg['pin']).__name__}"
        )


def _is_readonly_tool(
    tool_name: str, registry: ToolRegistry | None, readonly: set[str]
) -> bool:
    """True when this tool is PROVEN read-only.

    Two proofs, in order: a managed connector descriptor that reports the
    operation is a read, or the SKILL.md's own ``readonly-tools:`` line. A plain
    callable that nobody declared is assumed to WRITE — that default is what
    keeps a write tool out of a conversational leaf where the model picks the
    arguments.
    """
    descriptor = _managed_descriptor(registry, tool_name) if registry is not None else None
    if descriptor is not None:
        return not descriptor.side_effecting
    return tool_name in readonly


def _check_conversational_tool(
    tool_name: str,
    *,
    skill: str,
    where: str,
    allowed: set[str],
    readonly: set[str],
    registry: ToolRegistry | None,
) -> None:
    """Allowlisted + registered + proven read-only, or a load error."""
    if tool_name not in allowed:
        raise ValueError(
            f"skill {skill!r}: {where} tool {tool_name!r} not in `allowed-tools` "
            f"{sorted(allowed)}{_near(tool_name, allowed)}"
        )
    if registry is None or not registry.has(tool_name):
        raise ValueError(
            f"skill {skill!r}: {where} tool {tool_name!r} is not registered with the "
            "host ToolRegistry — grant it via FlowAgent.from_dir(tools=...)"
        )
    if not _is_readonly_tool(tool_name, registry, readonly):
        descriptor = _managed_descriptor(registry, tool_name)
        if descriptor is not None:
            raise ValueError(
                f"skill {skill!r}: managed write tool {tool_name!r} cannot be used in "
                f"{where} — a conversational leaf lets the model choose the arguments, "
                "so a write there would be ungated. Run it from a `do:` step with "
                "`requires:` instead."
            )
        raise ValueError(
            f"skill {skill!r}: {where} tool {tool_name!r} is not declared read-only, so "
            "it may not be used where the model chooses the arguments. Add it to "
            "`readonly-tools:` if it only reads; if it writes, run it from a `do:` step "
            "with `requires:` instead."
        )


def _validate_managed_bindings(
    *,
    skill_name: str,
    usage: str,
    descriptor: Any,
    bindings: dict[str, Any],
    field_specs: dict[str, FieldSpec],
) -> None:
    """Prove a managed tool's complete typed input mapping at load time."""
    parameters = getattr(descriptor, "parameters", {}) or {}
    required = {name for name, spec in parameters.items() if getattr(spec, "required", True)}
    supplied = set(bindings)
    if required - supplied or supplied - set(parameters):
        raise ValueError(
            f"skill {skill_name!r}: {usage} managed tool {descriptor.name!r} input mismatch "
            f"(missing={sorted(required-supplied)}, extra={sorted(supplied-set(parameters))})"
        )
    compatible = {
        "string": {"string", "enum"},
        "number": {"number", "integer"},
        "integer": {"integer"},
        "boolean": {"boolean"},
        "object": set(),
        "array": set(),
    }
    for argument, binding in bindings.items():
        field = whole_var(binding) if usage.startswith("action") else binding
        expected = getattr(parameters[argument], "type", "string")
        if usage.startswith("action") and field == "flow_run_id":
            if expected != "string":
                raise ValueError(
                    f"skill {skill_name!r}: {usage} input {argument!r} binds flow_run_id, "
                    f"but the managed parameter expects {expected}"
                )
            continue
        if field not in field_specs:
            if usage.startswith("action") and whole_var(binding) is None:
                continue  # a fixed JSON literal is validated by the runtime
            raise ValueError(
                f"skill {skill_name!r}: {usage} input {argument!r} must bind a declared field"
            )
        actual = field_specs[field].type
        if actual not in compatible.get(expected, set()):
            raise ValueError(
                f"skill {skill_name!r}: {usage} input {argument!r} expects {expected}, "
                f"but field {field!r} is {actual}"
            )


def load_skill(
    skill_dir: Path,
    *,
    tool_registry: ToolRegistry | None = None,
    audit: AuditSink | None = None,
) -> Skill:
    """Load one skill directory.

    Mode is chosen per-skill, purely structurally:
      - a SKILL.md with a ``flow:`` block is DECLARATIVE — the @flow is
        synthesized from ``flow:``/``compute:``/``actions:`` and handlers.py is
        optional (only an @action escape hatch);
      - a SKILL.md with a handlers.py is IMPERATIVE — a hand-written @flow
        (unchanged behavior; existing skills load byte-for-byte);
      - a SKILL.md with neither, and no typed policy of its own, is
        INSTRUCTION-ONLY: an advisory skill (tone, escalation, house style) that
        loads as body-as-prompt and is never routed to by the deterministic
        policy. It is *not* a downgrade path: a skill that declares
        ``fields:``/``actions:``/``compute:`` but has no runnable flow is a load
        error, because loading its policy as prose would hand the policy to the
        model.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"missing SKILL.md in {skill_dir}")

    frontmatter, body = parse_skill_md(
        skill_md.read_text(encoding="utf-8"), source=str(skill_md)
    )
    _validate_folder_layout(skill_dir, frontmatter)

    # A skill's own scripts/ tools, scoped to this skill (Anthropic layout).
    shared = set(tool_registry.names()) if tool_registry is not None else set()
    tool_registry = _load_skill_scripts(skill_dir, tool_registry)
    own_tools: list[dict[str, Any]] = []
    if tool_registry is not None:
        from .toolbox import describe_tool

        for own in sorted(set(tool_registry.names()) - shared):
            try:
                described = describe_tool(own, tool_registry.raw(own), tool_registry.descriptor(own)).as_dict()
            except Exception:  # noqa: BLE001 - an odd tool is still usable; it is just not listed
                continue
            described["source"] = described["source"] or f"{skill_dir.name}/scripts"
            own_tools.append(described)

    # The short form: expand `steps:` into `fields:` + `flow:` and carry on as
    # if the long form had been written. Its tools become actions here.
    step_actions: dict[str, Any] = {}
    short_form = frontmatter.steps is not None
    if short_form:
        from .shortform import expand

        frontmatter, step_actions = expand(frontmatter, tool_registry, audit, skill_dir=skill_dir)

    allowed_set = _parse_allowed_tools(frontmatter.allowed_tools)
    readonly_set = _parse_allowed_tools(frontmatter.readonly_tools)
    ungranted = readonly_set - allowed_set
    if ungranted:
        raise ValueError(
            f"skill {frontmatter.name!r}: `readonly-tools` names {sorted(ungranted)}, "
            f"which `allowed-tools` {sorted(allowed_set)} does not grant — "
            "`readonly-tools` is a subset of `allowed-tools`"
        )

    declared_policy = [
        key
        for key, value in (
            ("fields:", frontmatter.fields),
            ("actions:", frontmatter.actions),
            ("compute:", frontmatter.compute),
        )
        if value
    ]
    has_handlers = (skill_dir / frontmatter.handlers).exists()
    workflow = frontmatter.workflow

    if not workflow:
        # `workflow: false` reserves a SKILL.md for plain Anthropic-spec use:
        # instructions only, no flow runtime. Anything that needs the runtime
        # contradicts it, and silently ignoring either half is how a gated skill
        # ends up as advisory prose.
        contradictions = (
            declared_policy
            + (["flow:"] if frontmatter.flow is not None else [])
            + ([frontmatter.handlers] if has_handlers else [])
        )
        if contradictions:
            raise ValueError(
                f"skill {frontmatter.name!r}: `workflow: false` means instruction-only "
                f"(body-as-prompt, never routed), but the skill also has "
                f"{', '.join(contradictions)}. Remove `workflow: false`, or remove the "
                "typed blocks / handlers file."
            )

    if frontmatter.flow is not None:
        try:
            flow_fn, actions, chat_tools = _load_declarative(
                skill_dir, frontmatter, tool_registry, audit, extra_actions=step_actions
            )
        except ValueError as e:
            if not short_form:
                raise
            # The loader counts steps of the long form, which this author never
            # wrote. Say so, and say how to see it.
            raise ValueError(
                f"{e}\n(Counted against the long form that `steps:` expands to; "
                "`aigenos_flow.shortform.explain` prints it.)"
            ) from None
    elif workflow and has_handlers:
        # `actions:` / `compute:` are read only by a declarative `flow:`. An
        # imperative @flow ignores them, so a whole frontmatter policy declared
        # here (allowlist, gates, arg bindings) would evaporate in silence while
        # the @flow did whatever it liked. That is refused.
        stray = [
            key
            for key, value in (("actions:", frontmatter.actions), ("compute:", frontmatter.compute))
            if value
        ]
        if stray:
            raise ValueError(
                f"skill {frontmatter.name!r}: declares {', '.join(stray)} but runs the "
                f"@flow in {frontmatter.handlers}, which never reads them — the policy "
                "would be silently ignored. Either add a declarative `flow:` block (and "
                "drop the @flow), or move that work into the @flow itself."
            )
        flow_fn, actions = _load_imperative(skill_dir, frontmatter)
        # An imperative @flow may call ctx.chat too. Only tools PROVEN read-only
        # reach that surface: a conversational leaf lets the model choose the
        # args, so a write there would be an ungated, unaudited side effect.
        chat_tools = {}
        if tool_registry is not None:
            for tname in allowed_set:
                if tool_registry.has(tname) and _is_readonly_tool(
                    tname, tool_registry, readonly_set
                ):
                    chat_tools[tname] = tool_registry.raw(tname)
    elif workflow and declared_policy:
        # NO SILENT DOWNGRADE: typed policy with nothing to run it.
        raise ValueError(
            f"skill {frontmatter.name!r}: declares {', '.join(declared_policy)} but has "
            f"no runnable flow — add a `flow:` block to SKILL.md, or a "
            f"{frontmatter.handlers} with an @flow. (A skill with a typed policy is "
            "never loaded as advisory prose: that would hand its gates to the model.)"
        )
    else:
        # Instruction-only advisory skill: body-as-prompt, not routable.
        flow_fn, actions, chat_tools = None, {}, {}
        workflow = False

    # `assist:` (mid-flow side questions) uses the same capability rules as
    # `chat:`: every tool must be allowlisted, registered, and PROVEN read-only;
    # pins must name declared fields. Validated here so every skill mode gets it.
    if frontmatter.assist is not None:
        a = frontmatter.assist
        if not isinstance(a, dict):
            raise ValueError(f"skill {frontmatter.name!r}: assist: must be a mapping")
        _check_leaf_keys(a, skill=frontmatter.name, where="assist:")
        for tname in a.get("tools") or []:
            _check_conversational_tool(
                tname,
                skill=frontmatter.name,
                where="assist:",
                allowed=allowed_set,
                readonly=readonly_set,
                registry=tool_registry,
            )
            descriptor = _managed_descriptor(tool_registry, tname)
            if descriptor is not None:
                _validate_managed_bindings(
                    skill_name=frontmatter.name,
                    usage="assist pin",
                    descriptor=descriptor,
                    bindings=a.get("pin") or {},
                    field_specs=frontmatter.fields,
                )
            chat_tools[tname] = tool_registry.raw(tname)
        for argname, fld in (a.get("pin") or {}).items():
            if fld not in frontmatter.fields:
                raise ValueError(
                    f"skill {frontmatter.name!r}: assist pin {argname!r} references "
                    f"unknown field {fld!r}{_near(fld, frontmatter.fields)}"
                )
            _check_pinned_field(
                argname, fld, frontmatter.fields[fld],
                skill=frontmatter.name, where="assist pin",
            )

    # `guidelines:` tools follow the same capability rules as chat/assist:
    # allowlisted, registered, and never a write — a guideline can steer the
    # model toward a read, but it can never carry a side effect.
    if frontmatter.guidelines:
        for gi, g in enumerate(frontmatter.guidelines):
            for tname in g.tools:
                _check_conversational_tool(
                    tname,
                    skill=frontmatter.name,
                    where=f"guideline {gi + 1}",
                    allowed=allowed_set,
                    readonly=readonly_set,
                    registry=tool_registry,
                )
                # Make the tool callable from chat/assist leaves; each leaf's
                # own `tools:` allowlist still gates what actually fires.
                chat_tools[tname] = tool_registry.raw(tname)

    # Collect per-action dependency metadata for smart cache invalidation.
    action_deps_map: dict[str, list[str] | None] = {
        aname: action_depends_on(afn) for aname, afn in actions.items()
    }

    # The confirmations this skill's side effects are gated on (see
    # Skill.confirmation_fields).
    gates = {spec.requires for spec in frontmatter.actions.values() if spec.requires}
    gates |= {action_requires(afn) for afn in actions.values() if action_requires(afn)}
    confirmation_fields = sorted(
        g for g in gates
        if frontmatter.fields.get(g) is not None and frontmatter.fields[g].type == "boolean"
    )
    # What the user chooses from a list (see Skill.picked_fields).
    picked_fields = sorted({
        name
        for step in (getattr(frontmatter, "flow", None) or [])
        if isinstance(step, dict) and "pick" in step
        for name in (step.get("save") or [])
        if name in frontmatter.fields
    })
    links = list(dict.fromkeys(
        step["link"]
        for step in (getattr(frontmatter, "flow", None) or [])
        if isinstance(step, dict) and isinstance(step.get("link"), str)
    ))
    repeats = {
        str(step["_repeat"]): str(step["more"])
        for step in (getattr(frontmatter, "flow", None) or [])
        if isinstance(step, dict) and "_repeat" in step
    }
    answer_checks: dict[str, list[tuple[str, str]]] = {}
    for field_name, field_spec in (frontmatter.fields or {}).items():
        for condition, sentence in field_spec.rules or []:
            try:
                _validate_expr(condition, skill=frontmatter.name, where=f"field {field_name!r} rules:")
                reads = free_vars(condition)
            except ValueError as e:
                raise ValueError(str(e)) from None
            if reads != {field_name}:
                raise ValueError(
                    f"skill {frontmatter.name!r}: field {field_name!r} rule `{condition}` reads "
                    f"{sorted(reads - {field_name}) or 'nothing'}. A rule on a field reads that field "
                    "and nothing else; a rule over several answers is a `check:` step."
                )
            answer_checks.setdefault(field_name, []).append((str(condition), str(sentence)))
    for step in (getattr(frontmatter, "flow", None) or []):
        if not isinstance(step, dict) or "check" not in step:
            continue
        if step.get("when") is not None or step.get("when_missing") is not None:
            continue  # only on some branches: not a property of the answer alone
        cleared = step.get("else_clear") or []
        cleared = [cleared] if isinstance(cleared, str) else list(cleared)
        try:
            reads = free_vars(str(step["check"]))
        except Exception:  # reported by the flow validation above
            continue
        if len(cleared) == 1 and reads == {cleared[0]} and cleared[0] in frontmatter.fields:
            answer_checks.setdefault(cleared[0], []).append(
                (str(step["check"]), str(step.get("else_say") or ""))
            )

    return Skill(
        name=frontmatter.name,
        description=frontmatter.description,
        license=frontmatter.license,
        compatibility=frontmatter.compatibility,
        allowed_tools=frontmatter.allowed_tools,
        metadata=frontmatter.metadata,
        triggers=frontmatter.triggers,
        fields=frontmatter.fields,
        body=body,
        path=skill_dir,
        flow_fn=flow_fn,
        actions=actions,
        action_deps=action_deps_map,
        workflow=workflow,
        widget=frontmatter.widget,
        readonly_tools=frontmatter.readonly_tools,
        chat_tools=chat_tools,
        confirmation_fields=confirmation_fields,
        picked_fields=picked_fields,
        short_form=short_form,
        own_tools=own_tools,
        answer_checks=answer_checks,
        links=links,
        repeats=repeats,
        assist=frontmatter.assist,
        guidelines=frontmatter.guidelines,
    )


def answer_problem(skill: Skill, field: str, value: Any) -> str | None:
    """Why ``value`` is not an acceptable answer for ``field``, or None.

    Type, range, choices and shape first (the field's own spec), then every
    rule of the flow that reads this answer and nothing else. The sentence is
    the one the flow itself would say, so a form that checks as the user types
    and the flow that checks on submit cannot disagree.
    """
    from .declarative import _render_path
    from .engine.safe_eval import eval_strict
    from .fields import FieldValidationError, coerce_and_validate

    spec = skill.fields.get(field)
    if spec is None:
        return None
    try:
        coerced = coerce_and_validate(spec, value)
    except FieldValidationError as e:
        return e.reason
    if coerced is None:
        return None
    for condition, sentence in skill.answer_checks.get(field, ()):
        try:
            ok = eval_strict(condition, {field: coerced})
        except Exception:
            ok = False
        if not ok:
            env = {field: coerced}
            return _render_path(sentence, env) if sentence else "That answer is not accepted."
    return None


def _load_imperative(skill_dir: Path, frontmatter: SkillFrontmatter) -> tuple[Any, dict[str, Any]]:
    """Imperative skill: import handlers.py, require exactly one @flow, collect @actions."""
    handlers_path = (skill_dir / frontmatter.handlers).resolve()
    if not handlers_path.exists():
        raise FileNotFoundError(
            f"skill {frontmatter.name!r}: handlers file not found at {handlers_path}"
        )
    module = _import_handlers(handlers_path)
    flow_fns = [v for v in vars(module).values() if is_flow(v)]
    if len(flow_fns) == 0:
        raise ValueError(
            f"skill {frontmatter.name!r}: handlers module {handlers_path.name} has no @flow function"
        )
    if len(flow_fns) > 1:
        names = [fn.__name__ for fn in flow_fns]
        raise ValueError(
            f"skill {frontmatter.name!r}: expected exactly one @flow, found {len(flow_fns)}: {names}"
        )
    actions = {action_name(v): v for v in vars(module).values() if is_action(v)}
    return flow_fns[0], actions


def _load_declarative(
    skill_dir: Path,
    frontmatter: SkillFrontmatter,
    tool_registry: ToolRegistry | None,
    audit: AuditSink | None,
    extra_actions: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Declarative skill: synthesize the @flow + compiled effects, validating
    EVERYTHING at load time so a malformed/unsafe skill fails loudly before any
    turn runs (never a runtime gate-bypass)."""
    fm = frontmatter
    name = fm.name
    field_specs = fm.fields
    steps = fm.flow or []
    compute = fm.compute
    declared = fm.actions
    registry = tool_registry or ToolRegistry()
    sink = audit or default_audit
    allowed = _parse_allowed_tools(fm.allowed_tools)
    readonly_tools = _parse_allowed_tools(fm.readonly_tools)

    # ---- reserved and colliding names -------------------------------------
    # `flow_run_id` is the engine's own key in every gate namespace; a declared
    # field of that name would be overwritten by the run uuid, so a value that
    # passed its `pattern:` would never be the value the tool receives.
    for kind, names in (("field", set(field_specs)), ("compute", set(compute))):
        if "flow_run_id" in names:
            raise ValueError(
                f"skill {name!r}: {kind} 'flow_run_id' uses a reserved name — the engine "
                "binds flow_run_id to the at-most-once run id. Rename it."
            )
    collisions = set(compute) & set(field_specs)
    if collisions:
        raise ValueError(
            f"skill {name!r}: compute {sorted(collisions)} collide with declared field "
            "name(s) — fields and computes share one gate namespace, so the collision "
            "would silently decide which value a gate reads. Rename the compute."
        )
    action_collisions = set(declared) & (set(field_specs) | set(compute))
    if action_collisions:
        raise ValueError(
            f"skill {name!r}: action name(s) {sorted(action_collisions)} collide with a "
            "declared field or compute — step conditions read all three from one "
            "namespace. Rename the action."
        )

    bound = set(field_specs) | set(compute) | {"flow_run_id"}

    def _check_bound(expr: str, where: str) -> None:
        missing = free_vars(expr) - bound
        if missing:
            raise ValueError(
                f"skill {name!r}: {where} references undeclared name(s) {sorted(missing)} "
                f"(fields {sorted(field_specs)}, compute {sorted(compute)})"
            )

    # Optional handlers.py provides an @action escape hatch ONLY (no @flow).
    py_actions: dict[str, Any] = {}
    handlers_path = skill_dir / fm.handlers
    if handlers_path.exists():
        module = _import_handlers(handlers_path.resolve())
        if any(is_flow(v) for v in vars(module).values()):
            raise ValueError(
                f"skill {name!r}: SKILL.md has a declarative `flow:` block AND a @flow in "
                f"{handlers_path.name} — ambiguous. Use one or the other."
            )
        py_actions = {action_name(v): v for v in vars(module).values() if is_action(v)}
        py_collisions = set(py_actions) & (set(field_specs) | set(compute))
        if py_collisions:
            raise ValueError(
                f"skill {name!r}: @action name(s) {sorted(py_collisions)} collide with a "
                "declared field or compute — step conditions read all three from one "
                "namespace. Rename the @action."
            )

    # The short form's tools arrive as ready-made actions (see shortform._wrap).
    # They are held to the same rules as a handlers.py @action from here on.
    py_actions = {**py_actions, **(extra_actions or {})}

    # A boolean an action names in `requires:` is a CONFIRMATION, not a
    # threshold input: the runtime refuses a value for it that the user did not
    # supply, and the action refuses to fire unless it is true. Demanding
    # `must_elicit: true` on top of that would be noise on every confirm field.
    confirmation_fields = {
        gate
        for gate in (
            [spec.requires for spec in declared.values()]
            + [action_requires(fn) for fn in py_actions.values()]
        )
        if gate
        and field_specs.get(gate) is not None
        and field_specs[gate].type == "boolean"
    }

    def _require_must_elicit(expr: str, where: str) -> None:
        """A gate input is user-only. The loader makes it so.

        Applies to EVERY gate expression — ``compute:``, a step ``when:`` and a
        step ``check:`` alike. A field such an expression reads decides what the
        flow does, so a value for it must come from the user's own words and
        never from the model.

        The loader sets the mark itself instead of making the author type it,
        so it cannot be forgotten. Writing ``must_elicit: true`` by hand still
        works, and is how a field no gate reads is made user-only.
        """
        for fld in sorted(free_vars(expr) & set(field_specs)):
            if fld in confirmation_fields:
                continue
            if not field_specs[fld].must_elicit:
                field_specs[fld].must_elicit = True

    # Fail-closed gates: every compute expression parses, is evaluable, and is
    # provably fully bound. (Step `when:`/`check:` expressions are validated in
    # the step loop below — they may additionally reference prior action results.)
    for cname, cexpr in compute.items():
        _validate_expr(cexpr, skill=name, where=f"compute {cname!r}")
        _check_bound(cexpr, f"compute {cname!r}")
        _require_must_elicit(cexpr, f"compute {cname!r}")

    # Compute ordering — a cycle or forward reference is a load error.
    order = topo_sort_compute(compute)

    # Validate declared (tool-backed) actions.
    for aname, spec in declared.items():
        if spec.tool not in allowed:
            raise ValueError(
                f"skill {name!r}: action {aname!r} uses tool {spec.tool!r} not in "
                f"`allowed-tools` {sorted(allowed)}{_near(spec.tool, allowed)}"
            )
        if not registry.has(spec.tool):
            raise ValueError(
                f"skill {name!r}: tool {spec.tool!r} (action {aname!r}) is not registered with "
                "the host ToolRegistry — grant it via FlowAgent.from_dir(tools=...)"
            )
        descriptor = _managed_descriptor(registry, spec.tool)

        # EVERY declared action is a write unless it says `readonly: true`.
        # A write must name its confirmation field and must stay audited; its
        # `do:` step is checked for the matching guard in the step loop below.
        if spec.readonly:
            if descriptor is not None and descriptor.side_effecting:
                raise ValueError(
                    f"skill {name!r}: action {aname!r} declares `readonly: true` but the host "
                    f"reports tool {spec.tool!r} is a write operation"
                )
            if spec.requires is not None:
                raise ValueError(
                    f"skill {name!r}: action {aname!r} declares `readonly: true` and "
                    f"`requires: {spec.requires}` — a read-only action has no side effect "
                    "to gate. Drop one."
                )
            if spec.idempotency_key is not None:
                raise ValueError(
                    f"skill {name!r}: action {aname!r} declares `readonly: true` and an "
                    "`idempotency_key:` — at-most-once only means something for a write. "
                    "Drop one."
                )
        else:
            if spec.requires is None:
                hint = (
                    "the host reports this operation is a read; declare `readonly: true`"
                    if descriptor is not None and not descriptor.side_effecting
                    else "declare `readonly: true` if it only reads"
                )
                raise ValueError(
                    f"skill {name!r}: action {aname!r} is a write (every action is, unless it "
                    f"declares `readonly: true`), so it must declare `requires:` naming a "
                    f"declared boolean confirmation field — {hint}"
                )
            if not spec.audit:
                raise ValueError(
                    f"skill {name!r}: write action {aname!r} cannot disable auditing "
                    "(`audit: false` is for read-only actions)"
                )

        if descriptor is not None:
            _validate_managed_bindings(
                skill_name=name,
                usage=f"action {aname!r}",
                descriptor=descriptor,
                bindings=spec.args,
                field_specs=field_specs,
            )
        if spec.requires is not None:
            rspec = field_specs.get(spec.requires)
            if rspec is None or rspec.type != "boolean":
                raise ValueError(
                    f"skill {name!r}: action {aname!r} `requires: {spec.requires}` must name a "
                    f"declared boolean field{_near(spec.requires, field_specs)}"
                )

        # Only CONSTRAINED values may reach a tool argument — whole-arg binding,
        # interpolation inside a larger string, or an aliasing compute alike.
        for argk, argv in spec.args.items():
            for tmpl in _arg_templates(argv, skill=name, action=aname, arg=argk):
                plain = template_vars(tmpl)
                dotted = binding_roots(tmpl) - plain
                if dotted:
                    raise ValueError(
                        f"skill {name!r}: action {aname!r} arg {argk!r} uses the dotted "
                        f"binding(s) {sorted(dotted)}; a tool argument binds a whole declared "
                        "field or compute ({{name}}), which the runtime resolves typed"
                    )
                for vn in plain:
                    if vn not in bound:
                        if vn in set(declared) | set(py_actions):
                            raise ValueError(
                                f"skill {name!r}: action {aname!r} arg {argk!r} binds the "
                                f"result of action {vn!r}; a tool argument binds only a "
                                "declared field or compute, so save the value into a "
                                "typed field (e.g. a widget `save:`) and bind that"
                            )
                        raise ValueError(
                            f"skill {name!r}: action {aname!r} arg {argk!r} references "
                            f"undeclared {vn!r}{_near(vn, bound)}"
                        )
                    reason = _binding_unconstrained_reason(vn, field_specs, compute)
                    if reason:
                        raise ValueError(
                            f"skill {name!r}: action {aname!r} binds {vn!r} into tool arg "
                            f"{argk!r}, but it is {reason}. Only constrained values "
                            "(pattern, enum, number, integer, boolean) may reach a tool "
                            "argument."
                        )
        if spec.idempotency_key:
            for vn in template_vars(spec.idempotency_key):
                if vn not in bound:
                    raise ValueError(
                        f"skill {name!r}: action {aname!r} idempotency_key references "
                        f"undeclared {vn!r}{_near(vn, bound)}"
                    )
            if "flow_run_id" not in template_vars(spec.idempotency_key):
                raise ValueError(
                    f"skill {name!r}: write action {aname!r} has "
                    f"idempotency_key: {spec.idempotency_key!r}, which does not include "
                    "{{flow_run_id}} — a business key widens the dedup window, so a "
                    "correction would re-fire the effect in the same run. Use "
                    '"{{flow_run_id}}" (optionally with more parts).'
                )

    dup = set(declared) & set(py_actions)
    if dup:
        raise ValueError(
            f"skill {name!r}: action name(s) {sorted(dup)} declared in BOTH `actions:` and a "
            "@action — remove one."
        )

    # A tool cannot be a read and a write in the same skill. Without this, an
    # author could declare a second, `readonly: true` action over a write tool
    # (or list it in `readonly-tools:`) and reach the same side effect with no
    # gate at all.
    write_tools = {spec.tool for spec in declared.values() if not spec.readonly}
    read_tools = {spec.tool for spec in declared.values() if spec.readonly}
    both = sorted(write_tools & read_tools)
    if both:
        raise ValueError(
            f"skill {name!r}: tool(s) {both} back BOTH a write action and a "
            "`readonly: true` action — a tool is one or the other. The read-only "
            "declaration would reach the write with no gate."
        )
    mislabelled = sorted(write_tools & readonly_tools)
    if mislabelled:
        raise ValueError(
            f"skill {name!r}: `readonly-tools` names {mislabelled}, which this skill also "
            "uses as a write action — a conversational leaf would then be able to fire it "
            "with the model's own arguments. Remove it from `readonly-tools`."
        )

    # Validate the flow steps.
    effect_names = set(declared) | set(py_actions)
    # The list each `repeat:` (in a skill written as steps) collects.
    repeat_lists = {str(s["_repeat"]) for s in steps if isinstance(s, dict) and "_repeat" in s}
    # Step conditions (when:/check:) may read fields, computes, flow_run_id AND
    # prior action results — results are deterministic tool outputs.
    step_bound = bound | effect_names | repeat_lists
    chat_tools: dict[str, Any] = {}

    def _refuse_dotted(template: Any, where: str) -> None:
        # say:/else_say: render whole {{name}} references only. A dotted path
        # was sent to the user as written, braces and all — refuse it instead.
        paths = dotted_refs(template)
        if paths:
            raise ValueError(
                f"skill {name!r}: {where} cannot read inside an action's result "
                f"({{{{{paths[0]}}}}}). A `say:` takes whole field or compute names only. "
                "To quote a result, put the sentence in `then_say:` on the `do:` step that "
                "produced it, or render the value with `show:`."
            )

    def _check_step_bound(expr: str, where: str) -> None:
        missing = free_vars(expr) - step_bound
        if missing:
            raise ValueError(
                f"skill {name!r}: {where} references undeclared name(s) {sorted(missing)} "
                f"(fields/computes {sorted(bound)}, action results {sorted(effect_names)})"
            )

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(
                f"skill {name!r}: flow step {i} must be a mapping like "
                f"`- ask: amount` (got {step!r})"
            )
        if "_display" in step:
            # Written by `say:` + `widget:` in a skill's steps: a designed widget
            # that is shown, not answered.
            for root in binding_roots(step.get("data") or {}):
                if root not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} widget data references undeclared {root!r}"
                    )
            if step.get("when") is not None:
                _validate_expr(step["when"], skill=name, where=f"flow step {i} when:")
                _check_step_bound(step["when"], f"flow step {i} when:")
            continue
        if "_repeat" in step or "_collect" in step:
            # Written by `repeat:` in a skill's steps: where one item begins,
            # and where it is collected. Its names are this skill's fields.
            for f in [step.get("more"), *(step.get("items") or [])]:
                if f not in field_specs:
                    raise ValueError(f"skill {name!r}: flow step {i} repeat: unknown field {f!r}")
            if step.get("when") is not None:
                _validate_expr(step["when"], skill=name, where=f"flow step {i} when:")
                _check_step_bound(step["when"], f"flow step {i} when:")
            continue

        # Unknown / misspelled keys FIRST: a `wehn:` ignored in silence would
        # delete the guard on the step below it.
        for key in step:
            if key not in _ALL_STEP_KEYS:
                raise ValueError(
                    f"skill {name!r}: flow step {i} has unknown key {key!r}"
                    f"{_near(key, _ALL_STEP_KEYS)}"
                )

        present = [v for v in _STEP_VERBS if v in step]
        if not present:
            raise ValueError(
                f"skill {name!r}: flow step {i} has no action — need one of "
                f"{'/'.join(_STEP_VERBS)}"
            )
        if set(present) == {"pick", "widget"}:
            verb = "pick"  # the documented pair: pick rendered by a catalog widget
        elif len(present) > 1:
            raise ValueError(
                f"skill {name!r}: flow step {i} carries {len(present)} verbs "
                f"{sorted(present)} — a step does exactly one thing and the extra one "
                "would be silently dropped. Split it into separate steps."
            )
        else:
            verb = present[0]

        if verb != "chat" and not step.get(verb):
            raise ValueError(
                f"skill {name!r}: flow step {i} {verb}: is empty — give it a value "
                f"(e.g. `{verb}: {'amount' if verb == 'ask' else 'submit_transfer'}`)"
            )

        # A key the chosen verb never reads is a silent no-op too.
        usable = set(present) | _STEP_GUARDS | _STEP_MODIFIERS[verb]
        for key in step:
            if key not in usable:
                extra = ""
                if verb == "ask" and key == "prompt":
                    extra = (
                        " — the wording of an `ask:` comes from the field's "
                        "`description:`"
                    )
                raise ValueError(
                    f"skill {name!r}: flow step {i} is an `{verb}:` step, which never "
                    f"reads {key!r}{extra}. Remove it or split the step."
                )

        wm = step.get("when_missing")
        if wm is not None:
            wm_fields = [wm] if isinstance(wm, str) else list(wm)
            for f in wm_fields:
                if f not in field_specs:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} when_missing: unknown field "
                        f"{f!r}{_near(f, field_specs)}"
                    )
        if step.get("when") is not None:
            _validate_expr(step["when"], skill=name, where=f"flow step {i} when:")
            _check_step_bound(step["when"], f"flow step {i} when:")
            _require_must_elicit(step["when"], f"flow step {i} when:")
        if "ask" in step and step["ask"] not in field_specs:
            raise ValueError(
                f"skill {name!r}: flow step {i} ask: unknown field "
                f"{step['ask']!r}{_near(step['ask'], field_specs)}"
            )

        if "widget" in step or "pick" in step:
            saves = step.get("save") or []
            if saves and not isinstance(saves, list):
                raise ValueError(
                    f"skill {name!r}: flow step {i} save: must be a list of field names, "
                    f"got {type(saves).__name__}"
                )
            # A step with BOTH keys is a pick that renders through a catalog
            # widget (`pick: "{{rows}}"` + `widget: client_picker`).
            verb = "pick" if "pick" in step else "widget"
            if not saves:
                raise ValueError(
                    f"skill {name!r}: flow step {i} {verb} must list the fields it fills in `save:`"
                )
            for f in saves:
                if f not in field_specs:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} {verb} save: unknown field {f!r}"
                    )
            src = step.get("pick") if verb == "pick" else step.get("data")
            for root in binding_roots(src or {}):
                if root not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} {verb} data references undeclared {root!r} "
                        f"(fields/computes {sorted(bound)}, action results {sorted(effect_names)})"
                    )
            if verb == "pick":
                if not isinstance(step["pick"], str):
                    raise ValueError(
                        f"skill {name!r}: flow step {i} pick: must be a single "
                        '"{{binding}}" to the rows (e.g. "{{search_clients.matches}}")'
                    )
                if step.get("as") and step.get("widget"):
                    raise ValueError(
                        f"skill {name!r}: flow step {i} pick: has both `as:` and `widget:` — "
                        "use `as:` for an auto-generated primitive OR `widget:` for a "
                        "catalog widget, not both"
                    )
                if step.get("as") and step["as"] not in PICK_KINDS:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} pick `as: {step['as']}` — "
                        f"must be one of {list(PICK_KINDS)}"
                    )

        if "show" in step:
            for root in binding_roots(step["show"]):
                if root not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} show: references undeclared {root!r}"
                    )

        if "check" in step:
            _validate_expr(step["check"], skill=name, where=f"flow step {i} check:")
            _check_step_bound(step["check"], f"flow step {i} check:")
            _require_must_elicit(step["check"], f"flow step {i} check:")
            clears = step.get("else_clear") or []
            if clears and not isinstance(clears, list):
                raise ValueError(
                    f"skill {name!r}: flow step {i} else_clear: must be a list of field "
                    f"names, got {type(clears).__name__}"
                )
            if not clears:
                raise ValueError(
                    f"skill {name!r}: flow step {i} check: needs `else_clear:` — the field(s) "
                    "to re-collect when the check fails (that's its recovery path)"
                )
            for f in clears:
                if f not in field_specs:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} check else_clear: unknown field {f!r}"
                    )
            for vn in template_roots(step.get("else_say") or ""):
                if vn not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} check else_say: references undeclared {vn!r}"
                    )

        if step.get("then_say") is not None:
            if not isinstance(step["then_say"], str) or not step["then_say"].strip():
                raise ValueError(
                    f"skill {name!r}: flow step {i} then_say: must be a non-empty sentence"
                )
            for root in template_roots(step["then_say"]):
                if root not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} then_say: references undeclared "
                        f"{root!r} (fields/computes {sorted(bound)}, "
                        f"action results {sorted(effect_names)})"
                    )

        if "chat" in step:
            if i != len(steps) - 1:
                raise ValueError(
                    f"skill {name!r}: flow step {i} chat: must be the LAST step — the flow "
                    "stays in open conversation until the user switches or cancels"
                )
            cfg = step["chat"] or {}
            if not isinstance(cfg, dict):
                raise ValueError(f"skill {name!r}: chat: must be a mapping (tools/pin/greeting)")
            _check_leaf_keys(cfg, skill=name, where=f"flow step {i} chat:")
            for tname in cfg.get("tools") or []:
                _check_conversational_tool(
                    tname,
                    skill=name,
                    where="chat:",
                    allowed=allowed,
                    readonly=readonly_tools,
                    registry=registry,
                )
                descriptor = _managed_descriptor(registry, tname)
                if descriptor is not None:
                    _validate_managed_bindings(
                        skill_name=name,
                        usage="chat pin",
                        descriptor=descriptor,
                        bindings=cfg.get("pin") or {},
                        field_specs=field_specs,
                    )
                chat_tools[tname] = registry.raw(tname)
            pin = cfg.get("pin") or {}
            if not isinstance(pin, dict):
                raise ValueError(f"skill {name!r}: chat pin: must map tool-arg -> field")
            # A field a `pick:` step fills holds one of the rows a lookup returned.
            picked = {f for st in steps if isinstance(st, dict) and "pick" in st for f in (st.get("save") or [])}
            for argname, fld in pin.items():
                if fld not in field_specs:
                    raise ValueError(
                        f"skill {name!r}: chat pin {argname!r} references unknown field "
                        f"{fld!r}{_near(fld, field_specs)}"
                    )
                if fld in picked:
                    continue
                _check_pinned_field(
                    argname, fld, field_specs[fld], skill=name, where="chat pin",
                )
            for cname in cfg.get("context") or []:
                if cname not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: chat context {cname!r} is not a declared field, "
                        f"compute, or action result"
                    )

        if "link" in step:
            target = step["link"]
            if not isinstance(target, str) or not _KEBAB_NAME_RE.match(target):
                raise ValueError(
                    f"skill {name!r}: flow step {i} link: must be the name of another skill "
                    f"(like `link: add-payee`), got {target!r}"
                )
            if target == name:
                raise ValueError(
                    f"skill {name!r}: flow step {i} link: names this skill itself. A flow "
                    "cannot hand off to itself; to go round again, end the flow and let the "
                    "user start it."
                )
            if step.get("else_say") is not None:
                if not isinstance(step["else_say"], str) or not step["else_say"].strip():
                    raise ValueError(
                        f"skill {name!r}: flow step {i} link else_say: must be a non-empty sentence"
                    )
                for vn in template_vars(step["else_say"]):
                    if vn not in step_bound:
                        raise ValueError(
                            f"skill {name!r}: flow step {i} link else_say: references "
                            f"undeclared {vn!r}"
                        )
                _refuse_dotted(step["else_say"], f"flow step {i} link else_say:")

        if "say" in step:
            # A say may quote an answer ({{amount}}) or reach into the result of
            # an action that has run ({{quote.total}}).
            for vn in template_roots(step["say"]):
                if vn not in step_bound:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} say: references undeclared {vn!r}"
                        f"{_near(vn, step_bound)}"
                    )
        if verb in {"do", "run"}:
            do_name = step.get("do") or step.get("run")
            if do_name not in effect_names:
                raise ValueError(
                    f"skill {name!r}: flow step {i} {verb}: unknown action {do_name!r}"
                    f"{_near(do_name, effect_names)}"
                )
            spec = declared.get(do_name)
            if spec is not None:
                is_write, gate = (not spec.readonly), spec.requires
                kind = "action"
            else:
                # A handlers.py @action is held to the SAME rules: a write needs
                # its confirmation field and a guard. An @action that says
                # nothing is a write (fail-closed default).
                fn = py_actions[do_name]
                is_write, gate = (not action_readonly(fn)), action_requires(fn)
                kind = "@action"
                if is_write and gate is None:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} runs @action {do_name!r}, which is a "
                        "write (every @action is, unless it declares "
                        "`@action(readonly=True)`), so it must declare "
                        '`@action(requires="<boolean field>")` and be guarded by a '
                        "matching `when:`"
                    )
                if gate is not None:
                    gspec = field_specs.get(gate)
                    if gspec is None or gspec.type != "boolean":
                        raise ValueError(
                            f"skill {name!r}: @action {do_name!r} declares "
                            f"requires={gate!r}, which must name a declared boolean field"
                            f"{_near(gate, field_specs)}"
                        )
            if is_write:
                whens = step.get("when")
                if not whens:
                    raise ValueError(
                        f"skill {name!r}: flow step {i} runs gated {kind} {do_name!r} but is "
                        f'not guarded — add `when: "{gate}"` to the step'
                    )
                guard_tree = _validate_expr(
                    whens, skill=name, where=f"flow step {i} when:"
                )
                if gate not in _implied_true(guard_tree):
                    raise ValueError(
                        f"skill {name!r}: flow step {i} runs gated {kind} {do_name!r} under "
                        f"`when: {whens!r}`, which does not prove {gate!r} is true (a "
                        "negated test, or `is not None`, lets the step run after the user "
                        f'declines). Use `when: "{gate}"`.'
                    )

    declared_effects = compile_effects(
        declared, registry, field_specs, compute, order, sink, skill_name=name
    )
    all_actions = {**declared_effects, **py_actions}
    flow_fn = build_declarative_flow(steps, compute, order, all_actions, field_specs)
    return flow_fn, all_actions, chat_tools


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SkillRegistry:
    """In-process catalog of loaded skills, looked up by name. Multi-process
    deployments load the registry independently in each worker."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}
        # The tool library these skills were loaded against (host tools plus
        # the folder's own `tools/`). None for a registry built by hand.
        self.tools: Any = None
        # folder name -> why it did not load. A skill in here is NOT loaded and
        # therefore not reachable; the list exists so a host can surface it.
        self.errors: dict[str, str] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"skill {skill.name!r} already registered")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise KeyError(f"unknown skill: {name!r} (known: {sorted(self._skills)})")
        return self._skills[name]

    def has(self, name: str) -> bool:
        return name in self._skills

    def all(self) -> list[Skill]:
        return list(self._skills.values())

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __iter__(self):
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)


def _link_cycle(start: str, registry: "SkillRegistry") -> list[str] | None:
    """The chain of `link:` steps that leads from ``start`` back to itself, if any."""
    def walk(name: str, path: list[str]) -> list[str] | None:
        if not registry.has(name):
            return None
        for target in getattr(registry.get(name), "links", None) or []:
            if target == start:
                return [*path, target]
            if target not in path:
                found = walk(target, [*path, target])
                if found:
                    return found
        return None

    return walk(start, [start])


def load_skills_dir(
    root: Path | str,
    *,
    tool_registry: ToolRegistry | None = None,
    audit: AuditSink | None = None,
    strict: bool = False,
    tools_dir: Path | str | None = None,
) -> SkillRegistry:
    """Walk a directory and load every immediate subdirectory that contains a
    SKILL.md as a skill. Returns a populated registry.

    ``tool_registry`` grants the host tools that declarative ``actions:`` may
    reference; ``audit`` is the sink declarative side effects record to. Both
    default to empty/in-process, so imperative skills load with no extra wiring.

    One unloadable skill does not take the folder down: it is logged by name,
    recorded in ``registry.errors``, and SKIPPED — so it is unreachable, while
    its siblings still load. (Skipping is fail-closed: a rejected skill can
    never run.) Pass ``strict=True`` to re-raise the first failure instead, and
    note that a folder where NOTHING loaded is always an error.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        raise FileNotFoundError(f"skills directory not found: {root_path}")

    # The tool library: `<root>/tools/*.py` (and `tools.py`), plus `tools_dir`
    # when given. Every skill in the folder can name these; nobody has to pass
    # them in from the code that runs the agent.
    from .toolbox import load_library

    tool_registry = load_library(root_path, tool_registry, tools_dir)

    registry = SkillRegistry()
    registry.tools = tool_registry
    for sub in sorted(root_path.iterdir()):
        if not (sub.is_dir() and (sub / "SKILL.md").exists()):
            continue
        try:
            registry.register(load_skill(sub, tool_registry=tool_registry, audit=audit))
        except Exception as e:
            if strict:
                raise
            registry.errors[sub.name] = f"{type(e).__name__}: {e}"
            log.error(
                "skill %r in %s did NOT load and is unavailable: %s: %s",
                sub.name, root_path, type(e).__name__, e,
            )

    # A `link:` names another skill. With the whole folder in, check that each
    # one is there and has a flow to run. A hand-off to a skill that is missing
    # can only fail in the middle of a conversation, so the skill that links to
    # it does not load either. Repeated until nothing changes: unloading one
    # skill can leave another linking to nothing.
    changed = True
    while changed:
        changed = False
        for skill in list(registry.all()):
            for target in getattr(skill, "links", None) or []:
                if registry.has(target) and registry.get(target).flow_fn is not None:
                    continue
                if registry.has(target):
                    why = "is an instruction-only skill with no flow to run"
                elif target in registry.errors:
                    why = "did not load"
                else:
                    why = f"is not a skill in this folder{_near(target, [s.name for s in registry.all()])}"
                problem = f"skill {skill.name!r}: `link: {target}` names a skill that {why}"
                if strict:
                    raise ValueError(problem)
                registry._skills.pop(skill.name, None)  # noqa: SLF001
                folder = skill.path.name if getattr(skill, "path", None) else skill.name
                registry.errors[folder] = f"ValueError: {problem}"
                log.error(
                    "skill %r in %s did NOT load and is unavailable: %s",
                    skill.name, root_path, problem,
                )
                changed = True
                break
            else:
                # Every target is there. A circle of links is the one loop the
                # skill file could otherwise express: each lap starts the same
                # flow again inside itself and the stack only grows.
                cycle = _link_cycle(skill.name, registry)
                if cycle:
                    problem = (
                        f"skill {skill.name!r}: its `link:` steps lead back to itself "
                        f"({' -> '.join(cycle)}). A flow cannot start itself again from inside "
                        "itself; let it end, and the flow that linked to it carries on."
                    )
                    if strict:
                        raise ValueError(problem)
                    registry._skills.pop(skill.name, None)  # noqa: SLF001
                    folder = skill.path.name if getattr(skill, "path", None) else skill.name
                    registry.errors[folder] = f"ValueError: {problem}"
                    log.error(
                        "skill %r in %s did NOT load and is unavailable: %s",
                        skill.name, root_path, problem,
                    )
                    changed = True

    if registry.errors and len(registry) == 0:
        raise ValueError(
            f"no skill in {root_path} could be loaded: "
            + "; ".join(f"{k} ({v})" for k, v in sorted(registry.errors.items()))
        )
    return registry

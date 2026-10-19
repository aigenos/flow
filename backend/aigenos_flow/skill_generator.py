"""LLM-powered skill generator — turn natural language into a SKILL.md.

The "skill-creator" lets a user describe a chatbot workflow in plain English
and get back a validated SKILL.md that the engine can load immediately: a list
of steps, with no Python. This is the meta-demo: a skill that creates skills.

The generator constrains the LLM to a fixed JSON schema (not free-form text),
then writes the file from it. The model is never trusted to write the skill
itself — we extract structured fields and assemble the steps ourselves, so the
output always loads.

Usage::

    from aigenos_flow.skill_generator import generate_skill_files

    files = generate_skill_files(
        description="Help users file expense reports under $500",
        llm=my_chat_model,
    )
    # files = {"name": "...", "SKILL.md": "...", "spec": {...}}

Or write straight to disk + reload an existing registry::

    from aigenos_flow.skill_generator import create_skill_in_dir

    skill = create_skill_in_dir(
        description="...",
        llm=my_chat_model,
        skills_dir=Path("skills/"),
        registry=agent.registry,  # optional: hot-reload if provided
    )
"""
from __future__ import annotations

import json
import keyword
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from .engine.safe_eval import free_vars
from .skills import (
    SkillFrontmatter,
    SkillRegistry,
    _KEBAB_NAME_RE,
    _validate_expr,
    load_skill,
)

log = logging.getLogger("aigenos_flow.skill_generator")


# ---------------------------------------------------------------------------
# Spec the LLM must produce
# ---------------------------------------------------------------------------


class GeneratedFieldSpec(BaseModel):
    """One field in the generated skill, in the LLM's intermediate JSON form."""

    type: str = Field(..., pattern=r"^(string|integer|number|enum|boolean)$")
    # Guidance for the INTERPRETER (how to extract the value). It goes in the
    # SKILL.md body, where the user never sees it.
    description: str = Field(..., min_length=1, max_length=500)
    # What the USER is asked. It becomes the field's `description:` in the
    # frontmatter, which is the prompt the runtime shows.
    question: str | None = Field(default=None, max_length=200)
    values: list[str] | None = None  # only for enum
    min: int | float | None = None
    max: int | float | None = None
    required: bool = True
    # "Ask this only when ...": a condition over EARLIER fields, in the flow's
    # own expression language (`amount > 500`, `category == 'travel'`). It
    # becomes a `when:` on the question, so the rule is in the flow and not in
    # the prose. One the flow cannot run is dropped (see _usable_condition).
    only_when: str | None = Field(default=None, max_length=200)
    # A code, PIN or password the user holds. Becomes `sensitive: true`: never
    # taken from the model, never echoed back in the summary, never recorded.
    secret: bool = False


class GeneratedAction(BaseModel):
    """One @action stub in the generated skill."""

    name: str = Field(..., min_length=1, max_length=64)
    purpose: str = Field(..., min_length=1, max_length=300)
    # The LLM proposes a fake result for the demo — the user wires real data later.
    fake_result: dict[str, Any] = Field(default_factory=dict)
    # One short sentence said once the action has run ("Your table is booked.").
    done_message: str | None = Field(default=None, max_length=160)

    @field_validator("name")
    @classmethod
    def _snake_case(cls, v: str) -> str:
        if not re.match(r"^[a-z][a-z0-9_]*$", v):
            raise ValueError(
                f"action name {v!r} must be snake_case Python identifier"
            )
        return v


class GeneratedSkillSpec(BaseModel):
    """The structured spec the LLM produces, before we template it to files."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=20, max_length=1024)
    triggers: list[str] = Field(default_factory=list, max_length=20)
    fields: dict[str, GeneratedFieldSpec] = Field(default_factory=dict, max_length=10)
    actions: list[GeneratedAction] = Field(default_factory=list, max_length=10)
    # Free-form prose body for the LLM interpreter — appended after frontmatter.
    body_notes: str = Field(default="", max_length=2000)
    confirmation_field: str | None = None  # if set, flow asks confirm at end

    @field_validator("name")
    @classmethod
    def _kebab(cls, v: str) -> str:
        if not _KEBAB_NAME_RE.match(v):
            raise ValueError(
                f"skill name {v!r} must be kebab-case (a-z, 0-9, single hyphens)"
            )
        return v


# ---------------------------------------------------------------------------
# LLM prompting
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You generate skill specs for the aigenos flow deterministic flow engine.

A skill defines a multi-step chatbot workflow: which fields to collect from
the user, what actions to run, and what to say. The engine handles all the
state, validation, and widget rendering — you just describe the shape.

Output ONLY a single JSON object matching this schema. No prose, no markdown,
no code fences. Just the JSON.

{
  "name": "kebab-case-name",           // [a-z0-9-]+, must match folder name
  "description": "<what + when>",      // 20-1024 chars, no < or > characters
  "triggers": ["phrase", ...],         // 3-8 phrases users would actually say
  "fields": {                          // 2-6 fields max
    "field_name": {                    // snake_case
      "type": "string|integer|number|enum|boolean",
      "question": "What the USER is asked, in plain words, ending in '?'",
      "description": "For the interpreter: how to extract the value, with examples",
      "values": ["..."],               // only for enum
      "only_when": "amount > 500",     // optional: ask this ONLY when the condition holds
      "secret": true                   // optional: a code, PIN or password
    }
  },
  "actions": [                         // exactly 1: the thing this workflow DOES
    {
      "name": "snake_case_verb",       // book_table, submit_refund, create_ticket
      "purpose": "What this action does",
      "done_message": "One short sentence said when it has run, e.g. 'Your table is booked.'",
      "fake_result": {"key": "value"}  // realistic mock return
    }
  ],
  "body_notes": "Markdown prose appended to SKILL.md body. Extraction rules, examples, edge cases.",
  "confirmation_field": "confirm"      // optional: name of bool field for end confirmation
}

RULES:
- name: kebab-case only, no underscores ("file-expense" not "file_expense")
- description must say BOTH what it does AND when to use it (trigger phrases)
- triggers should be things real users type ("file expense", "expense report")
- 2-6 fields keeps the demo clean; don't over-engineer
- For enum fields, list 3-6 realistic values
- Actions return fake_result for the demo; user wires real backend later
- Every workflow ENDS BY DOING SOMETHING. Always give exactly one action, named
  for what it does, and always include a "confirm" boolean field with
  confirmation_field set to "confirm": the user approves before it runs.
- Collect EVERYTHING the action needs in order to be carried out, even when the
  user's description did not list it: WHICH restaurant, not only when; WHICH
  order, not only why. A booking with no place and a refund with no order are
  incomplete.
- "question" is what a person is asked: short, plain, ending in "?", with no
  instructions in it ("Which restaurant?", "For how many people?").
  "description" is for the interpreter and the user never sees it.
- A RULE IN THE DESCRIPTION IS A RULE IN THE SPEC. When something is needed only
  in some cases ("anything over $500 needs a manager's name", "only for
  international orders"), put the condition in that field's "only_when". Never
  leave it in body_notes: prose is not enforced, and the question would be
  asked every time. A condition compares EARLIER fields with == != < <= > >=,
  joined by and / or / not. Numbers are bare, text values are quoted:
    "manager_name": {"type": "string", "only_when": "amount > 500", ...}
    "visa_number":  {"type": "string", "only_when": "trip_type == 'international'", ...}
  A yes/no field is tested by its name alone:
    "leave_where":  {"type": "string", "only_when": "not someone_home", ...}
    "gift_message": {"type": "string", "only_when": "is_gift", ...}
  The fields a condition reads must come BEFORE it in "fields".
- A one-time code, PIN or password is "secret": true.

FIELD DESCRIPTION QUALITY (this is critical — bad descriptions cause the
runtime interpreter to loop forever asking the same question):

- Each field's "description" must tell the interpreter HOW to extract a value
  from free-text user input, with examples.
- For enums, name the enum values and give example phrasings that map to each.
- For numbers, give example phrasings ("$45", "forty five dollars", "5k").
- For strings, say what the value looks like and give 1-2 examples.

GOOD field descriptions (copy this style):
  "category": "Type of technical issue, one of: hardware (laptop/monitor/peripherals),
   software (apps crashing, install errors), network (wifi/vpn/connectivity),
   account (login/permissions/access), other. Map common phrasings: 'my wifi'→network,
   'outlook crashed'→software, 'can't log in'→account."

BAD field descriptions (avoid):
  "category": "Type of issue."   ← too vague, interpreter won't know mappings

EXAMPLES of good output:

Input: "Help me build a bot for handling customer refund requests"
{
  "name": "process-refund",
  "description": "Processes customer refund requests by collecting the order ID, refund amount, and reason, then submitting to the refund queue after confirmation. Use when the user says 'refund', 'I want my money back', 'return my order', or asks to undo a purchase.",
  "triggers": ["refund", "return my order", "I want my money back", "process refund", "refund request"],
  "fields": {
    "order_id": {"type": "string", "question": "What is the order number?", "description": "The order identifier from the customer's receipt (e.g. ORD-1234)."},
    "amount": {"type": "number", "question": "How much should be refunded?", "description": "Refund amount in dollars. Extract numeric value from phrases like '$45' or 'forty five dollars'."},
    "reason": {"type": "enum", "values": ["defective", "wrong_item", "changed_mind", "late_delivery", "other"], "question": "What is the reason for the refund?", "description": "Why the customer wants a refund. Map 'broken'/'doesn't work' to defective, 'not what I ordered' to wrong_item."},
    "confirm": {"type": "boolean", "question": "Submit this refund?", "description": "Customer confirms the refund details. Accept yes/y/sure/ok as true."}
  },
  "actions": [
    {"name": "submit_refund", "purpose": "Submit refund to processing queue", "done_message": "Your refund has been submitted.", "fake_result": {"refund_id": "RFD-4242", "status": "pending", "eta_days": 5}}
  ],
  "body_notes": "Extract order_id, amount, and reason from the initial message if possible. Show a summary before asking for confirmation. After submission, tell the user the refund ID and ETA.",
  "confirmation_field": "confirm"
}

Input: "Arrange a parcel pickup: the address, and whether it needs a signature. If it does, ask who will sign. Parcels over 20 kg need a loading-dock code."
{
  "name": "arrange-pickup",
  "description": "Arranges a parcel pickup by collecting the address, weight and signature details, then booking it after confirmation. Use when the user says 'arrange a pickup', 'collect my parcel' or 'schedule a courier'.",
  "triggers": ["arrange a pickup", "collect my parcel", "schedule a courier"],
  "fields": {
    "address": {"type": "string", "question": "What is the pickup address?", "description": "The street address, verbatim."},
    "weight_kg": {"type": "number", "question": "How heavy is it, in kg?", "description": "Weight in kilograms, e.g. '12 kg', 'about 30 kilos'."},
    "needs_signature": {"type": "boolean", "question": "Does it need a signature?", "description": "Yes or no."},
    "signer_name": {"type": "string", "only_when": "needs_signature", "question": "Who will sign?", "description": "A person's name."},
    "dock_code": {"type": "string", "only_when": "weight_kg > 20", "secret": true, "question": "What is the loading-dock code?", "description": "A code the user reads out."},
    "confirm": {"type": "boolean", "question": "Book this pickup?", "description": "Accept yes/y/sure/ok as true."}
  },
  "actions": [
    {"name": "book_pickup", "purpose": "Book the courier pickup", "done_message": "Your pickup is booked.", "fake_result": {"pickup_id": "PU-1001"}}
  ],
  "body_notes": "Extract the address and weight from the first message if possible.",
  "confirmation_field": "confirm"
}
(Both rules became "only_when". Neither is repeated in body_notes. A field with
no rule in the description has no "only_when".)

Now generate the spec for the user's description. Output JSON only."""


def _build_user_prompt(description: str) -> str:
    return f"Description: {description.strip()}\n\nGenerate the JSON spec."


# ---------------------------------------------------------------------------
# LLM invocation + JSON extraction
# ---------------------------------------------------------------------------


def _extract_json_block(text: str) -> str:
    """Pull the first {...} JSON object out of an LLM response.

    Small models often wrap JSON in ```json ... ``` fences, prose, or echo
    the prompt. Find the first balanced `{...}` and return it.
    """
    # Strip code fences first
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    # qwen3 thinking models prefix with <think>...</think> — strip them
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    start = text.find("{")
    if start < 0:
        raise ValueError(f"no JSON object found in LLM output: {text[:200]!r}")
    # Walk to find the matching closing brace
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if escape:
            escape = False
            continue
        if c == "\\":
            escape = True
            continue
        if c == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError(f"unterminated JSON object in LLM output: {text[start:start+200]!r}")


def _invoke_llm(llm: Any, description: str) -> str:
    """Send the system + user prompts to the LLM and return raw text."""
    # Try LangChain's BaseChatModel interface
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        result = llm.invoke([
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=_build_user_prompt(description)),
        ])
        content = getattr(result, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Some providers return list of content blocks
            parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
            return "".join(parts)
        return str(result)
    except ImportError:
        raise RuntimeError(
            "skill_generator requires langchain-core. Install it with the engine's "
            "provider extra (e.g. pip install 'aigenos-flow[ollama]')."
        )


def generate_skill_spec(description: str, llm: Any) -> GeneratedSkillSpec:
    """Ask the LLM to produce a structured skill spec and validate it.

    Raises ValueError if the LLM output can't be parsed or doesn't match
    the schema. Caller should retry or surface the error to the user.
    """
    raw = _invoke_llm(llm, description)
    log.info("[skill_generator] raw LLM output: %s", raw[:500])
    try:
        json_str = _extract_json_block(raw)
        data = json.loads(json_str)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"LLM did not return valid JSON: {exc}. Raw: {raw[:300]!r}") from exc

    try:
        spec = GeneratedSkillSpec.model_validate(data)
    except Exception as exc:
        raise ValueError(f"LLM output failed schema validation: {exc}. Data: {data}") from exc

    _normalize_confirmation_field(spec)
    return spec


def _normalize_confirmation_field(spec: GeneratedSkillSpec) -> None:
    """Guarantee the confirmation field exists and is boolean.

    ``render_handlers_py`` emits ``ctx.ask(confirmation_field)`` whenever
    ``spec.confirmation_field`` is set, but it trusts the LLM to have also
    declared that field in ``spec.fields``. Smaller models routinely set
    ``confirmation_field="confirm"`` while forgetting to add the field — the
    SKILL.md then has no FieldSpec for it, so the runtime renders a typeless
    "Please provide a value for 'confirm'" widget that can never resolve to a
    boolean. The flow re-asks forever (the confirm loop).

    Enforce the invariant here: if a confirmation field is named but missing
    (or declared with a non-boolean type), inject a proper boolean field so the
    widget renders a Yes/No control and the flow can complete. Mutates in place.
    """
    cf = spec.confirmation_field
    if not cf:
        return
    existing = spec.fields.get(cf)
    if existing is not None and existing.type == "boolean":
        return
    spec.fields[cf] = GeneratedFieldSpec(
        type="boolean",
        description=(
            "User confirms the details before the action runs. "
            "Accept yes/y/sure/ok/confirm/go ahead as true; "
            "no/n/cancel/stop as false."
        ),
        required=True,
    )


# ---------------------------------------------------------------------------
# Spec → files templating
# ---------------------------------------------------------------------------


def _yaml_quote(s: str) -> str:
    """Quote a YAML string value safely — handles the common cases.

    YAML flow scalars need quoting if they contain `:`, leading whitespace,
    leading punctuation, or look like booleans/numbers. For descriptions
    we always use double quotes with `\\"` escaping.
    """
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{escaped}"'


def _render_field_block(name: str, fs: GeneratedFieldSpec) -> str:
    lines = [f"  {name}:"]
    lines.append(f"    type: {fs.type}")
    if fs.values:
        # Inline YAML list
        values_str = ", ".join(json.dumps(v) for v in fs.values)
        lines.append(f"    values: [{values_str}]")
    if fs.min is not None:
        lines.append(f"    min: {fs.min}")
    if fs.max is not None:
        lines.append(f"    max: {fs.max}")
    if not fs.required:
        lines.append(f"    required: false")
    # Use folded scalar for long descriptions
    desc = fs.description.replace("\n", " ").strip()
    lines.append(f"    description: {_yaml_quote(desc)}")
    return "\n".join(lines)


def _render_extraction_rules(spec: GeneratedSkillSpec) -> str:
    """Build an interpreter-LLM-facing extraction guide for the SKILL.md body.

    Without this, auto-generated skills suffer the flex-mode under-extraction
    problem: the LLM emits a conversational Reply but no SetField commands,
    so the runtime sees fields as empty and re-asks the same prompt forever.

    This section explicitly tells the interpreter what SetField commands to
    emit for typical user utterances, mirroring the hand-tuned style of the
    book-meeting-room example.
    """
    non_confirm = {
        n: f for n, f in spec.fields.items() if n != spec.confirmation_field
    }
    if not non_confirm:
        return ""

    lines: list[str] = []
    lines.append("# Extraction rules")
    lines.append("")
    lines.append("On EVERY user turn, scan the message for values for any unfilled field")
    lines.append("and emit a `SetField(field=..., value=...)` for each one you can extract.")
    lines.append("Be generous — partial information is better than no information.")
    lines.append("Always emit at least one structured item; never reply with text alone")
    lines.append("when the user has given any extractable information.")
    lines.append("")
    lines.append("Field map:")
    lines.append("")
    for name, fs in non_confirm.items():
        if fs.type == "enum" and fs.values:
            opts = ", ".join(f"`{v}`" for v in fs.values)
            lines.append(f"- `{name}` ({fs.type}, one of {opts}): {fs.description}")
            lines.append(
                f"  Map common phrasings to the nearest enum value. "
                f"If the user's intent is ambiguous, ask once; do not loop."
            )
        elif fs.type in ("integer", "number"):
            lines.append(f"- `{name}` ({fs.type}): {fs.description}")
            lines.append(
                f"  Extract the numeric value from phrases like \"$45\", \"forty-five\", \"five hundred\"."
            )
        elif fs.type == "boolean":
            lines.append(f"- `{name}` ({fs.type}): {fs.description}")
            lines.append(
                f"  Map yes/y/sure/ok/confirm/go ahead → true; no/n/cancel/stop → false."
            )
        else:
            lines.append(f"- `{name}` ({fs.type}): {fs.description}")
            lines.append(
                f"  Capture the user's words verbatim (or normalized form) "
                f"as the value. Even a short phrase is acceptable."
            )

    lines.append("")
    lines.append("# Corrections")
    lines.append("")
    lines.append(
        "If the user revises an already-filled field "
        "(*\"actually make it X instead\"*), emit a `Correct(field=..., value=NEW)` "
        "— not SetField. Change only the named field; keep all others unchanged."
    )

    return "\n".join(lines)


def render_skill_md(spec: GeneratedSkillSpec) -> str:
    """Render a validated GeneratedSkillSpec into a SKILL.md file."""
    triggers_block = "\n".join(f"  - {_yaml_quote(t)}" for t in spec.triggers)
    fields_block = "\n".join(
        _render_field_block(name, fs) for name, fs in spec.fields.items()
    )

    frontmatter = [
        "---",
        f"name: {spec.name}",
        f"description: {_yaml_quote(spec.description)}",
        "license: Apache-2.0",
        f"metadata:",
        f"  author: skill-creator",
        f"  version: 1.0.0",
        f"  source: skill-creator",
        "handlers: ./handlers.py",
    ]
    if spec.triggers:
        frontmatter.append("triggers:")
        frontmatter.append(triggers_block)
    if spec.fields:
        frontmatter.append("fields:")
        frontmatter.append(fields_block)
    frontmatter.append("---")

    title = spec.name.replace("-", " ").title()
    body_parts = [f"# {title}", ""]
    if spec.body_notes:
        body_parts.append(spec.body_notes.strip())
        body_parts.append("")

    # Always-injected extraction guide — this fixes the flex-mode loop where
    # the interpreter LLM forgets to emit SetField commands.
    extraction = _render_extraction_rules(spec)
    if extraction:
        body_parts.append(extraction)
        body_parts.append("")

    body_parts.append("# Confirmation")
    if spec.confirmation_field:
        body_parts.append(
            f"After all fields are collected, the flow shows a summary "
            f"and asks for `{spec.confirmation_field}`. Map yes/y/sure/ok "
            f"to true and no/cancel to false."
        )
    else:
        body_parts.append(
            "This flow runs to completion without an explicit confirmation step."
        )

    return "\n".join(frontmatter) + "\n\n" + "\n".join(body_parts) + "\n"


def render_handlers_py(spec: GeneratedSkillSpec) -> str:
    """Render the handlers.py for a generated skill.

    The flow function asks for each field in order, runs each action, then
    optionally asks for confirmation. Actions return the spec's fake_result
    so the demo "works" immediately — the user replaces them with real
    integrations later.

    We build the file as a flat list of lines (no textwrap.dedent) so the
    indentation is unambiguous and the output is always valid Python.
    """
    flow_fn_name = spec.name.replace("-", "_")
    if not flow_fn_name.isidentifier() or keyword.iskeyword(flow_fn_name):
        flow_fn_name = "run_flow"

    # De-collide @action names from the @flow function name, the imported
    # symbols, and each other. Without this, a skill like "create-jira-ticket"
    # whose action is also "create_jira_ticket" defines the @action AFTER the
    # @flow and overwrites it in the module namespace, so the loader finds no
    # @flow function ("handlers.py has no @flow function").
    reserved = {"flow", "action", "Context", "post_to_slack", "ctx", "state", flow_fn_name}
    used = set(reserved)
    action_names: list[str] = []
    for i, a in enumerate(spec.actions):
        base = a.name if (a.name.isidentifier() and not keyword.iskeyword(a.name)) else f"action_{i + 1}"
        nm, n = base, 2
        while nm in used:
            nm, n = f"{base}_{n}", n + 1
        used.add(nm)
        action_names.append(nm)

    lines: list[str] = []

    # Module docstring + imports
    lines.append(f'"""Auto-generated handlers for the {spec.name!r} skill.')
    lines.append("")
    lines.append("Generated by aigenos_flow.skill_generator. Edit freely — re-running")
    lines.append("the generator with the same name will overwrite this file when")
    lines.append("overwrite=True; otherwise the create call fails so your edits")
    lines.append("stay safe.")
    lines.append('"""')
    lines.append("from __future__ import annotations")
    lines.append("")
    lines.append("from aigenos_flow import flow, action, Context, post_to_slack")
    lines.append("")
    lines.append("")

    # @flow function
    lines.append("@flow")
    lines.append(f"def {flow_fn_name}(ctx: Context):")
    lines.append(f'    """Generated by skill-creator from a natural-language description."""')

    field_names = list(spec.fields.keys())
    non_confirm_fields = [f for f in field_names if f != spec.confirmation_field]

    for fname in non_confirm_fields:
        lines.append(f"    {fname} = ctx.ask({fname!r})")

    for nm in action_names:
        lines.append(f"    {nm}_result = ctx.run({nm})")

    if spec.confirmation_field:
        summary_parts = ", ".join(f"{f}={{{f}}}" for f in non_confirm_fields)
        lines.append("")
        lines.append(f'    ctx.say(f"Summary: {summary_parts}")')
        lines.append(f"    {spec.confirmation_field} = ctx.ask({spec.confirmation_field!r})")
        lines.append(f"    if not {spec.confirmation_field}:")
        lines.append(f'        ctx.say("Cancelled.")')
        lines.append("        return")

    if spec.actions:
        primary = spec.actions[0]
        primary_name = action_names[0]
        result_keys = list(primary.fake_result.keys())
        if result_keys:
            first_key = result_keys[0]
            lines.append(
                f'    ctx.say(f"Done. {primary_name} result: {{{primary_name}_result[{first_key!r}]}}")'
            )
        else:
            lines.append(f'    ctx.say("Done.")')
    else:
        lines.append('    ctx.say("Done.")')

    # @action stubs
    for nm, action in zip(action_names, spec.actions):
        lines.append("")
        lines.append("")
        lines.append("@action")
        lines.append(f"def {nm}(state):")
        lines.append(f'    """{action.purpose}')
        lines.append("")
        lines.append("    Posts to Slack when SLACK_WEBHOOK_URL is set (so the demo")
        lines.append("    can produce a real side effect). Falls back to the fake_result")
        lines.append("    below so the flow still runs end-to-end with no setup.")
        lines.append("")
        lines.append("    TODO: replace with your real integration. The Slack post is a")
        lines.append("    placeholder for whatever real backend write you eventually do.")
        lines.append('    """')
        # Build a Slack post that includes every collected field as context
        title = f"{spec.name.replace('-', ' ').title()} — {action.name.replace('_', ' ')}"
        lines.append(f"    slack_info = post_to_slack(")
        lines.append(f"        {title!r},")
        lines.append(f"        fields=dict(state.get('fields', {{}})),")
        lines.append(f"    )")
        # JSON-dump the fake_result. Append slack_info so success messages
        # can show whether the webhook actually fired.
        result_dict = dict(action.fake_result)
        result_dict.setdefault("slack", "<populated at runtime>")
        result_json = json.dumps(result_dict, indent=4)
        result_lines = result_json.split("\n")
        # Render as a dict literal, replacing the placeholder with the real var
        rendered = "\n    ".join(result_lines).replace(
            '"<populated at runtime>"', "slack_info"
        )
        lines.append(f"    return {rendered}")

    lines.append("")  # trailing newline
    return "\n".join(lines)


_CONFIRM = "confirm"


def _label(name: str) -> str:
    return name.replace("_", " ").strip()


def _question_for(name: str, fs: GeneratedFieldSpec) -> str:
    """The sentence the user is asked. Never the extraction hint: a person
    should not be shown "Extract numeric values from phrases like 'party of 6'"."""
    asked = (fs.question or "").strip()
    if asked:
        return asked if asked.endswith("?") else asked.rstrip(".") + "?"
    label = _label(name)
    if fs.type == "boolean":
        return f"{label.capitalize()}?"
    if fs.type == "enum":
        return f"Which {label}?"
    return f"What is the {label}?"


def _confirm_name(spec: GeneratedSkillSpec) -> str:
    return spec.confirmation_field or _CONFIRM


def _final_action(spec: GeneratedSkillSpec) -> GeneratedAction:
    """The one thing the workflow does. A generated skill always has one: a
    flow that only collects fields and then stops is not a workflow."""
    taken = set(spec.fields) | {_confirm_name(spec)}
    if spec.actions:
        chosen = spec.actions[0]
    else:
        chosen = GeneratedAction(
            name="submit_request",
            purpose=f"Carry out the {spec.name.replace('-', ' ')} request.",
        )
    if chosen.name in taken:  # an action may not share a name with a field
        chosen = chosen.model_copy(update={"name": f"{chosen.name}_action"})
    return chosen


_JSON_WORD_RE = re.compile(r"""('[^']*'|"[^"]*")|\b(true|false|null)\b""")


def _usable_condition(expr: str | None, known: list[str], spec: GeneratedSkillSpec) -> str | None:
    """The model's ``only_when`` as a ``when:`` the flow can run, or ``None``.

    The condition goes into the file as written, so it has to be something the
    loader accepts: the closed expression grammar, reading only fields that
    have ALWAYS been asked by the time this one is reached. Anything else is
    dropped and the question is asked every time. That is the safe side (it
    over-collects) and it is what the generator did with every condition
    before it could express one.
    """
    text = (expr or "").strip()
    if not text:
        return None
    # A model writing JSON writes `someone_home == false`. Outside quotes those
    # are the flow's True / False / None; inside quotes they are text and stay.
    text = _JSON_WORD_RE.sub(
        lambda m: m.group(1) or {"true": "True", "false": "False", "null": "None"}[m.group(2)], text
    )
    try:
        _validate_expr(text, skill=spec.name, where="only_when")
        names = free_vars(text)
    except Exception:  # noqa: BLE001 - not an expression the flow can run
        log.info("[skill_generator] dropping condition %r: not a runnable expression", text)
        return None
    if not names or not names <= set(known):
        log.info("[skill_generator] dropping condition %r: reads %s, known %s", text, sorted(names), known)
        return None
    return text


# ---------------------------------------------------------------------------
# The short form: what a generated skill is written as
# ---------------------------------------------------------------------------

_TYPE_WORD = {"number": "number", "integer": "whole number", "boolean": "yes/no"}
_PLAIN_WORD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
#: Words YAML reads as something other than text.
_YAML_WORDS = {"yes", "no", "on", "off", "true", "false", "null", "y", "n"}


def _q(text: Any) -> str:
    """A double-quoted YAML scalar (a JSON string is one)."""
    return json.dumps(str(text), ensure_ascii=False)


def _choice(value: Any) -> str:
    text = str(value)
    plain = _PLAIN_WORD_RE.match(text) and text.lower() not in _YAML_WORDS
    return text if plain else _q(text)


def _plain_number(value: int | float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _bounds(name: str, fs: GeneratedFieldSpec) -> tuple[str, str] | None:
    """`min:` / `max:` as a rule on the answer, with the sentence to say."""
    if fs.type not in ("number", "integer") or (fs.min is None and fs.max is None):
        return None
    label = _label(name).capitalize()
    low = _plain_number(fs.min) if fs.min is not None else None
    high = _plain_number(fs.max) if fs.max is not None else None
    if low is not None and high is not None:
        return f"{name} >= {low} and {name} <= {high}", f"{label} must be between {low} and {high}."
    if low is not None:
        return f"{name} >= {low}", f"{label} must be at least {low}."
    return f"{name} <= {high}", f"{label} must be at most {high}."


def render_steps_skill_md(spec: GeneratedSkillSpec) -> str:
    """Render a COMPLETE skill as a list of steps: ask, sum up, confirm, do,
    and say what happened.

    The whole skill is this one file. Its last action is the built-in
    ``save_request``, which records the request and returns a reference, so a
    brand-new skill completes without any Python on disk. The place to put the
    real call is obvious (name another tool in that one ``do:`` line), and
    nothing else in the skill changes when you do.
    """
    confirm = _confirm_name(spec)
    final = _final_action(spec)
    collected = [n for n in spec.fields if n != confirm]
    secrets = {n for n in collected if spec.fields[n].secret}

    # "Ask this only when ...". A condition may read the answers that are always
    # asked before it, and nothing else: those are known when the guard runs.
    conditions: dict[str, str] = {}
    always: list[str] = []
    for n in collected:
        condition = _usable_condition(spec.fields[n].only_when, always, spec)
        if condition:
            conditions[n] = condition
        elif n not in secrets:
            always.append(n)

    def blank_ok(n: str) -> bool:
        fs = spec.fields[n]
        return not fs.required and fs.type == "string" and not fs.secret and not fs.values

    lines: list[str] = []
    for n in collected:
        fs = spec.fields[n]
        question = _question_for(n, fs)
        if blank_ok(n) and "(optional)" not in question.lower():
            question = f"{question} (optional)"
        lines.append(f"  - ask: {{ {n}: {_q(question)} }}")
        if fs.type == "enum" and fs.values:
            lines.append(f"    choices: [{', '.join(_choice(v) for v in fs.values)}]")
        elif fs.secret:
            lines.append("    type: secret")
        elif fs.type in _TYPE_WORD:
            lines.append(f"    type: {_TYPE_WORD[fs.type]}")
        rule = _bounds(n, fs)
        if rule:
            lines.append(f"    check: {{ {_q(rule[0])}: {_q(rule[1])} }}")
        if n in conditions:
            lines.append(f"    when: {_q(conditions[n])}")

    # The summary names what was collected: not a code, not something that may
    # be blank, and not something that was never asked (a guarded line of its
    # own follows for each of those).
    shown = [n for n in collected if n not in conditions and n not in secrets and not blank_ok(n)]
    summary = (
        "Here's what I have — " + ", ".join(_label(n) + ": {{" + n + "}}" for n in shown) + "."
    ) if shown else "Here is what I have."
    lines.append(f"  - say: {_q(summary)}")
    for n, condition in conditions.items():
        if n not in secrets and not blank_ok(n):
            line = _label(n).capitalize() + ": {{" + n + "}}."
            lines.append("  - say: " + _q(line))
            lines.append("    when: " + _q(condition))

    asked_confirm = spec.fields.get(confirm)
    go_ahead = (
        _question_for(confirm, asked_confirm)
        if asked_confirm is not None and (asked_confirm.question or "").strip()
        else "Shall I go ahead?"
    )
    done = (final.done_message or "").strip() or "Done."
    if done[-1] not in ".!?":
        done += "."
    lines.append(f"  - confirm: {_q(go_ahead)}")
    lines.append("  - do: save_request")
    lines.append("  - say: " + _q(done + " Reference: {{save_request.reference}}."))

    head: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "license": "Apache-2.0",
        "metadata": {"author": "skill-creator", "version": "1.0.0", "source": "skill-creator"},
        "triggers": spec.triggers,
    }
    frontmatter = yaml.safe_dump(head, sort_keys=False, allow_unicode=True, width=100).strip()
    title = spec.name.replace("-", " ").title()
    body = [f"# {title}", ""]
    if spec.body_notes:
        body.extend([spec.body_notes.strip(), ""])
    extraction = _render_extraction_rules(spec)
    if extraction:
        body.extend([extraction, ""])
    body.extend([
        "# Making it do the real thing",
        "",
        "`save_request` is built in. It keeps a record of each confirmed request in "
        "`records.jsonl` next to this file and returns the reference the user is told, so "
        "the flow runs end to end as generated. To make it do the real work, add a function "
        "to the tool library (the Studio's Tools page, or a `.py` file in the `tools/` folder "
        "next to your skills) and name it in the `do:` step instead. The `confirm:` above it "
        "and the rule that it runs at most once stay exactly as they are.",
    ])
    return f"---\n{frontmatter}\nsteps:\n" + "\n".join(lines) + "\n---\n\n" + "\n".join(body) + "\n"


# ---------------------------------------------------------------------------
# Top-level: generate, write to disk, reload registry
# ---------------------------------------------------------------------------


def generate_skill_files(description: str, llm: Any) -> dict[str, str]:
    """Generate the SKILL.md for a described workflow.

    Returns ``{"name": str, "SKILL.md": str, "spec": dict}``. The skill is the
    one file: a list of steps that ends on the built-in ``save_request``. Does
    NOT write to disk — use ``create_skill_in_dir`` for that.
    """
    spec = generate_skill_spec(description, llm)
    return {
        "name": spec.name,
        "SKILL.md": render_steps_skill_md(spec),
        "spec": spec.model_dump(),
    }


def create_skill_in_dir(
    *,
    description: str,
    llm: Any,
    skills_dir: Path,
    registry: SkillRegistry | None = None,
    overwrite: bool = False,
    tool_registry: Any = None,
    generated_files: dict[str, Any] | None = None,
) -> Any:
    """Generate, write, and (optionally) hot-reload a skill.

    Args:
        description: Natural-language description of the workflow.
        llm: LangChain BaseChatModel.
        skills_dir: Parent directory containing all skill folders.
        registry: If provided, the new skill is registered into it immediately
                  so a running engine picks it up without restart.
        overwrite: If True, replace an existing folder with the same name.
        generated_files: Optional already-previewed output, so UI creation
                         writes exactly what the user reviewed without a
                         second nondeterministic LLM call.

    Returns the loaded ``Skill`` object. Raises if generation, validation,
    or disk write fails.
    """
    files = generated_files or generate_skill_files(description, llm)
    name = files["name"]
    target = skills_dir / name

    if target.exists() and not overwrite:
        raise FileExistsError(
            f"skill folder {target} already exists. Pass overwrite=True to replace it."
        )

    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(files["SKILL.md"], encoding="utf-8")
    if files.get("handlers.py"):
        (target / "handlers.py").write_text(files["handlers.py"], encoding="utf-8")
    else:
        # Replacing a skill generated by an older version: its Python file is
        # not part of the new one, and left behind it would still be imported.
        (target / "handlers.py").unlink(missing_ok=True)
    log.info("[skill_generator] wrote skill %r to %s", name, target)

    skill = load_skill(target, tool_registry=tool_registry)

    if registry is not None:
        # If a skill with this name was previously registered, allow replacement
        # so re-generation during a session works.
        if name in registry:
            registry._skills.pop(name, None)  # noqa: SLF001 — controlled internal use
        registry.register(skill)
        log.info("[skill_generator] registered %r into live registry", name)

    return skill

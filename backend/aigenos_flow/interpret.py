"""LLM interpretation: classify which skill should start, and interpret
user messages into typed items the runtime applies.

Two entrypoints: ``classify_skill`` picks a skill (or returns None) from
the user message; ``interpret_turn`` reads the user message against an
active flow and returns a ``TurnInterpretation`` of typed items
(SetField, Correct, Cancel, etc.). A post-pass (``_filter_must_elicit``)
drops field-fills that aren't grounded in the user's actual message.

The runtime never asks the LLM to decide what happens next — only what
the user said. State transitions are pure Python downstream of here.
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Annotated, Any, Literal, TypeVar, Union

from pydantic import BaseModel, Field

from .host import Host, ToolCall
from .skills import Skill


# ---------------------------------------------------------------------------
# The structured-output vocabulary of one user turn
# ---------------------------------------------------------------------------


class SetField(BaseModel):
    """User provided a value for a field."""

    type: Literal["SetField"] = "SetField"
    field: str
    value: Any


class Correct(BaseModel):
    """User changed a previously-set field. Distinct from SetField so the
    runtime can invalidate downstream action caches that depended on the
    old value."""

    type: Literal["Correct"] = "Correct"
    field: str
    value: Any


class StartFlow(BaseModel):
    """User wants to (re)start a flow. Mid-flow this signals a digression —
    the runtime pushes the current frame and runs the named flow until it
    completes, then pops back."""

    type: Literal["StartFlow"] = "StartFlow"
    flow_id: str


class Cancel(BaseModel):
    """User wants to abandon the active flow."""

    type: Literal["Cancel"] = "Cancel"


class OutOfScope(BaseModel):
    """User said something the flow can't handle. The runtime keeps the
    current field cursor; the LLM's Reply is the user-facing redirect."""

    type: Literal["OutOfScope"] = "OutOfScope"
    reason: str | None = None


class Chitchat(BaseModel):
    """Pure acknowledgement — does not emit. The LLM's Reply (if any) does."""

    type: Literal["Chitchat"] = "Chitchat"
    text: str | None = None


class Reply(BaseModel):
    """A natural-language response the user sees. First-class peer of the
    state-mutating items, not a polish layer. Cannot change field state."""

    type: Literal["Reply"] = "Reply"
    text: str


class Restart(BaseModel):
    """User wants the task in hand again from its first question. Everything
    collected for it is dropped; nothing already done is undone."""

    type: Literal["Restart"] = "Restart"


class Reopen(BaseModel):
    """User wants to answer an earlier question again and has not given the new
    answer ("let me pick a different payee"). The runtime clears that field,
    voids what depended on it, and the flow asks it again. With the new answer
    in hand it is a ``Correct``."""

    type: Literal["Reopen"] = "Reopen"
    field: str


class Resume(BaseModel):
    """User wants to go back to the task they stopped part-way. Only meaningful
    while one is paused; the runtime ignores it otherwise."""

    type: Literal["Resume"] = "Resume"


Turn = Annotated[
    Union[SetField, Correct, StartFlow, Cancel, OutOfScope, Chitchat, Reply, Restart, Reopen, Resume],
    Field(discriminator="type"),
]


class TurnInterpretation(BaseModel):
    """Wrapper for the structured-output call. One user message can yield
    several items (e.g. one Reply + two SetField + one StartFlow)."""

    items: list[Turn] = Field(default_factory=list)

    @classmethod
    def describe_for_llm(cls) -> str:
        """Hand-crafted, model-friendly description for hosts that can't
        feed Pydantic's discriminated-union schema directly to the LLM
        (older Ollama models, etc.). Concrete TypeScript-style is much
        easier for small models than $ref / $defs notation."""
        return (
            "Output a JSON object of this exact shape:\n\n"
            "  { \"items\": [ ... ] }\n\n"
            "The \"items\" array contains zero or more objects. Each MUST have\n"
            "a \"type\" field whose value is EXACTLY one of these strings:\n\n"
            "  SetField    -> { \"type\": \"SetField\",   \"field\": \"<name>\", \"value\": <value> }\n"
            "  Correct     -> { \"type\": \"Correct\",    \"field\": \"<name>\", \"value\": <new_value> }\n"
            "  StartFlow   -> { \"type\": \"StartFlow\",  \"flow_id\": \"<flow_name>\" }\n"
            "  Cancel      -> { \"type\": \"Cancel\" }\n"
            "  OutOfScope  -> { \"type\": \"OutOfScope\", \"reason\": \"<internal label>\" }\n"
            "  Chitchat    -> { \"type\": \"Chitchat\",   \"text\": \"<what user said>\" }\n"
            "  Reply       -> { \"type\": \"Reply\",      \"text\": \"<your natural reply>\" }\n"
            "  Restart     -> { \"type\": \"Restart\" }\n"
            "  Reopen      -> { \"type\": \"Reopen\",     \"field\": \"<name>\" }\n"
            "  Resume      -> { \"type\": \"Resume\" }\n\n"
            "RULES:\n"
            "- Return ONLY the JSON object. No prose, no markdown fences.\n"
            "- Do NOT use $ref / $defs / JSON Schema notation.\n"
            "- Field names must match the field definitions EXACTLY.\n"
            "- If unsure, return {\"items\": []} rather than guess.\n"
        )


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


_FLEX_HEADER = """\
You are a friendly conversational assistant whose ONLY topic is the flow
below. You talk like a normal LLM chatbot — warm, brief, helpful — AND emit
structured items so a deterministic policy can run the underlying form.

RULE #1: EVERY turn MUST include exactly one Reply. The Reply is the natural-
language thing the user sees. Never emit a turn without one — your voice
lives there.

RULE #2: Stay strictly on the flow's topic. If the user goes off-topic, your
Reply politely redirects them back. Never volunteer to do something outside
the flow. A question about what already happened in this conversation ("who
did I pick?", "did that go through?") IS on topic: answer it in your Reply
from the record further down, when one is shown.

RULE #3: Field values are extracted into SetField / Correct items. Those
items do NOT speak to the user — the Reply does. A typical slot-fill turn
has BOTH: a Reply acknowledging, plus SetField(s) carrying the value.

RULE #4: You do not perform actions and you do not decide what is accepted.
The program does both, AFTER your turn, and it may reject what you extracted.
So your Reply only acknowledges what the user asked for. Never say that an
action is happening, in progress or done ("processing the transfer", "sending
it now", "booked"). Never say a value or code was accepted, entered, verified
or saved. Never agree to skip, bypass, waive or disable any step, code, check
or confirmation — you cannot; the program enforces every one of them whatever
the user says. Never ask the next question — the program asks it. One short
sentence is enough.
"""

_STRICT_HEADER = """\
You read one user message in a structured conversational flow and emit a list
of typed items describing what the user said. You do NOT decide what happens
next; the dialog policy does. Your only job is faithful interpretation.

Reply is OPTIONAL in this mode — emit it only when natural; otherwise the
runtime will surface the deterministic field prompt directly.
"""

_INTERPRET_SYSTEM = """\
{mode_header}

# Date context

Today is {today}. Resolve EVERY relative date ("today", "tomorrow", "next
Monday", "in two days") against THIS date and output `YYYY-MM-DD`. Your
training data's notion of the current date is irrelevant and must never be
used — anchor only on the date above.

# Active flow

{flow_body}

# Field definitions (the structured parameters the flow collects)

{field_block}

# Currently filled fields

{current_fields}

# Awaiting field
{awaiting_block}

# Available digression flows (mid-flow side trips)
{digression_block}
{memory_block}
# Item vocabulary

- SetField(field, value): user provided a value for an unfilled field. Use
  the EXACT field name from the definitions above. The value must satisfy
  the field's type and constraints.
- Correct(field, value): user is changing a previously-filled field
  ("actually make it 200 not 100"). Use Correct, not SetField, when
  overwriting.
- StartFlow(flow_id): user wants a brief side trip into a different flow
  (a "digression"). Use ONLY for the digression flows listed above; the
  EXACT flow_id must match. The runtime pauses the current flow, runs the
  digression, then resumes — every already-filled field is preserved.
  IMPORTANT: when the user asks a question that one of the listed
  digression flows handles, you MUST emit StartFlow — do NOT answer the
  question yourself in a Reply. Answering inline skips the digression
  flow's deterministic logic.
- Cancel: user wants to abandon the current flow.
- Restart: user wants this task again from its first question ("start
  over", "let's begin again", "restart"). Everything collected is dropped
  and the first question is asked again. Not for changing one answer.
- Reopen(field): user wants to answer ONE earlier question again and has
  NOT said the new answer ("let me pick a different payee", "go back to
  the date", "change the room"). Use the EXACT name of an already-filled
  field. The flow asks that question again. If the new answer is in the
  message, use Correct instead.
- Resume: ONLY when a "Paused task" section appears above and the user
  asks to go back to it or carry on with it.
- OutOfScope(reason): classify a request that doesn't fit. `reason` is an
  INTERNAL label — never shown to the user; your Reply does the redirect.
  OutOfScope NEVER modifies fields. Already-filled fields stay filled.
- Chitchat(text): pure acknowledgement ("ok", "thanks"). Doesn't speak —
  your Reply does. Chitchat NEVER modifies fields.
- Reply(text): the natural-language thing the user sees. Brief, friendly,
  on-topic. Reply CANNOT change field state.

# Field-preservation invariant

OutOfScope and Chitchat NEVER change `fields`. If the user asks about the
weather mid-flow, every field they've already given you stays exactly as
it was. Your job is to redirect them politely in a Reply, not to reset
state.

# Worked examples (illustrative — do not copy verbatim)

User: "hi"
-> {{"items": [{{"type":"Reply","text":"Hi! Let's get your expense filed. How much was it?"}}]}}

User: "what's the weather?"
-> {{"items": [
     {{"type":"OutOfScope","reason":"weather"}},
     {{"type":"Reply","text":"I can't help with weather, but I can get this expense filed — how much was it?"}}
   ]}}

User: "$100 at Whole Foods"
-> {{"items": [
     {{"type":"Reply","text":"Got it."}},
     {{"type":"SetField","field":"amount","value":100}},
     {{"type":"SetField","field":"merchant","value":"Whole Foods"}}
   ]}}

User: "actually make it 200"
-> {{"items": [
     {{"type":"Reply","text":"Updated to $200."}},
     {{"type":"Correct","field":"amount","value":200}}
   ]}}

User: "wait, what are the options?"  (when a digression flow exists that
lists options for this domain — substitute its actual flow_id below)
-> {{"items": [
     {{"type":"Reply","text":"Sure — let me show you."}},
     {{"type":"StartFlow","flow_id":"<the listed digression flow_id>"}}
   ]}}
   (Do NOT answer the question yourself. The digression flow owns the
   answer and the runtime will resume your flow afterwards.)

User: "start over"
-> {{"items": [{{"type":"Restart"}}]}}

User: "actually let me change the merchant"  (merchant is already filled,
and the message does not say the new one)
-> {{"items": [
     {{"type":"Reply","text":"Sure."}},
     {{"type":"Reopen","field":"merchant"}}
   ]}}

# Boolean confirm/yes-no extraction

If the active field is a boolean (e.g. a `confirm` field), be GENEROUS in
mapping affirmative/negative. Map ALL of these to `true`: "yes", "y",
"yeah", "yep", "yup", "ok", "okay", "sure", "sounds good", "go ahead",
"do it", "confirmed", "proceed", "alright", "uh huh", "affirmative". Map
ALL of these to `false`: "no", "n", "nope", "nah", "cancel", "stop",
"don't", "negative", "wait", "hold on".

User: "ok"   (when awaiting boolean confirm field)
-> {{"items": [
     {{"type":"Reply","text":"Booking now."}},
     {{"type":"SetField","field":"confirm","value":true}}
   ]}}
"""

_INTERPRET_USER = """\
The user just said:

{user_message}

Interpret.
"""


# ---------------------------------------------------------------------------
# Skill classifier — replaces any regex routing.
# ---------------------------------------------------------------------------


class SkillChoice(BaseModel):
    """Structured output of the classifier. ``skill`` is the matched skill's
    name or ``None`` for ``no_match``."""

    skill: str | None = None
    reason: str = ""


_CLASSIFY_SYSTEM = """\
You are a dispatcher for a multi-skill conversational assistant. Your ONLY
job is to read the user's message and decide which skill — if any — best
matches what the user wants. You do NOT respond to the user. You do NOT
call tools. You emit one JSON object naming the skill, or null if no
skill applies.

# What counts as a skill match

A skill matches when the user's intent — action OR read-only question —
falls within the scope described by that skill. Both styles are valid:

- **Action skills** carry out a task ("book a room", "file an expense",
  "submit a ticket"). Match on imperative verbs and clear intent.
- **Read-only skills** answer a question or list information ("what
  rooms exist?", "show meeting capacities"). Match on question patterns
  whose subject the skill is built to answer.

A skill does NOT match when the user is:
- making pure conversation ("hi", "thanks", "ok"),
- asking about overall capabilities ("what can you do?"),
- asking something that no listed skill is built to answer.

# Disambiguation rules (most important first)

1. **Most specific wins.** If two skills could plausibly handle the
   message, pick the one whose description names the user's exact subject.
   Example with skills "book-meeting-room" and "list-rooms":
   - "book redwood tomorrow" → book-meeting-room (action: book)
   - "what rooms are there?" → list-rooms (question: room listing)

2. **Action verbs beat ambient noun mentions.** "book redwood" → the
   booking skill, not the room-listing skill, even though both mention
   "room". The imperative verb is the strongest signal of intent.

3. **Question patterns route to read-only skills when one matches.** If
   a skill exists whose description covers the question's subject ("show
   rooms", "list available X", "what are my Y"), pick it. Only fall back
   to null when NO skill is built to answer the question.

4. **Mention-doesn't-equal-intent.** A skill mentioning "room" in its
   description does NOT mean every message containing "room" matches it.
   Read the user's *intent*, not just their nouns.

5. **When still uncertain, return null.** Null falls through to free-form
   chat (recoverable). A wrong pick starts an unwanted skill (not
   recoverable in one turn).

# Worked examples (illustrative — adapt to whatever skills are listed)

User: "book redwood tomorrow at 3pm"
-> {{"skill": "<the booking skill>", "reason": "imperative 'book'"}}

User: "what rooms are available?"
-> {{"skill": "<the room-listing skill if one is listed>", "reason": "question covered by listing skill"}}
   (If no listing skill is in the catalog, return null.)

User: "show me my bookings"
-> {{"skill": "<the booking-listing skill if one exists>", "reason": "question covered by listing skill"}}
   (If no listing skill is in the catalog, return null.)

User: "hi" / "thanks" / "what can you do?"
-> {{"skill": null, "reason": "conversation / capability question"}}

User: "file an expense for $100"
-> {{"skill": "<the expense skill>", "reason": "imperative 'file'"}}

# Skill catalog

{catalog}

Respond with JSON only: {{"skill": "<name>" or null, "reason": "<short>"}}.
"""

_CLASSIFY_USER = """\
User said:

{user_message}

Pick one skill, or null.
"""


def classify_skill(
    *, host: Host, user_message: str, registry: Any
) -> str | None:
    """LLM-driven first-turn / between-flow routing.

    Returns the matched workflow skill's name, or ``None`` if the user's
    message doesn't start one. Always runs (deterministic *when*), the LLM
    decides *which* (semantic judgment). No regex, no keyword match.

    Every aigenos flow workflow (``workflow: true`` in SKILL.md, the default)
    is a routable candidate. The classifier picks based on the user's
    intent against the skill's description; the runtime's push/pop machinery
    handles whether it ends up running as a top-level flow or mid-flow
    detour.
    """
    all_skills = list(registry.all() if hasattr(registry, "all") else registry)
    # Plain Anthropic skills (workflow: false) are not aigenos flow-routable —
    # they're loaded into the registry but the deterministic policy never
    # picks them. Every workflow: true skill is a valid candidate.
    skills = [s for s in all_skills if getattr(s, "workflow", True)]
    if not skills:
        return None
    catalog = "\n".join(f"- {s.name}: {s.description}" for s in skills)
    system = _CLASSIFY_SYSTEM.format(catalog=catalog)
    user = _CLASSIFY_USER.format(user_message=user_message)
    try:
        choice = host.llm_structured(system=system, user=user, schema=SkillChoice)
    except Exception:
        # Classifier failures should never block the conversation — fall
        # through to ReAct so the LLM can still call activate_skill.
        return None
    if not choice or not choice.skill:
        return None
    known = {s.name for s in skills}
    return choice.skill if choice.skill in known else None


# ---------------------------------------------------------------------------
# Field block rendering
# ---------------------------------------------------------------------------


def _render_field_block(skill: Skill) -> str:
    if not skill.fields:
        return "(no fields)"
    lines: list[str] = []
    for name, spec in skill.fields.items():
        bits = [f"- {name} ({spec.type})"]
        if spec.must_elicit:
            bits.append("  [user-only — extract ONLY when user explicitly states this]")
        if spec.description:
            bits.append(f"  — {spec.description}")
        if spec.values:
            bits.append(f"  allowed: {spec.values}")
        if spec.min is not None or spec.max is not None:
            bits.append(f"  range: [{spec.min}, {spec.max}]")
        if not spec.required:
            bits.append("  (optional)")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _render_must_elicit_rule(skill: Skill) -> str:
    """Strong top-of-prompt rule for every user-only field. Empty string when
    nothing requires the rule — keeps prompts tight.

    Lists the same set the deterministic backstop enforces
    (``user_only_fields``), so the prompt asks for exactly the restraint the
    runtime will insist on. A value dropped by the backstop only ever costs a
    re-ask, but a prompt that disagrees with the enforcement wastes a turn.
    """
    names = sorted(user_only_fields(skill))
    if not names:
        return ""
    quoted = ", ".join(f"`{n}`" for n in names)
    return (
        "\nRULE #4 (user-only fields): The following fields are marked "
        f"[user-only]: {quoted}. For these fields, emit SetField ONLY when "
        "the user's message explicitly contains a value — even one that "
        "could plausibly be inferred from context (today's date, the "
        "previous message, a default in the flow body). If the user did NOT "
        "explicitly state a value for a user-only field, leave it unset and "
        "let the flow ask. Violating this is the single most common source "
        "of dialog hallucination, so be conservative.\n"
    )


# ---------------------------------------------------------------------------
# Post-hoc must_elicit guard — deterministic backstop on the prompt rule.
# ---------------------------------------------------------------------------


_DATE_HINT_RE = re.compile(
    r"\b("
    r"today|tomorrow|yesterday|tonight|"
    r"mon(day)?|tues?(day)?|wed(nesday)?|thurs?(day)?|fri(day)?|sat(urday)?|sun(day)?|"
    r"jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|jun(e)?|jul(y)?|"
    r"aug(ust)?|sep(t|tember)?|oct(ober)?|nov(ember)?|dec(ember)?|"
    r"next|last|this\s+(week|month|year)|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}[-/]\d{1,2}"
    r")\b",
    re.IGNORECASE,
)

#: "the 23rd", "on the 1st". An ordinal names a day of the month, and that is
#: checkable: it grounds a date whose day IS that number, and nothing else. As
#: a plain hint word it let "the one on the 2nd floor" ground any date.
_ORDINAL_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE)
_ISO_DAY_RE = re.compile(r"\d{4}-\d{2}-(\d{2})")
#: A field is date-shaped by a whole part of its name (date, start_date,
#: meeting_day), not by a substring: `days`, `holiday` and `update_at` are not.
_DATE_FIELD_RE = re.compile(r"(^|_)(date|day)($|_)")


#: A time of day written the way a field stores it.
_HHMM_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")
_CLOCK_12_RE = re.compile(r"\b(1[0-2]|0?[1-9])(?::([0-5]\d))?\s*([ap])\.?\s?m\b\.?", re.IGNORECASE)
_CLOCK_24_RE = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])(?!\s*[ap]\.?\s?m\b)", re.IGNORECASE)


def _message_times(msg: str) -> set[str]:
    """Every time of day the user stated, as HH:MM: "3pm", "3:30 p.m.",
    "15:00", "noon", "midnight".

    The model rewrites "3pm" to "15:00", so the value itself is not in the
    message; the time is. This only ever READS what the user wrote: a value is
    grounded when it EQUALS one of these, so a time nobody said is still dropped.
    ("3 o'clock" is not here: morning or afternoon would be the model's guess.)
    """
    out: set[str] = set()
    low = msg.lower()
    for hour, minute, half in _CLOCK_12_RE.findall(low):
        h = int(hour) % 12 + (12 if half == "p" else 0)
        out.add(f"{h:02d}:{minute or '00'}")
    for hour, minute in _CLOCK_24_RE.findall(low):
        out.add(f"{int(hour):02d}:{minute}")
    if re.search(r"\b(noon|midday)\b", low):
        out.add("12:00")
    if re.search(r"\bmidnight\b", low):
        out.add("00:00")
    return out


#: Number-shaped runs in a message, in the shapes ``fields.parse_number``
#: accepts: "5000", "5,000", "2400.50", "$3,000", "5k".
#: The k/m/b magnitude suffix must be ATTACHED to the digits and must end the
#: token. Allowing whitespace before it, or a letter after it, swallowed the
#: first letter of the next word: "1500 bucks" read as 1500 billion and
#: "500 more" as 500 million, so a plainly stated amount was dropped as
#: ungrounded (and a model-invented multiple of it would have been accepted).
#: A leading minus counts only at the start of a token, so "send -5" states -5
#: (and validation then refuses it WITH a reason) while the hyphens in
#: "2026-10-05" stay separators.
_NUM_TOKEN_RE = re.compile(
    r"(?:(?<![\w.,])-)?[0-9][0-9,]*(?:\.[0-9]+)?(?:[kKmMbB](?![A-Za-z]))?"
)
#: Word tokens for a whole-token (not substring) membership test.
_WORD_TOKEN_RE = re.compile(r"[a-z0-9]+")

_AFFIRMATIVE = frozenset(
    {
        "yes", "y", "yeah", "yep", "yup", "ok", "okay", "sure", "confirm",
        "confirmed", "confirming", "correct", "right", "approve", "approved",
        "proceed", "affirmative", "agreed", "true", "1",
        "yea", "ya", "yah", "aye", "k", "kk", "alright", "absolutely",
        "definitely", "certainly", "accept", "accepted", "lgtm",
    }
)
#: Approvals people actually type that contain no single "yes" word. Matched as
#: contiguous whole tokens. Found with a live model: "go ahead" was dropped as
#: ungrounded and the flow re-asked its confirmation forever.
_AFFIRMATIVE_PHRASES = (
    ("go", "ahead"), ("go", "for", "it"), ("do", "it"), ("sounds", "good"),
    ("looks", "good"), ("that", "works"), ("please", "do"), ("make", "it", "happen"),
    ("of", "course"), ("good", "to", "go"), ("ship", "it"), ("thats", "right"),
    ("that", "s", "right"), ("all", "good"),
    ("that", "is", "right"), ("that", "is", "correct"), ("thats", "correct"),
    ("that", "s", "correct"),
)
_NEGATIVE = frozenset(
    {
        "no", "n", "nope", "nah", "not", "dont", "don", "cancel", "stop", "hold",
        "wait", "negative", "false", "0", "decline", "declined", "reject",
        "rejected", "abort", "nevermind", "never",
    }
)


#: Affirmative words that also turn up in questions and remarks: "is that the
#: right category?", "are you sure?", "what happens after I confirm?". They are
#: consent only when they open a short reply ("sure", "confirm it", "right").
_WEAK_AFFIRMATIVE = frozenset({"right", "correct", "sure", "confirm", "confirmed", "confirming", "true", "1"})

#: Words that pause rather than refuse. They still veto a yes ("yes, wait" is
#: not consent), but they do not make a no: "hold on a second" at a last
#: confirmation must not decline it, because a declined last step ends the
#: flow and throws the plan away.
_PAUSE = frozenset({"wait", "hold"})


_UNIT_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALE_WORDS = {
    "thousand": 1_000, "grand": 1_000, "million": 1_000_000,
    "billion": 1_000_000_000,
}
_DIGITS_THEN_SCALE_RE = re.compile(
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s+(hundred|thousand|grand|million|billion)\b"
)


def _spelled_numbers(msg: str) -> list[float]:
    """Numbers the user wrote as WORDS: "five thousand", "twenty five hundred",
    "two hundred and fifty", "a grand", and "5 thousand".

    A person testing a payment flow types "send five thousand dollars" at least
    as often as "send $5000". Both are the user stating the amount, so both
    must ground it — otherwise the flow asks "how much?" right after being told.
    This only ever READS what the user wrote; it never widens what a model may
    invent, because the value still has to equal a number found here.
    """
    out: list[float] = []
    low = msg.lower()

    for digits, scale in _DIGITS_THEN_SCALE_RE.findall(low):
        try:
            base = float(digits.replace(",", ""))
        except ValueError:
            continue
        out.append(base * (100 if scale == "hundred" else _SCALE_WORDS[scale]))

    tokens = re.findall(r"[a-z]+", low.replace("-", " "))
    total = current = 0
    active = False
    last = ""  # "unit" | "tens" | "hundred" | "scale" | ""

    def flush() -> None:
        nonlocal total, current, active, last
        if active:
            out.append(float(total + current))
        total = current = 0
        active = False
        last = ""

    for i, tok in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if tok in _UNIT_WORDS:
            if last in ("unit", "tens") and not (last == "tens" and _UNIT_WORDS[tok] < 10):
                flush()  # "one two" is two numbers, "twenty five" is one
            current += _UNIT_WORDS[tok]
            active, last = True, "unit"
        elif tok in _TENS_WORDS:
            if last in ("unit", "tens"):
                flush()
            current += _TENS_WORDS[tok]
            active, last = True, "tens"
        elif tok == "hundred" and active and last in ("unit", "tens"):
            current *= 100
            last = "hundred"
        elif tok in _SCALE_WORDS and active:
            total += (current or 1) * _SCALE_WORDS[tok]
            current = 0
            last = "scale"
        elif tok == "a" and not active and (nxt == "hundred" or nxt in _SCALE_WORDS):
            current, active, last = 1, True, "unit"  # "a hundred", "a grand"
        elif tok == "and" and active and (nxt in _UNIT_WORDS or nxt in _TENS_WORDS):
            continue  # "two hundred and fifty"
        else:
            flush()
    flush()
    return out


def _message_numbers(msg: str) -> list[float]:
    """Every number the user actually stated — typed as digits or written as
    words — normalized the same way a numeric field's value is. Used for an
    EXACT match instead of a substring test."""
    from .fields import parse_number

    out: list[float] = []
    for raw in _NUM_TOKEN_RE.findall(msg):
        try:
            out.append(parse_number(raw.strip()))
        except (ValueError, TypeError):
            continue
    out.extend(_spelled_numbers(msg))
    return out


def _token_sublist(needle: list[str], haystack: list[str]) -> bool:
    """True when ``needle`` appears as a contiguous run of WHOLE tokens."""
    if not needle:
        return False
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


def _is_grounded_in_user(field_name: str, value: Any, user_message: str) -> bool:
    """Return True iff this value traces back to text the user actually wrote.

    The match is by WHOLE TOKEN, never by substring. The substring rule this
    replaced accepted any value that shared a character run with the message, so
    a model that recorded $150 for a "send $1500" request passed the backstop and
    the 2FA threshold never armed — and any digits anywhere in the message
    ("call me on 555-481920") grounded an invented 6-digit code.

    Three rules:

      1. Numeric value: it must equal a number the user typed, after the same
         normalization a numeric field gets ("5k" -> 5000, "$1,000" -> 1000).
      2. Other values: the value's tokens must appear as a contiguous run of
         whole tokens in the message.
      3. Date-shaped field name + a date-anchoring word in the message. The LLM
         rewrites "tomorrow" to an ISO string, so the value itself cannot match;
         the user clearly did anchor a day.
      4. A time written HH:MM that equals a time the user stated ("3pm" for
         "15:00"). Equality, not a hint word: a time nobody said is dropped.
    """
    if not user_message:
        return False
    msg = user_message.lower()
    val = str(value).lower().strip()
    if not val:
        return False

    if isinstance(value, bool):
        return _consent_grounded(value, msg)

    if isinstance(value, (int, float)):
        typed = _message_numbers(msg)
        if any(t == float(value) for t in typed):
            return True
    else:
        val_tokens = _WORD_TOKEN_RE.findall(val)
        msg_tokens = _WORD_TOKEN_RE.findall(msg)
        if _token_sublist(val_tokens, msg_tokens):
            return True

    clock = _HHMM_RE.fullmatch(val) if isinstance(value, str) else None
    if clock and f"{int(clock.group(1)):02d}:{clock.group(2)}" in _message_times(msg):
        return True

    if isinstance(value, str) and _DATE_FIELD_RE.search(field_name.lower()):
        if _DATE_HINT_RE.search(msg):
            return True
        day = _ISO_DAY_RE.fullmatch(val)
        if day and any(int(n) == int(day.group(1)) for n in _ORDINAL_RE.findall(msg)):
            return True
    return False


def _consent_grounded(value: bool, msg: str) -> bool:
    """True when the user's own words carry a yes/no matching ``value``.

    A confirmation is the one field where the value is not a token the user
    typed, so grounding is polarity: an approval needs an affirmative word and
    no contradicting negative. Without this the model could answer its own
    confirm question — the transfer fired on a turn whose entire user text was
    "no, cancel that".
    """
    ordered = _WORD_TOKEN_RE.findall(msg.lower())
    tokens = set(ordered)
    affirm = bool(tokens & (_AFFIRMATIVE - _WEAK_AFFIRMATIVE)) or any(
        _token_sublist(list(phrase), ordered) for phrase in _AFFIRMATIVE_PHRASES
    ) or (
        # "sure", "confirm it", "right, send it" open a short reply; "I confirm"
        # is a two-word one. "what happens after I confirm" is neither.
        bool(ordered) and bool(tokens & _WEAK_AFFIRMATIVE)
        and ((ordered[0] in _WEAK_AFFIRMATIVE and len(ordered) <= 4) or len(ordered) <= 2)
    )
    deny = bool(tokens & _NEGATIVE)
    if value:
        # A question is not consent, whatever words it contains.
        return affirm and not deny and "?" not in msg
    return bool(tokens & (_NEGATIVE - _PAUSE))


def user_only_fields(skill: Skill) -> set[str]:
    """Field names whose value is only ever valid when the USER supplied it.

    Four kinds, all derived — no new authoring key, so a shipped SKILL.md
    cannot forget to opt in:

      * ``must_elicit: true`` — a declared gate input. The loader already forces
        this onto every field a ``compute:``/``when:``/``check:`` gate reads.
      * ``skill.confirmation_fields`` — the booleans an action names in
        ``requires:``. The model must never be able to answer the confirm
        question on the user's behalf.
      * ``sensitive: true`` — a secret the user holds and the model does not (a
        one-time code). A model-emitted value for one of these is, by
        definition, fabricated.
      * ``skill.picked_fields`` — what a ``pick:`` step saves. The model can
        read the list (the conversation's record shows it), so "the model does
        not know the ids" no longer keeps it from choosing a row. This does.
    """
    out = {n for n, s in (skill.fields or {}).items() if s.must_elicit}
    out |= {
        n for n in (getattr(skill, "confirmation_fields", None) or [])
        if n in (skill.fields or {})
    }
    out |= {
        n for n, s in (skill.fields or {}).items()
        if getattr(s, "sensitive", False)
    }
    out |= {n for n in (getattr(skill, "picked_fields", None) or []) if n in (skill.fields or {})}
    return out


def _needs_pending_ask(skill: Skill, name: str) -> bool:
    """True when free-text extraction of this field is only trustworthy on the
    turn the flow actually asked for it.

    Secrets and shape-constrained codes: the user types these in reply to the
    ask, never in passing. Requiring the pending ask is what separates "the user
    answered the 2FA prompt with 481920" from "the user mentioned a phone number
    that happens to contain six digits".
    """
    spec = (skill.fields or {}).get(name)
    if spec is None:
        return False
    if getattr(spec, "sensitive", False):
        return True
    if name in (getattr(skill, "confirmation_fields", None) or []):
        return True
    return False


def _filter_must_elicit(
    interp: TurnInterpretation,
    skill: Skill,
    user_message: str,
    *,
    awaiting_field: str | None = None,
    pending_fields: list[str] | None = None,
    current_fields: dict[str, Any] | None = None,
) -> TurnInterpretation:
    """GATE INPUTS AND CONFIRMATIONS COME FROM THE USER.

    Drops every SetField / Correct the model emitted for a user-only field
    (see ``user_only_fields``) unless the value is grounded in what the user
    actually wrote. Deterministic backstop: the interpreter prompt asks for the
    same restraint, but instruction following is statistical, and a fabricated
    2FA code or a self-answered confirmation is not a class of mistake a prompt
    may be trusted with.

    Structured submissions (``chat.submit`` / ``Command(resume={...})``) never
    reach here — they bypass the interpreter entirely, which is exactly why they
    are the trusted path.
    """
    protected = user_only_fields(skill)
    if not protected:
        return interp
    pending = set(pending_fields or ())
    if awaiting_field:
        pending.add(awaiting_field)

    filled = {n for n, v in (current_fields or {}).items() if v is not None}
    confirmations = set(getattr(skill, "confirmation_fields", None) or [])
    picked = set(getattr(skill, "picked_fields", None) or [])

    kept: list[Any] = []
    dropped = False
    unheard: str | None = None  # the field being asked, when ITS answer was dropped
    unchanged: str | None = None  # a filled field the user tried to change, and the change was dropped
    reopen: list[str] = []  # list choices the user asked to change in words
    for item in interp.items:
        if isinstance(item, (SetField, Correct)) and item.field in protected:
            if _needs_pending_ask(skill, item.field) and item.field not in pending:
                # The flow never asked for it on this turn, so the model
                # volunteered it. Drop it every time.
                dropped = True
                continue
            if (
                item.field in picked
                and item.field in filled
                and _as_written(skill, item.field, item.value) == (current_fields or {}).get(item.field)
            ):
                # The choice already made, sent again: it changes nothing, so
                # it is not a change that was refused. (With the list in view a
                # model answering "who did I pick?" tends to restate the pick.)
                continue
            # Judge the value the runtime will WRITE, not the JSON shape the
            # model happened to use. A confirmation sent as "yes" or 1 is
            # judged as a yes/no, never as a word or a number ("yes" is a
            # token of "no wait yes"; 75 is a number in "no, make it 75").
            if not _is_grounded_in_user(item.field, _as_written(skill, item.field, item.value), user_message):
                dropped = True
                if item.field in pending:
                    unheard = unheard or item.field
                elif item.field in picked and item.field in filled:
                    # "Send it to Mom instead": the user wants a different row
                    # and only a click (or the id itself) chooses one. Show the
                    # list again rather than refuse.
                    reopen.append(item.field)
                elif item.field in filled or isinstance(item, Correct):
                    unchanged = unchanged or item.field
                continue
        kept.append(item)
    for name in dict.fromkeys(reopen):
        if not any(isinstance(it, Reopen) and it.field == name for it in kept):
            kept.append(Reopen(field=name))

    if unchanged:
        # "yes, but one more night": the change did not land, so the yes is not
        # a yes to what would run. Hold it back and ask again.
        kept = [
            it for it in kept
            if not (
                isinstance(it, (SetField, Correct)) and it.field in confirmations
                and _as_written(skill, it.field, it.value) is True
            )
        ]

    if dropped:
        # The model wrote its Reply believing the value would land ("Got it,
        # you want to send $5000") and the flow is about to ask for exactly
        # that value. Replace the claim with a neutral acknowledgement so the
        # two never contradict each other on screen.
        #
        # A hint is added only when it will still be true once the turn has
        # run: nothing else happens in it. With a Cancel, a switch to another
        # flow, or another value that did land, the next lines on screen come
        # from somewhere else, and "Please answer yes or no." in front of
        # "Okay, cancelled." is worse than "Okay.".
        moves = any(isinstance(it, (Cancel, StartFlow, Restart, Resume, Reopen)) for it in kept)
        writes = [it for it in kept if isinstance(it, (SetField, Correct))]
        hint: str | None = None
        if not moves:
            if unchanged and not any(it.field == unchanged for it in writes):
                hint = _not_changed(skill, unchanged)
            elif unheard and not writes:
                hint = _how_to_answer(skill, unheard)
        # The user ASKED something ("who did I pick last time?") and the model
        # both answered it and guessed a value for the question on screen. The
        # guess is gone; the answer stays. Replacing it with "Please choose one
        # of: ..." answered a question with an instruction. Never for a
        # confirmation or a secret: there the model's words could be "sending
        # it now", and the plain yes/no hint is the true line.
        answered = (
            not moves and not writes and not unchanged and bool(unheard)
            and unheard not in confirmations
            and not getattr((skill.fields or {}).get(unheard), "sensitive", False)
            and _asks_something(user_message)
            and any(isinstance(it, Reply) and it.text.strip() for it in kept)
        )
        replied = False
        for item in kept:
            if isinstance(item, Reply):
                if not answered:
                    item.text = hint or "Okay."
                replied = True
        if hint and not replied:
            kept.append(Reply(text=hint))
    interp.items = kept
    return interp


_QUESTION_WORDS = frozenset({"who", "whom", "whose", "what", "which", "when", "where", "why", "how"})


def _asks_something(msg: str) -> bool:
    """True when the message reads as a question rather than an answer."""
    return "?" in msg or bool(set(_WORD_TOKEN_RE.findall(msg.lower())) & _QUESTION_WORDS)


def _as_written(skill: Skill, name: str, value: Any) -> Any:
    """``value`` as the runtime would store it for this field, or unchanged when
    it would be refused (the runtime then refuses it with its own message)."""
    spec = (skill.fields or {}).get(name)
    if spec is None:
        return value
    from .fields import coerce_and_validate

    try:
        return coerce_and_validate(spec, value)
    except Exception:  # noqa: BLE001 - any refusal means "judge it as sent"
        return value


def _not_changed(skill: Skill, name: str) -> str | None:
    """Told to the user when a change to a value they already gave is dropped.

    "make it the 24th instead" at the confirmation: the model moved the day, the
    guard put it back, and "Okay." let the user say yes to the day they had just
    tried to change.
    """
    spec = (skill.fields or {}).get(name)
    if spec is None or getattr(spec, "sensitive", False):
        return None
    return "I could not take that change, so nothing was changed. Please state the new value exactly."


def _how_to_answer(skill: Skill, name: str) -> str | None:
    """What to say when the user answered the question being asked, in words
    the grounding guard cannot tie to a value.

    The guard is right to drop it, but "Okay." followed by the same question
    looked as if the answer had been heard and then lost: "book it" in reply to
    "Shall I book it?", "I have paid them before" in reply to a choice. Say what
    will be accepted instead.

    A secret gets no hint. Nobody answers a code prompt "in other words": a
    dropped code is one the model made up, and "Okay." stays the whole reply.
    """
    spec = (skill.fields or {}).get(name)
    if spec is None or getattr(spec, "sensitive", False):
        return None
    kind = getattr(spec, "type", "string")
    if name in (getattr(skill, "picked_fields", None) or []):
        return "Please pick one from the list."
    if kind == "boolean":
        return "Please answer yes or no."
    if kind == "enum" and spec.values:
        from .fields import option_label

        return "Please choose one of: " + ", ".join(option_label(v) for v in spec.values) + "."
    if kind in ("number", "integer"):
        return "I need that as a number."
    return "I need that stated exactly."


# ---------------------------------------------------------------------------
# Public entry: interpret one user turn
# ---------------------------------------------------------------------------


def _today_str(host: Host) -> str:
    """The runtime's current date, formatted for the interpret prompt.

    Sourced from ``host.now()`` so it tracks whatever clock the host uses
    (tests can pin it). The interpreter needs this because flow bodies tell
    the LLM to resolve "tomorrow" against "today" — but the LLM has no clock,
    so without an explicit anchor it falls back on its training-era date and
    emits stale (often past) years. See the date-context block in the prompts.
    """
    try:
        dt = datetime.datetime.fromtimestamp(host.now())
        if dt.year < 2000:  # TestHost default clock (0.0) → epoch; unhelpful
            dt = datetime.datetime.now()
    except Exception:
        dt = datetime.datetime.now()
    return dt.strftime("%Y-%m-%d (%A)")


def _render_digression_block(digressions: list[Skill]) -> str:
    if not digressions:
        return "(none available — do NOT emit StartFlow)"
    lines = []
    for d in digressions:
        lines.append(f"- {d.name}: {d.description}")
    return "\n".join(lines)


def _render_awaiting_block(skill: Skill, awaiting_field: str | None) -> str:
    """Tell the interpreter which field the flow just asked for, so short or
    ambiguous answers ("3", "morning", "yes") resolve against the question
    that was actually posed instead of being guessed across all fields."""
    if not awaiting_field:
        return "(none — no question is pending)"
    spec = (skill.fields or {}).get(awaiting_field)
    if spec is None:
        return f"The flow just asked the user for `{awaiting_field}`."
    bits = [f"The flow just asked the user for `{awaiting_field}` ({spec.type})."]
    if spec.values:
        bits.append(f"Allowed values: {spec.values}.")
    bits.append(
        "A short answer in this message most likely answers THIS question — "
        "interpret it against this field first. The user may still answer a "
        "different field, correct an earlier one, or digress; trust the "
        "message over this hint when they conflict."
    )
    return " ".join(bits)


def interpret_turn(
    *,
    host: Host,
    skill: Skill,
    user_message: str,
    current_fields: dict[str, Any],
    mode: str = "flex",
    digressions: list[Skill] | None = None,
    awaiting_field: str | None = None,
    pending_fields: list[str] | None = None,
    memory: str = "",
    paused: str = "",
) -> TurnInterpretation:
    """Run the interpreter LLM on one user turn against an active flow.

    ``memory`` is the read-only record of what already happened in this
    conversation and ``paused`` describes a task the user stopped part-way
    (both from ``render_memory`` / ``render_paused``; "" when there is none).
    They let the model ANSWER about the past. They do not widen what it may
    write: gate inputs and confirmations are still checked against the user's
    own words in this message.

    Two interpretation strategies live behind one entry point:

      - ``mode="flex"`` (default): one free-form structured-output call.
        The LLM returns a JSON object whose ``items`` array can hold any
        mix of typed items including a free-prose ``Reply``. Maximum
        warmth, statistical interpretation reliability.

      - ``mode="strict"``: one constrained tool-use call. Tools are
        generated from the active skill's ``FieldSpec`` plus a small
        control vocabulary (cancel / out_of_scope / start_digression).
        The provider's tool schema physically prevents the LLM from
        emitting wrong field names, off-type values, or unknown digression
        ids. A ``reply`` tool preserves the warm voice. Strict requires
        ``host.supports_tool_use`` — callers (run_turn) check and fall
        back to flex when missing.

    Both paths feed the same ``_filter_must_elicit`` guard and return the
    same ``TurnInterpretation`` shape, so downstream code is mode-agnostic.
    """
    if mode == "strict":
        return _interpret_turn_strict(
            host=host,
            skill=skill,
            user_message=user_message,
            current_fields=current_fields,
            digressions=digressions or [],
            awaiting_field=awaiting_field,
            pending_fields=pending_fields,
            memory=memory,
            paused=paused,
        )
    return _interpret_turn_flex(
        host=host,
        skill=skill,
        user_message=user_message,
        current_fields=current_fields,
        digressions=digressions or [],
        awaiting_field=awaiting_field,
        pending_fields=pending_fields,
        memory=memory,
        paused=paused,
    )


# ---------------------------------------------------------------------------
# Memory: what already happened in this conversation
# ---------------------------------------------------------------------------

_OUTCOMES = {
    "ended": "ENDED (ended does not mean it succeeded)",
    "declined": "ENDED with the user saying NO at its confirmation, so its action did NOT run",
    "stopped": "STOPPED early, because a step it handed to another workflow was not completed",
    "cancelled": "CANCELLED by the user part-way, so its remaining steps did NOT run",
    "restarted": "DROPPED part-way because the user asked to start over",
}


def render_memory(
    journal: list[dict[str, Any]] | None,
    history: list[dict[str, Any]] | None = None,
    *,
    in_flow: bool = True,
    current: str = "",
    changed: dict[str, list[Any]] | None = None,
) -> str:
    """The conversation's record as a prompt section ("" when there is none).

    Two parts: what each workflow run came to (written by the runtime, so it is
    a fact and not the model's recollection), and the last lines of the
    conversation. The rules under it are what keep a deterministic flow
    deterministic with a memory attached: the record answers questions, and it
    is never where a value comes from.
    """
    runs: list[str] = []
    for n, entry in enumerate(journal or [], 1):
        line = f'{n}. "{entry.get("flow")}": {_OUTCOMES.get(str(entry.get("outcome")), str(entry.get("outcome")))}.'
        if entry.get("waiting_for"):
            line += f' It was waiting for "{entry["waiting_for"]}".'
        if entry.get("wrote"):
            line += " Part of it HAD already gone through before that (see its results)."
        handed = "; ".join(
            f"{h.get('flow')} ({'completed' if h.get('completed') else 'NOT completed'})"
            for h in (entry.get("handoffs") or [])
        )
        if handed:
            line += f" Steps it handed to other workflows: {handed}."
        line += f" Details the user gave: {json.dumps(entry.get('fields') or {}, default=str)}."
        if entry.get("results") and entry["results"] != "{}":
            line += f" Results of its actions: {entry['results']}"
        runs.append(line)
    said = [
        f"  {m.get('role')}: {str(m.get('content'))[:240]}"
        for m in (history or [])[-8:]
        if m.get("content")
    ]
    if not runs and len(said) < 2 and not current and not changed:
        return ""
    out = "\n# Earlier in this conversation (a read-only record)\n"
    if current:
        # The lists and quotes the flow in hand has already shown the user.
        out += f"What the CURRENT flow's actions have returned so far: {current}\n"
    if changed:
        # An answer taken back is gone from "Currently filled fields", and
        # "who did I pick before?" still deserves the name.
        out += (
            "Answers the user gave earlier in the CURRENT flow and has since changed or "
            f"taken back (oldest first): {json.dumps(changed, default=str)}\n"
        )
    if runs:
        out += "Workflow runs so far, oldest first:\n" + "\n".join(runs) + "\n"
    if said:
        out += "The last lines of the conversation:\n" + "\n".join(said) + "\n"
    out += (
        "How to use this record:\n"
        "- Answer the user's questions about it in your reply, with the actual names and\n"
        "  values. Look an id (such as P-1002) up in the results to give its name.\n"
        "- Something was sent, paid, saved, booked or changed ONLY if a result or a line\n"
        "  above says so. If nothing does, say plainly that it was NOT done.\n"
    )
    if in_flow:
        out += (
            "- It is NOT a source of values. Take a value from it only when the user's\n"
            "  CURRENT message asks for the same thing again (\"the same amount as before\").\n"
            "- A question about the record does not answer the question the flow is asking:\n"
            "  reply to it and emit nothing for the awaiting field.\n"
            "- You never choose a row of a list for the user. If they name one in words,\n"
            "  say they can pick it from the list; the program shows the list.\n"
        )
    return out


def render_paused(parked: dict[str, Any] | None, *, in_flow: bool = True) -> str:
    """The task the user stopped part-way, as a prompt section ("" when none)."""
    if not parked:
        return ""
    root = parked.get("root") or parked.get("flow_id")
    at = parked.get("flow_id")
    where = f'"{root}"' + (f' (it had reached "{at}")' if at and at != root else "")
    waiting = f', waiting for "{parked["waiting_for"]}"' if parked.get("waiting_for") else ""
    how = "emit Resume" if in_flow else 'choose action "resume"'
    return (
        "\n# Paused task\n"
        f"The user stopped {where} part-way{waiting}. What they had entered is kept:\n"
        f"{json.dumps(parked.get('shown') or {}, default=str)}\n"
        f"- If the user asks to continue it, carry on, pick it up or go back to it: {how}.\n"
        "  It reopens at the question it stopped on, and any approval is asked for again.\n"
        "- If they ask to do it again from the beginning, or for something else, that is\n"
        "  NOT a resume.\n"
    )


# ---------------------------------------------------------------------------
# Outside a flow: after one finishes, or when no skill matched
# ---------------------------------------------------------------------------


class OutsideFlowDecision(BaseModel):
    """What to do with a message that is NOT inside a running workflow."""

    #: ``"resume"`` is offered only while a task is paused (see ``render_paused``).
    action: Literal["reply", "start_skill", "resume"] = "reply"
    #: Exact skill name, only when ``action == "start_skill"``.
    skill: str | None = None
    #: What to say to the user, only when ``action == "reply"``.
    reply: str = ""


_OUTSIDE_FLOW_SYSTEM = """\
You are a helpful, friendly assistant in a chat that can also run a few
structured workflows. No workflow is running right now, so you are NOT limited
to any one topic.
{finished_block}{memory_block}{paused_block}
# Workflows you can start
{skills_block}

# How to decide
- If the user asks about what was just done or what happened earlier (its
  status, a summary, "show me the plan", "who did I pick?"), answer from the
  details above. Show the actual values. Never say you "can" show something —
  show it.
- If the user clearly asks to START a task one of the workflows covers —
  including doing a finished or stopped one again from the beginning ("start
  over", "do that again") — choose action "start_skill" with that workflow's
  exact name. Do not start a workflow just because a word from its name
  appeared.
- Otherwise choose "reply" and answer normally, on any topic, like a general
  assistant. You have no tools in this chat: for anything needing live data
  (weather, prices, news, the user's accounts) say you cannot look it up here,
  then help with what you do know.
- If asked what you can do, describe the workflows above in plain words and add
  that you can also just chat.
- Never claim that an action was performed on this turn, and never invent a
  value that is not in the details above.
- Be brief. Today is {today}.
"""


def _render_results(results: dict[str, Any], *, budget: int = 3600, each: int = 700) -> str:
    """Action results for the model, newest first under a size budget.

    Results are stored in the order the actions ran, and a flow usually starts
    with its searches. Cutting the whole blob at a fixed length therefore kept
    two long result lists and dropped everything that describes the outcome —
    the quote, the route, the booking references. The last actions are what a
    follow-up question is about, so they are kept first, each one capped.
    """
    kept: list[str] = []
    for name, value in reversed(list(results.items())):
        text = json.dumps(value, default=str)
        if len(text) > each:
            text = text[:each] + "…"
        line = f'"{name}": {text}'
        if len(line) > budget:
            break
        kept.append(line)
        budget -= len(line)
    return "{" + ", ".join(reversed(kept)) + "}"


def _render_finished_block(
    skill: Skill | None,
    fields: dict[str, Any] | None,
    results: dict[str, Any] | None,
    handoffs: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str:
    if skill is None:
        return ""
    shown: dict[str, Any] = {}
    for name, value in (fields or {}).items():
        if value is None:
            continue
        spec = (skill.fields or {}).get(name)
        if getattr(spec, "sensitive", False):
            shown[name] = "(hidden)"
        elif isinstance(value, float) and value.is_integer():
            shown[name] = int(value)  # 4000, not 4000.0 — the model repeats what it reads
        else:
            shown[name] = value
    details = json.dumps(shown, default=str)
    outcome = _render_results(results or {})
    # "FINISHED" is not "succeeded". Told only that send-money had finished, with
    # {"pay_to": "someone_new"} collected, the model answered "I sent money to
    # someone_new" after an errand in which nothing was saved or sent. So the
    # block states what did and did not happen, and shows what the user was told.
    steps = "; ".join(
        f"{h.get('flow')}: {'completed' if h.get('completed') else 'NOT completed (cancelled or declined; it did nothing)'}"
        for h in (handoffs or [])
    )
    said = "\n".join(
        f"  {m.get('role')}: {str(m.get('content'))[:240]}"
        for m in (history or [])[-8:]
        if m.get("content")
    )
    return (
        "\n# What just happened\n"
        f"The workflow \"{skill.name}\" has ENDED. Ended does not mean it succeeded.\n"
        f"What it is for: {skill.description}\n"
        f"Details collected: {details}\n"
        f"Results of the actions that ran: {outcome}\n"
        + (f"Steps it handed to other workflows: {steps}\n" if steps else "")
        + (f"The end of the conversation so far:\n{said}\n" if said else "")
        + "Something was sent, paid, saved, booked or changed ONLY if a result above or a\n"
        "line of the conversation says so. If nothing does, say plainly that it was NOT\n"
        "done. Never present a collected detail (such as a choice the user picked) as\n"
        "the person or thing that was paid.\n"
    )


def decide_outside_flow(
    *,
    host: Host,
    skills: list[Skill],
    user_message: str,
    finished: Skill | None = None,
    fields: dict[str, Any] | None = None,
    results: dict[str, Any] | None = None,
    handoffs: list[dict[str, Any]] | None = None,
    history: list[dict[str, Any]] | None = None,
    memory: str = "",
    paused: str = "",
) -> OutsideFlowDecision | None:
    """Route one message that arrives with no workflow running.

    A finished workflow does not own the conversation: a later message is not
    read against it under that flow's one-topic prompt. One question is asked
    instead (answer, start something, or go back to a cancelled run), with the
    finished run's real data in view and no topic restriction.

    Returns ``None`` when the host cannot answer this schema (a scripted test
    host), so the caller keeps its previous behaviour.
    """
    workflows = [s for s in skills if getattr(s, "workflow", True)]
    skills_block = (
        "\n".join(f"- {s.name}: {s.description}" for s in workflows)
        or "(none are loaded)"
    )
    system = _OUTSIDE_FLOW_SYSTEM.format(
        finished_block=_render_finished_block(finished, fields, results, handoffs, history),
        memory_block=memory,
        paused_block=paused,
        skills_block=skills_block,
        today=_today_str(host),
    )
    try:
        decision = host.llm_structured(
            system=system, user=user_message, schema=OutsideFlowDecision
        )
    except NotImplementedError:
        return None
    if decision.action == "resume" and not paused:
        # Nothing is paused, so there is nothing to go back to.
        decision.action = "reply"
    if decision.action == "start_skill":
        names = {s.name for s in workflows}
        if decision.skill not in names:
            # A name the model made up is not a routing decision.
            decision.action = "reply"
            decision.skill = None
    return decision


def render_guidelines(skill: Skill) -> str:
    """The skill's `guidelines:` as a prompt section ("" when none).

    Deterministically ordered (as authored) and injected verbatim into the
    interpreter and chat/assist prompts — guidelines steer what the model
    SAYS; the deterministic policy still owns what the flow DOES.
    """
    if not getattr(skill, "guidelines", None):
        return ""
    lines = []
    for g in skill.guidelines:
        rule = f"When {g.when}: {g.then}" if g.when else g.then
        if g.tools:
            rule += f" (relevant tools: {', '.join(g.tools)})"
        lines.append(f"- {rule}")
    return "\n\n# Behavior guidelines\n\nFollow these in every reply:\n" + "\n".join(lines)


def _interpret_turn_flex(
    *,
    host: Host,
    skill: Skill,
    user_message: str,
    current_fields: dict[str, Any],
    digressions: list[Skill],
    awaiting_field: str | None = None,
    pending_fields: list[str] | None = None,
    memory: str = "",
    paused: str = "",
) -> TurnInterpretation:
    header = _FLEX_HEADER + _render_must_elicit_rule(skill)
    system = _INTERPRET_SYSTEM.format(
        mode_header=header,
        today=_today_str(host),
        flow_body=(skill.body or skill.description) + render_guidelines(skill),
        field_block=_render_field_block(skill),
        current_fields=json.dumps(current_fields, default=str),
        awaiting_block=_render_awaiting_block(skill, awaiting_field),
        digression_block=_render_digression_block(digressions),
        memory_block=memory + paused,
    )
    user = _INTERPRET_USER.format(user_message=user_message)
    interp = host.llm_structured(system=system, user=user, schema=TurnInterpretation)
    return _filter_must_elicit(
        interp, skill, user_message,
        awaiting_field=awaiting_field, pending_fields=pending_fields,
        current_fields=current_fields,
    )


# ---------------------------------------------------------------------------
# Strict mode — constrained tool calling
# ---------------------------------------------------------------------------


_STRICT_SYSTEM = """\
You are interpreting one user turn in a structured conversational flow.

You do NOT decide what happens next; the deterministic policy does. Your only
job is to call the right tool(s) describing what the user said. The schema of
each tool physically prevents wrong field names, off-type values, and unknown
digression ids — trust it and call.

# Date context

Today is {today}. Resolve EVERY relative date ("today", "tomorrow", "next
Monday") against THIS date and output `YYYY-MM-DD`. Never use any other
year — your training data's current date is irrelevant.

# Active flow

{flow_body}

# Currently filled fields

{current_fields}

# Awaiting field
{awaiting_block}
{memory_block}
{must_elicit_rule}
# How to choose tools

- Call `set_<field>` when the user provides a value for an unfilled field.
- Call `correct_<field>` when the user changes a previously-filled field
  ("actually make it 4pm not 3pm"). Do NOT use set_<field> to overwrite.
- Call `cancel` when the user aborts the whole flow ("nevermind", "stop").
  Do NOT call cancel when answering "no" to a confirm question — that's a
  legitimate answer; call `set_confirm` with value=false instead.
- Call `out_of_scope` when the user asks something this flow can't handle.
  out_of_scope does NOT change any field — already-filled fields stay
  filled.
- Call `start_digression` when the user asks a question that a registered
  digression flow handles. The runtime pauses this flow, runs the
  digression, and resumes here with every field preserved.
- Call `restart` when the user wants this task again from its first
  question ("start over"). Not for changing one answer.
- Call `reopen` when the user wants to answer ONE earlier question again
  and has not said the new answer ("let me pick a different payee"). With
  the new answer in the message, call correct_<field> instead.
- Call `resume_paused` (only offered while a task is paused) when the user
  asks to go back to the paused task or carry on with it.
- Call `reply` to say something warm and on-topic to the user. Reply is
  the only place free-form prose lives. Reply CANNOT change field state.
  A question about what already happened in this conversation is answered
  in `reply`, from the record above when one is shown.

You may call MULTIPLE tools in one response — a typical turn that fills a
field also includes a brief `reply` acknowledging it. If nothing matches,
call `reply` alone with a redirect.
"""

_STRICT_USER = """\
The user just said:

{user_message}

Interpret by calling the appropriate tool(s).
"""


def _interpret_turn_strict(
    *,
    host: Host,
    skill: Skill,
    user_message: str,
    current_fields: dict[str, Any],
    digressions: list[Skill],
    awaiting_field: str | None = None,
    pending_fields: list[str] | None = None,
    memory: str = "",
    paused: str = "",
) -> TurnInterpretation:
    tools = _build_tools_for_skill(skill, digressions, paused=bool(paused))
    system = _STRICT_SYSTEM.format(
        today=_today_str(host),
        flow_body=(skill.body or skill.description) + render_guidelines(skill),
        current_fields=json.dumps(current_fields, default=str),
        awaiting_block=_render_awaiting_block(skill, awaiting_field),
        memory_block=memory + paused,
        must_elicit_rule=_render_must_elicit_rule(skill),
    )
    user = _STRICT_USER.format(user_message=user_message)
    calls = host.llm_tool_call(system=system, user=user, tools=tools, tool_choice="any")
    interp = _decode_tool_calls(calls, skill)
    return _filter_must_elicit(
        interp, skill, user_message,
        awaiting_field=awaiting_field, pending_fields=pending_fields,
        current_fields=current_fields,
    )


def _build_tools_for_skill(
    skill: Skill,
    digressions: list[Skill],
    *,
    paused: bool = False,
) -> list[dict[str, Any]]:
    """Generate a tool-use schema list from the skill's fields + control
    vocabulary + reachable digressions. One tool per field (set + correct),
    typed by FieldSpec. The provider's tool-use API enforces the schema
    at the wire."""
    tools: list[dict[str, Any]] = []

    tools.append({
        "name": "reply",
        "description": (
            "Send a natural-language message to the user. Use for warmth, "
            "acknowledgement, redirection, or asking clarification. CANNOT "
            "change field state — use set_/correct_ tools for that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    })

    for name, spec in (skill.fields or {}).items():
        value_schema = _field_value_schema(spec)
        set_desc = spec.description or f"Set the {name} field."
        if spec.must_elicit:
            set_desc += (
                " IMPORTANT: only call this when the user EXPLICITLY stated "
                "a value in their message. Do not infer from context."
            )
        tools.append({
            "name": f"set_{name}",
            "description": set_desc,
            "input_schema": {
                "type": "object",
                "properties": {"value": value_schema},
                "required": ["value"],
            },
        })
        tools.append({
            "name": f"correct_{name}",
            "description": (
                f"Correct the previously-set {name} field. Use this — NOT "
                "set_ — when the user is changing a value they already gave."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"value": value_schema},
                "required": ["value"],
            },
        })

    tools.append({
        "name": "cancel",
        "description": (
            "User wants to abandon the current flow entirely. NOT for "
            "answering 'no' to a confirm question."
        ),
        "input_schema": {"type": "object", "properties": {}},
    })

    tools.append({
        "name": "restart",
        "description": (
            "User wants this task again from its first question ('start over'). "
            "Drops everything collected. NOT for changing one answer."
        ),
        "input_schema": {"type": "object", "properties": {}},
    })

    reopenable = [n for n in (skill.fields or {}) if not _needs_pending_ask(skill, n)]
    if reopenable:
        tools.append({
            "name": "reopen",
            "description": (
                "User wants to answer ONE earlier question again and has not "
                "said the new answer. The field is cleared and asked again."
            ),
            "input_schema": {
                "type": "object",
                "properties": {"field": {"type": "string", "enum": reopenable}},
                "required": ["field"],
            },
        })

    if paused:
        tools.append({
            "name": "resume_paused",
            "description": (
                "User asks to go back to the task they stopped part-way, or to "
                "carry on with it. NOT for doing it again from the beginning."
            ),
            "input_schema": {"type": "object", "properties": {}},
        })

    tools.append({
        "name": "out_of_scope",
        "description": (
            "User said something this flow can't handle (off-topic, "
            "unrelated question). Does NOT change fields; pair with a "
            "reply that redirects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
        },
    })

    if digressions:
        tools.append({
            "name": "start_digression",
            "description": (
                "Pause this flow to run a registered digression flow, then "
                "resume here with all fields preserved. Use when the user "
                "asks a question handled by one of the listed digressions."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "flow_id": {
                        "type": "string",
                        "enum": [d.name for d in digressions],
                        "description": "; ".join(
                            f"{d.name}: {d.description}" for d in digressions
                        ),
                    }
                },
                "required": ["flow_id"],
            },
        })

    return tools


def _field_value_schema(spec: Any) -> dict[str, Any]:
    """Translate a FieldSpec into a JSON-schema fragment for the tool arg.
    Enum fields collapse to typed enums — the LLM physically cannot return
    an invalid value."""
    out: dict[str, Any] = {}
    ftype = getattr(spec, "type", "string")
    if ftype == "integer":
        out["type"] = "integer"
        if getattr(spec, "min", None) is not None:
            out["minimum"] = spec.min
        if getattr(spec, "max", None) is not None:
            out["maximum"] = spec.max
    elif ftype == "boolean":
        out["type"] = "boolean"
    elif ftype == "enum":
        out["type"] = "string"
        values = getattr(spec, "values", None) or []
        if values:
            out["enum"] = list(values)
    elif ftype == "number":
        out["type"] = "number"
        if getattr(spec, "min", None) is not None:
            out["minimum"] = spec.min
        if getattr(spec, "max", None) is not None:
            out["maximum"] = spec.max
    else:
        out["type"] = "string"
    desc = getattr(spec, "description", None)
    if desc:
        out["description"] = desc
    return out


def _decode_tool_calls(calls: list[ToolCall], skill: Skill) -> TurnInterpretation:
    """Map provider tool calls back into the runtime's typed items. Field
    names come straight from the tool name suffix — there is no parsing
    ambiguity because the tool schema enforced the name."""
    items: list[Any] = []
    valid_fields = set((skill.fields or {}).keys())
    for call in calls:
        name = call.name
        args = call.args or {}
        if name == "reply":
            text = args.get("text", "")
            if text:
                items.append(Reply(text=text))
        elif name.startswith("set_"):
            field = name[len("set_"):]
            if field in valid_fields and "value" in args:
                items.append(SetField(field=field, value=args["value"]))
        elif name.startswith("correct_"):
            field = name[len("correct_"):]
            if field in valid_fields and "value" in args:
                items.append(Correct(field=field, value=args["value"]))
        elif name == "cancel":
            items.append(Cancel())
        elif name == "restart":
            items.append(Restart())
        elif name == "reopen":
            if args.get("field") in valid_fields:
                items.append(Reopen(field=args["field"]))
        elif name == "resume_paused":
            items.append(Resume())
        elif name == "out_of_scope":
            items.append(OutOfScope(reason=args.get("reason")))
        elif name == "start_digression":
            flow_id = args.get("flow_id")
            if flow_id:
                items.append(StartFlow(flow_id=flow_id))
    return TurnInterpretation(items=items)

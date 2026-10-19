"""FieldSpec: typed variables a flow collects, with validation."""
from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FieldType = Literal["string", "number", "integer", "boolean", "enum", "any"]


class FieldSpec(BaseModel):
    """Schema for one collected field.

    The optional `must_elicit` flag tells the runtime that this field's value
    can only come from an explicit user statement — no inference, no default
    from context. The interpreter applies a deterministic backstop that drops
    inferred values, so even a well-meaning LLM hallucination ("the user
    probably meant today") cannot land in state.

    Unknown keys are REJECTED (``extra="forbid"``). A silently-swallowed
    ``sensitve:`` would drop an OTP into the audit log in clear, and a
    swallowed ``maximum:`` would remove a cap — so a misspelled key is a load
    error that names the nearest valid one instead of a weaker policy.
    """

    model_config = {"extra": "forbid"}

    type: FieldType = "string"
    description: str | None = None
    values: list[str] | None = None  # required when type == "enum"
    min: float | None = None
    max: float | None = None
    required: bool = True
    examples: list[str] = Field(default_factory=list)
    must_elicit: bool = False
    # Optional regex a string value must FULLY match (e.g. a 6-digit OTP:
    # '^[0-9]{6}$'). Checked in coerce_and_validate. A constrained string is the
    # only kind of free-text field a declarative tool arg may bind — it closes
    # the injection surface an unconstrained string would open.
    pattern: str | None = None
    # When True, the value is REDACTED from audit logs (OTP codes, secrets).
    sensitive: bool = False
    # A yes that is asked of someone else ("manager"). The flow waits for it, and
    # it can only arrive through the approval store: not from the user in the
    # conversation, and not from the model (see approvals.py).
    asked_of: str | None = None
    # What the answer must satisfy, beyond its type: ``[condition, sentence]``
    # pairs, each condition reading this field and nothing else
    # (``["amount > 0", "Enter an amount above zero."]``). A value that fails
    # one is refused where a wrong type is refused, with that sentence.
    rules: list[tuple[str, str]] = Field(default_factory=list)
    # Optional id of a pre-authored widget from the Widgets catalog. When set,
    # the host integration renders that widget for this field instead of the
    # auto-generated input. The widget's submit_schema is expected to surface
    # a value the runtime can map back into this field. If the id doesn't
    # resolve, integrations fall back to the auto-generated widget.
    widget: str | None = None
    # Conditional / dynamic fields (Feature 4)
    depends_on: str | None = None  # name of another field this field depends on
    visible_when: dict[str, Any] | None = None  # e.g. {"room_type": "executive"}

    @model_validator(mode="after")
    def _enum_needs_values(self) -> "FieldSpec":
        if self.type == "enum" and not self.values:
            raise ValueError("type=enum requires non-empty `values`")
        return self


def option_label(value: str) -> str:
    """The words an enum value is shown as: ``wrong_item`` -> "Wrong Item".

    A value written in capitals is a code and stays as written ("JFK", not
    "Jfk"). The dropdown and every refusal use this, so the two always match.
    """
    text = str(value)
    return text if text.isupper() else text.replace("_", " ").title()


class FieldValidationError(ValueError):
    """Raised when a value doesn't satisfy its FieldSpec. The message is
    user-safe — surface it directly in the assistant prompt."""

    def __init__(self, field_name: str, value: Any, reason: str):
        self.field_name = field_name
        self.value = value
        self.reason = reason
        super().__init__(f"field {field_name!r}: {reason} (got {value!r})")


# Conservative numeric normalization: accept the money/number shapes real
# users actually type — "$3,000", "3,000", "2,400.50", "5k", "1.2m" — and
# nothing fuzzier. Natural language ("a hundred bucks") stays the interpreter
# LLM's job, and anything unparseable raises so the runtime re-asks instead of
# guessing.
_NUM_RE = re.compile(
    r"^\s*([+-]?)\s*[$€£¥₹]?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmMbB]?)\s*$"
)
_MAGNITUDE = {"": 1.0, "k": 1e3, "m": 1e6, "b": 1e9}


def parse_number(value: Any) -> float:
    """Parse a user-typed number into a float, tolerating currency symbols,
    thousands separators, and k/m/b magnitude suffixes. Raises ValueError on
    anything it can't unambiguously parse."""
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        raise ValueError("boolean is not a number")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"cannot parse {value!r} as a number")
    m = _NUM_RE.match(value)
    if not m:
        raise ValueError(f"cannot parse {value!r} as a number")
    sign, digits, suffix = m.groups()
    num = float(digits.replace(",", "")) * _MAGNITUDE[suffix.lower()]
    return -num if sign == "-" else num


def coerce_and_validate(spec: FieldSpec, value: Any) -> Any:
    """Coerce `value` to the spec's type and check constraints.

    Returns the coerced value on success; raises FieldValidationError otherwise.
    Coercions are conservative — we don't try to be clever about parsing
    natural-language ("a hundred bucks" -> 100). That's the interpreter LLM's
    job, upstream of here.
    """
    if value is None:
        if spec.required:
            raise FieldValidationError("?", value, "value is required")
        return None

    try:
        if spec.type == "string":
            coerced: Any = str(value)
            # Continue on an empty box is not an answer. Stored as "", it let a
            # required step ("name of the approving manager") pass with nobody named.
            if spec.required and not coerced.strip():
                raise FieldValidationError("?", value, "value is required")
        elif spec.type == "number":
            coerced = parse_number(value)
            # NaN and infinities satisfy every min:/max: comparison (all
            # comparisons against NaN are False), which turned a threshold gate
            # OFF for an amount no human wrote. Reject them at the type
            # boundary so ctx.set() and any direct caller fail closed too.
            if not math.isfinite(coerced):
                raise ValueError(f"expected a finite number, got {value!r}")
        elif spec.type == "integer":
            num = parse_number(value)
            if not math.isfinite(num):
                raise ValueError(f"expected a finite number, got {value!r}")
            if num != int(num):
                raise ValueError(f"expected a whole number, got {value!r}")
            coerced = int(num)
        elif spec.type == "boolean":
            if isinstance(value, bool):
                coerced = value
            elif isinstance(value, str):
                low = value.strip().lower()
                if low in {"true", "yes", "y", "1"}:
                    coerced = True
                elif low in {"false", "no", "n", "0"}:
                    coerced = False
                else:
                    raise ValueError(f"cannot parse {value!r} as boolean")
            else:
                coerced = bool(value)
        elif spec.type == "enum":
            coerced = str(value)
            allowed = spec.values or []
            if coerced not in allowed:
                # A refusal names the options by their labels ("Wrong Item"),
                # so an answer that sends a label back is that option.
                said = coerced.strip().casefold()
                same = [v for v in allowed
                        if v.casefold() == said.replace(" ", "_") or option_label(v).casefold() == said]
                if len(same) == 1:
                    coerced = same[0]
            if coerced not in allowed:
                raise FieldValidationError(
                    "?", value, f"must be one of {allowed!r}"
                )
        else:  # "any"
            coerced = value
    except FieldValidationError:
        raise
    except (ValueError, TypeError) as e:
        raise FieldValidationError("?", value, str(e)) from e

    if spec.type in {"number", "integer"} and isinstance(coerced, (int, float)):
        if spec.min is not None and coerced < spec.min:
            raise FieldValidationError("?", value, f"must be >= {spec.min}")
        if spec.max is not None and coerced > spec.max:
            raise FieldValidationError("?", value, f"must be <= {spec.max}")

    if spec.type == "string" and spec.pattern is not None and isinstance(coerced, str):
        if re.fullmatch(spec.pattern, coerced) is None:
            raise FieldValidationError("?", value, f"must match pattern {spec.pattern!r}")

    return coerced

"""Field coercion + validation."""
from __future__ import annotations

import pytest

from aigenos_flow.fields import (
    FieldSpec,
    FieldValidationError,
    coerce_and_validate,
    parse_number,
)


def test_number_coerces_string():
    spec = FieldSpec(type="number")
    assert coerce_and_validate(spec, "42.5") == 42.5


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("3000", 3000.0),
        ("3,000", 3000.0),
        ("$3,000", 3000.0),
        ("$2,400.50", 2400.5),
        ("  $1,000 ", 1000.0),
        ("5k", 5000.0),
        ("2.5K", 2500.0),
        ("1.2m", 1_200_000.0),
        ("-50", -50.0),
        ("£99", 99.0),
    ],
)
def test_number_normalizes_messy_money(raw, expected):
    # Real users type currency symbols, commas, and k/m suffixes. The worst
    # outcome of NOT handling these was an infinite re-ask loop in chat.
    assert coerce_and_validate(FieldSpec(type="number"), raw) == expected


@pytest.mark.parametrize("raw,expected", [("3,000", 3000), ("5k", 5000), ("$1,200", 1200)])
def test_integer_normalizes(raw, expected):
    assert coerce_and_validate(FieldSpec(type="integer"), raw) == expected


def test_integer_rejects_non_integral():
    # "3.5" into an integer field must re-ask, never silently truncate.
    with pytest.raises(FieldValidationError):
        coerce_and_validate(FieldSpec(type="integer"), "3.5")


@pytest.mark.parametrize("raw", ["a hundred bucks", "abc", "", "1e3", "$", "twelve"])
def test_fuzzy_numbers_raise_not_guess(raw):
    # Anything we can't unambiguously parse must raise (→ runtime re-asks),
    # NOT coerce to a wrong value. This is the safety-relevant contract.
    with pytest.raises(FieldValidationError):
        coerce_and_validate(FieldSpec(type="number"), raw)


def test_min_max_apply_after_normalization():
    spec = FieldSpec(type="number", min=0, max=1000)
    assert coerce_and_validate(spec, "$500") == 500.0
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, "$3,000")  # over max even though it parses


def test_parse_number_rejects_bool():
    # bool is an int subclass; a yes/no must never read as the number 1/0.
    with pytest.raises(ValueError):
        parse_number(True)


def test_number_min_max():
    spec = FieldSpec(type="number", min=0, max=100)
    assert coerce_and_validate(spec, 50) == 50.0
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, -1)
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, 101)


def test_enum_membership():
    spec = FieldSpec(type="enum", values=["red", "green", "blue"])
    assert coerce_and_validate(spec, "red") == "red"
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, "purple")


def test_boolean_strings():
    spec = FieldSpec(type="boolean")
    assert coerce_and_validate(spec, "yes") is True
    assert coerce_and_validate(spec, "no") is False
    assert coerce_and_validate(spec, True) is True
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, "maybe")


def test_optional_accepts_none():
    spec = FieldSpec(type="string", required=False)
    assert coerce_and_validate(spec, None) is None


def test_required_rejects_none():
    spec = FieldSpec(type="string", required=True)
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, None)


def test_enum_requires_values_list():
    with pytest.raises(Exception):  # ValidationError from Pydantic
        FieldSpec(type="enum")

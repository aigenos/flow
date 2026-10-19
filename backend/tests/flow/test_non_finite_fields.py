"""NaN and infinity must not reach a typed numeric field.

Every comparison against NaN is False, so a NaN amount satisfied `min:` AND
turned a `>= threshold` gate off — a transfer with no verification code
(audit gate-bypass-3). The runtime rejects it on the submission path; this
pins the type boundary itself, so ctx.set() and any direct caller of
coerce_and_validate fail closed too.
"""
from __future__ import annotations

import math

import pytest

from aigenos_flow import FieldSpec, FieldValidationError
from aigenos_flow.fields import coerce_and_validate


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan", "inf", "-Infinity"])
@pytest.mark.parametrize("ftype", ["number", "integer"])
def test_non_finite_is_refused(ftype: str, bad):
    spec = FieldSpec(type=ftype, min=0.01)
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, bad)


def test_a_nan_amount_cannot_satisfy_a_minimum():
    """The specific shape of the bypass: NaN passing `min: 0.01`."""
    spec = FieldSpec(type="number", min=0.01)
    assert not (float("nan") >= 0.01)          # why the old check let it through
    with pytest.raises(FieldValidationError):
        coerce_and_validate(spec, float("nan"))


def test_finite_numbers_still_pass():
    spec = FieldSpec(type="number", min=0.01, max=1e9)
    assert coerce_and_validate(spec, 5000) == 5000.0
    assert coerce_and_validate(spec, "5000.50") == 5000.50
    assert math.isfinite(coerce_and_validate(spec, 0.01))
    assert coerce_and_validate(FieldSpec(type="integer"), "42") == 42

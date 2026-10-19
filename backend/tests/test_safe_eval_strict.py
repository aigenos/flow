"""Pin-tests for Property 4 (fail-closed evaluation) — engine/safe_eval.py.

The declarative runtime's gates and computed values evaluate through
``eval_strict``: any free variable not bound in the environment must RAISE
(blocking the gated path) rather than resolve to a permissive default. These
tests pin that contract directly, independent of the skills loader that also
relies on it.
"""
import pytest

from aigenos_flow.engine.safe_eval import (
    UnboundNameError,
    UnsafeExpressionError,
    eval_strict,
    free_vars,
    safe_eval,
)


# --------------------------------------------------------------------- free_vars
def test_free_vars_finds_every_name():
    assert free_vars("amount >= 1000 and currency == 'USD'") == {"amount", "currency"}


def test_free_vars_of_pure_literal_is_empty():
    assert free_vars("1000 > 500") == set()


def test_free_vars_sees_names_inside_subscripts_and_boolops():
    assert free_vars("fields['a'] or (limit > cap)") == {"fields", "limit", "cap"}


# -------------------------------------------------------------------- eval_strict
def test_eval_strict_evaluates_when_fully_bound():
    assert eval_strict("amount >= 1000", {"amount": 2500}) is True
    assert eval_strict("amount >= 1000", {"amount": 500}) is False


def test_eval_strict_raises_on_unbound_name():
    with pytest.raises(UnboundNameError, match="requires_2fa"):
        eval_strict("requires_2fa", {})


def test_eval_strict_names_every_missing_binding():
    with pytest.raises(UnboundNameError, match=r"amount.*limit|limit.*amount"):
        eval_strict("amount > limit", {})


def test_eval_strict_fail_closed_vs_safe_eval_fail_open():
    """The exact bug class P4 exists to kill: a typo'd gate name.

    ``safe_eval`` resolves the unknown name to None -> falsy -> a security
    gate silently passes as 'not required'. ``eval_strict`` raises instead.
    """
    typod = "require_2fa"  # declared name is requires_2fa
    assert not safe_eval(typod, {"requires_2fa": True})  # fail-open: falsy(!)
    with pytest.raises(UnboundNameError):
        eval_strict(typod, {"requires_2fa": True})       # fail-closed: raises


def test_eval_strict_none_valued_binding_comparison_raises_not_passes():
    # A declared-but-unfilled field (None) is a legitimate binding — no
    # UnboundNameError — but comparing it can never SILENTLY pass a gate:
    # Python's None >= int raises TypeError, which the declarative layer
    # catches for eager `compute:` seeding (value becomes None -> falsy gate,
    # ask-first ordering re-computes after the field fills). Either way the
    # gated effect stays unreachable.
    with pytest.raises(TypeError):
        eval_strict("amount >= 1000", {"amount": None})


def test_unbound_name_error_is_an_unsafe_expression_error():
    # Callers that catch the broad class also catch the strict one.
    assert issubclass(UnboundNameError, UnsafeExpressionError)


def test_eval_strict_still_rejects_unsafe_syntax():
    with pytest.raises(UnsafeExpressionError):
        eval_strict("().__class__", {})
    with pytest.raises(UnsafeExpressionError):
        eval_strict("open('x')", {"open": "shadowed"})

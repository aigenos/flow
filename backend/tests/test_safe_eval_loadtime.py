"""The loader's expression whitelist must match what the evaluator can run.

``skills._validate_expr`` rejects, at load time, any expression the runtime
evaluator would refuse mid-conversation. If the two lists drift apart the
loader either rejects a working expression or — much worse — lets one through
that crashes on the first user turn, which is the bug this pairing exists to
prevent (a documented `compute:` example shipped broken exactly that way).
"""
from __future__ import annotations

import pytest

from aigenos_flow.engine.safe_eval import UnsafeExpressionError, eval_strict
from aigenos_flow.skills import _validate_expr

ENV = {
    "amount": 1500.0,
    "attendees": 12,
    "per_head": 150.0,
    "confirm": True,
    "status": "approved",
    "result": {"valid": True},
    "rows": [1, 2],
}

EXPRESSIONS = [
    # things a gate legitimately says
    "confirm",
    "not confirm",
    "amount >= 1000",
    "amount >= 1000 and confirm",
    "status in ('approved', 'pending')",
    "result.valid",
    "result['valid']",
    "-amount < 0",
    "rows",
    "True",
    "'usd' == 'usd'",
    # things the evaluator refuses
    "attendees * per_head",
    "amount + 1 > 2",
    "(lambda: True)()",
    "amount.__class__",
    "f'{amount}' == '1500'",
    "[x for x in rows]",
    "len(rows) > 0",
]


@pytest.mark.parametrize("expr", EXPRESSIONS)
def test_loader_accepts_exactly_what_the_evaluator_can_run(expr):
    try:
        eval_strict(expr, ENV)
    except UnsafeExpressionError:
        runtime_ok = False
    else:
        runtime_ok = True

    try:
        _validate_expr(expr, skill="s", where="compute 'g'")
    except ValueError:
        load_ok = False
    else:
        load_ok = True

    assert load_ok == runtime_ok, (
        f"{expr!r}: loader {'accepts' if load_ok else 'rejects'} it but the "
        f"evaluator {'can' if runtime_ok else 'cannot'} run it"
    )


def test_rejection_names_the_skill_and_the_block():
    with pytest.raises(ValueError) as e:
        _validate_expr("attendees * per_head", skill="plan-team-offsite", where="compute 'total'")
    msg = str(e.value)
    assert "plan-team-offsite" in msg and "compute 'total'" in msg and "BinOp" in msg


def test_syntax_error_is_a_teaching_error_not_a_bare_syntaxerror():
    with pytest.raises(ValueError, match="is not a valid expression"):
        _validate_expr("amount >", skill="s", where="compute 'g'")

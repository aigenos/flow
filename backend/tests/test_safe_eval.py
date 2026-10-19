"""safe_eval: the branch-condition evaluator must run the YAML grammar we
document and refuse everything that smells like an escape."""
from __future__ import annotations

import pytest

from aigenos_flow.engine.safe_eval import UnsafeExpressionError, safe_eval


# ---------------------------------------------------------------------------
# The documented grammar works
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "state", "expected"),
    [
        # the exact shape used by the bundled refund_flow.yaml
        ("confirmation['confirmed'] == True", {"confirmation": {"confirmed": True}}, True),
        ("confirmation['confirmed'] == True", {"confirmation": {"confirmed": False}}, False),
        ("amount > 1000", {"amount": 5000}, True),
        ("amount > 1000", {"amount": 500}, False),
        ("amount >= 10 and amount <= 100", {"amount": 50}, True),
        ("status in ('approved', 'pending')", {"status": "approved"}, True),
        ("status not in ('approved', 'pending')", {"status": "rejected"}, True),
        ("not items", {"items": []}, True),
        ("not items", {"items": [1]}, False),
        ("a == 1 or b == 2", {"a": 0, "b": 2}, True),
        ("amount > -5", {"amount": 0}, True),
        ("1 < amount < 10", {"amount": 5}, True),  # chained comparison
        ("flag is None", {}, True),  # missing name -> None
        ("user['tier'] == 'gold'", {"user": {"tier": "gold"}}, True),
    ],
)
def test_grammar(expr, state, expected):
    assert safe_eval(expr, state) == expected


def test_missing_key_is_none_not_error():
    # condition on a not-yet-collected field is falsy, not a crash
    assert safe_eval("confirmation['confirmed'] == True", {}) is False
    assert safe_eval("user['missing'] == 'x'", {"user": {}}) is False


# ---------------------------------------------------------------------------
# Escapes are refused, not executed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        # the classic __class__-chain sandbox escape eval() allowed
        "x.__class__.__bases__[0].__subclasses__()",
        "().__class__",
        "x.__init__",
        # calls of any kind
        "open('/etc/passwd')",
        "__import__('os').system('whoami')",
        "min(1, 2)",
        # other non-condition syntax
        "[y for y in x]",
        "lambda: 1",
        "f'{x}'",
        "x := 5",
    ],
)
def test_unsafe_rejected(expr):
    with pytest.raises((UnsafeExpressionError, SyntaxError)):
        safe_eval(expr, {"x": {}})


def test_statements_rejected():
    with pytest.raises(SyntaxError):
        safe_eval("import os", {})


def test_runtime_skips_bad_branch():
    """_pick_next_branch must treat an unsafe/broken condition as
    non-matching and fall through to the default branch."""
    from aigenos_flow.engine.runtime import _pick_next_branch
    from aigenos_flow.schemas.workflow import WorkflowNode

    node = WorkflowNode(
        id="b",
        type="branch",
        branches=[
            {"if": "x.__class__ == 1", "next": "evil"},
            {"next": "default"},
        ],
    )
    assert _pick_next_branch(node, {"x": {}}) == "default"

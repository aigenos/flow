"""Safe evaluation of workflow branch conditions.

Replaces ``eval()`` for the ``if:`` expressions in workflow YAML. Branch
conditions need exactly this much Python and no more:

    confirmation['confirmed'] == True
    amount > 1000 and currency == 'USD'
    status in ('approved', 'pending')
    not items

The evaluator walks the AST and executes only a whitelisted node set:
literals, state-variable names, dict/list subscripts, comparisons, boolean
ops, and unary not/minus. Everything else — attribute access (the classic
``__class__``-chain sandbox escape), calls, comprehensions, lambdas,
f-strings — raises ``UnsafeExpressionError`` instead of executing.
"""
from __future__ import annotations

import ast
import math
from typing import Any


class UnsafeExpressionError(ValueError):
    """The expression uses syntax outside the branch-condition whitelist."""


class NonFiniteOperandError(UnsafeExpressionError):
    """A comparison was handed NaN or an infinity.

    IEEE-754 makes every ordering comparison against NaN False, which turns a
    threshold gate inside out: ``nan >= 1000`` is False, so a "needs 2FA above
    $1000" check silently decides no code is needed. There is no safe answer to
    such a comparison, so the evaluator refuses it instead of returning a value
    a gate would read as "allowed". Non-finite numbers are rejected at the field
    boundary too (``runtime._write_field``); this is the second line.
    """


_ALLOWED_CMP = {
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
}


def is_non_finite(value: Any) -> bool:
    """True for a float NaN / +inf / -inf. Bools and ints are always finite."""
    return isinstance(value, float) and not math.isfinite(value)


def safe_eval(expression: str, state: dict[str, Any]) -> Any:
    """Evaluate a branch condition against workflow state.

    Names resolve to ``state`` keys (missing names resolve to ``None`` so a
    condition on a not-yet-collected field is falsy rather than an error).
    Raises ``UnsafeExpressionError`` for any syntax outside the whitelist
    and ``SyntaxError`` for unparseable input.
    """
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree.body, state)


def _eval_node(node: ast.AST, state: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return state.get(node.id)

    if isinstance(node, ast.Subscript):
        container = _eval_node(node.value, state)
        key = _eval_node(node.slice, state)
        try:
            return container[key]
        except (KeyError, IndexError, TypeError):
            return None

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, state)
        for op, comparator in zip(node.ops, node.comparators):
            if type(op) not in _ALLOWED_CMP:
                raise UnsafeExpressionError(
                    f"comparison operator {type(op).__name__} not allowed"
                )
            right = _eval_node(comparator, state)
            if is_non_finite(left) or is_non_finite(right):
                raise NonFiniteOperandError(
                    "comparison operand is not a finite number "
                    f"({left!r} {type(op).__name__} {right!r}) — a gate cannot be "
                    "evaluated against NaN or an infinity"
                )
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.In):
                ok = left in right
            elif isinstance(op, ast.NotIn):
                ok = left not in right
            elif isinstance(op, ast.Is):
                ok = left is right
            else:  # ast.IsNot
                ok = left is not right
            if not ok:
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for value in node.values:
                result = _eval_node(value, state)
                if not result:
                    return result
            return result
        # ast.Or
        for value in node.values:
            result = _eval_node(value, state)
            if result:
                return result
        return result

    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not _eval_node(node.operand, state)
        if isinstance(node.op, ast.USub):
            operand = _eval_node(node.operand, state)
            if isinstance(operand, (int, float)) and not isinstance(operand, bool):
                return -operand
            raise UnsafeExpressionError("unary minus on non-number")
        raise UnsafeExpressionError(
            f"unary operator {type(node.op).__name__} not allowed"
        )

    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_node(item, state) for item in node.elts]

    if isinstance(node, ast.Attribute):
        # Dotted access is DICT-KEY lookup only: `result.valid` means
        # result["valid"] when result is a dict. It is NEVER Python attribute
        # access — getattr on an arbitrary object is the classic
        # ``__class__``-chain sandbox escape. Dunder names are rejected
        # outright (defense in depth), and any non-dict base raises.
        if node.attr.startswith("__"):
            raise UnsafeExpressionError(
                f"dunder access {node.attr!r} is not allowed in a branch condition"
            )
        container = _eval_node(node.value, state)
        if isinstance(container, dict):
            return container.get(node.attr)
        if container is None:
            return None
        raise UnsafeExpressionError(
            f"dotted access {node.attr!r} is only allowed on dict values "
            f"(got {type(container).__name__})"
        )

    # Everything else: Call, comprehensions, f-strings, lambdas, starred,
    # slices-with-steps, walrus...
    raise UnsafeExpressionError(
        f"{type(node).__name__} is not allowed in a branch condition"
    )


class UnboundNameError(UnsafeExpressionError):
    """A free variable in the expression is not present in the namespace.

    Distinct from the generic UnsafeExpressionError so a fail-closed gate can
    be told apart from a syntactically-forbidden expression.
    """


def free_vars(expression: str) -> set[str]:
    """The set of plain names an expression references.

    Used at skill-load time to prove every name in a ``when:`` / ``compute:`` /
    ``requires:`` / ``{{...}}`` expression is bound to a declared field or
    computed value. A typo'd gate name (``require_2fa`` vs ``requires_2fa``) is
    then a LOAD ERROR, never a runtime ``None`` that silently makes a gate falsy.
    """
    tree = ast.parse(expression, mode="eval")
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}


def eval_strict(expression: str, env: dict[str, Any]) -> Any:
    """Evaluate like ``safe_eval`` but FAIL CLOSED.

    Any free variable not present in ``env`` raises ``UnboundNameError`` instead
    of resolving to ``None``. Gates and computed values use this so an unbound
    name can never silently make a security gate evaluate to a falsy value.
    """
    missing = free_vars(expression) - set(env)
    if missing:
        raise UnboundNameError(
            f"expression {expression!r} references unbound name(s): {sorted(missing)}"
        )
    return safe_eval(expression, env)

"""Tools for your skills. Every public function in this folder is a tool a step can name.

A tool receives the answers whose names match its parameters, so nothing is
declared twice: `amount: float` is why the chat shows a number box.
"""
from __future__ import annotations

import secrets


def file_expense(amount: float, category: str, justification: str, manager_approval: str = "") -> dict:
    """File the expense for reimbursement."""
    # A stub for your real expense system: replace the body with the API call.
    #
    # This function is not marked @readonly, so the engine treats it as one
    # that changes something. Three things follow, and none of them is written
    # in the skill:
    #   * a skill that names it does not load without a `confirm:` above it;
    #   * it runs at most once per conversation, however many turns follow;
    #   * every run is recorded with its inputs.
    #
    # A function that only looks something up says so, and may then run
    # whenever a step needs its result:
    #
    #     from aigenos_flow import readonly
    #
    #     @readonly
    #     def spending_limit(category: str) -> dict:
    #         return {"over": False}
    return {"id": f"EXP-{secrets.token_hex(3).upper()}"}

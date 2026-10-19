"""Tools for expense-report. They sit beside the skill, so only it can name them.

Replace the bodies with calls to your expense system. The signatures are the
contract: `lines` is the list the skill's `repeat:` collected, one record per
expense ({"item", "category", "amount"}).
"""
from __future__ import annotations

from aigenos_flow import readonly

#: Every report submitted in this process (a stand-in for the expense system).
SUBMITTED: list = []


@readonly
def report_total(lines: list) -> dict:
    """The report's total, and whether a manager has to approve it."""
    total = round(sum(float(line.get("amount") or 0) for line in lines or []), 2)
    return {"total": f"{total:,.2f}", "needs_approval": total >= 500}


def submit_report(report_for: str, lines: list) -> dict:
    """Submit the report for reimbursement."""
    SUBMITTED.append({"report_for": report_for, "lines": [dict(line) for line in lines]})
    count = len(lines)
    return {
        "reference": f"EXP-{len(SUBMITTED):04d}",
        "count": count,
        "expenses": f"{count} expense" + ("" if count == 1 else "s"),
    }

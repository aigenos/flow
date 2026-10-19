---
name: expense-report
description: >-
  File an expense report, one line per expense. Use when the user wants to file,
  submit or claim expenses, or to report several receipts at once.
license: Apache-2.0

# A repeat collects the lines; a lookup adds them up; a manager approves a
# report of $500 or more. The tools are in scripts.py beside this file.
steps:
  - ask: { report_for: "What is this report for? (a trip or a project)" }

  - repeat: { lines: "Add another expense?" }
    steps:
      - ask: { item: "What was the expense?" }
      - ask: { category: "Which category?" }
        choices: [travel, meals, lodging, other]
      - ask: { amount: "How much was it, in USD?" }
        check: { amount > 0: "Enter an amount above zero." }

  - say: "{{lines}}"
    title: Expenses
  - say: "Total: ${{report_total.total}}."
  - confirm: Submit this report?
  - confirm: Approve this expense report?
    by: manager
    when: report_total.needs_approval
  - do: submit_report
  - say: "Submitted {{submit_report.expenses}}. Reference {{submit_report.reference}}."
---

Help the user file an expense report: what it is for, then each expense in
turn. The steps above decide what is asked and when the report is submitted;
this text is only context for understanding what the user says.

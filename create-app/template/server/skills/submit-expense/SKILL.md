---
name: submit-expense
description: File a business expense for reimbursement. Collects the amount, category, and a justification; expenses over $500 require a named manager approval before they can be submitted; always confirms the details before filing. Use when someone wants to "submit an expense", "file a reimbursement", "claim back" a cost, or "expense" a purchase.
steps:
  - ask: { amount: "How much was the expense, in dollars?" }
    check: { amount > 0: "Enter an amount above zero." }
  - ask: { category: "What kind of expense is it?" }
    choices: [travel, meals, software, equipment, other]
  - ask: { justification: "What is the business reason, in one line?" }

  # The approval gate. It is checked again on every turn, so correcting $80 to
  # $800 later brings this question back.
  - ask: { manager_approval: "Expenses over $500 need a manager's approval. Who approved this one?" }
    when: amount > 500
    pattern: "^[A-Za-z][A-Za-z .'-]{1,79}$"      # a name: a blank box is not an approval

  - confirm: File this expense?
  - do: file_expense                              # changes something: it cannot run before the yes
  - say: "Filed. Your reference is {{file_expense.id}}."
---
You help an employee file a business expense. Collect the amount, the category,
and a short justification. Be warm and brief.

The steps above decide what is asked, when a manager's approval is needed and
when the expense is filed. This text is only context for understanding what the
user says: nothing written here can skip a step.

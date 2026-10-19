"""Change a skill's steps from a sentence.

    propose(skill_md, "ask who approved it when the amount is over 500", llm, check=loads)

The model writes the change. It does not decide whether the change is valid:
what it returns is put through the same loader as a file written by hand, and
a file the loader refuses goes back to the model, once, with the loader's own
sentence. The caller gets a proposal and the loader's verdict on it. Nothing
is saved here, and nothing is trusted here: a proposal that names a tool that
does not exist, or runs one that changes something with no yes above it, comes
back marked as not loading, exactly as it would from a person.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterable

#: The language, as the model is told it. Kept beside the loader on purpose: a
#: word added to `shortform` and not here is a word the editor will never use.
LANGUAGE = """\
A skill's `steps:` is a YAML list. Each step is ONE of five kinds.

- ask: { name: "Question?" }        get an answer from the user and keep it under `name`
    choices: [a, b]                 a fixed list to choose from
    from: tool.rows                 rows returned by a lookup tool, to pick one from
    type: text | number | whole number | yes/no | secret     only when it cannot be worked out
    pattern: '^[0-9]{6}$'           the shape a typed answer must have
    check: { CONDITION: "What to say when it does not hold." }   refuse the answer and ask again
- say: "Text with {{name}} or {{tool.key}}"     tell the user something
    title: "Card title"             a value that is a record or a list is shown as a card
    widget: widget_id               a designed widget shown below the sentence (not answered)
- do: tool_name                     run a tool, or another skill by its name
    with: { input: value }          pass a value the steps did not ask for
    as: other_name                  another name for the result (to use one tool twice)
    else: "What to say if that skill did not finish."   only when `do:` names a skill
- confirm: "Question?"              get a yes; "no" stops the flow
    by: manager                     the yes is someone else's (a manager, finance), not the user's
- chat: "Ask me anything about it."  the LAST step: answer the user's questions
    tools: [lookup_a, lookup_b]     the lookups it may call; inputs named like an answer are fixed to it

Any step may carry `when: CONDITION`, to run only if it holds. Several steps under one condition:

- when: CONDITION
  steps:
    - ...

A list of items (travellers, line items), the same questions once per item:

- repeat: { travellers: "Add another traveller?" }
  steps:
    - ask: { traveller_name: "Name?" }   only `ask:` steps inside; after it, read `travellers`

A CONDITION compares: names, numbers, 'quoted text', == != < <= > >=, and / or / not, parentheses.
A name is an answer asked ABOVE the step, or a tool's result written tool.key. No arithmetic.

What the loader enforces. A file that breaks one of these is refused:
1. A tool that changes something needs a `confirm:` above it on every path that reaches it.
2. A lookup tool needs no step: write tool.key where its result is needed and it runs by itself.
3. A tool receives the answers whose names match its inputs. Name each answer after the input it
   fills, and ask every required input above the step that uses the tool.
4. Use only the words above, and only the tools and skills listed. Do not invent a key or a tool.
5. A text answer that may be left blank ends its question with "(optional)".
6. A `confirm:` question is fixed text, with no {{...}} in it. Say what is about to happen in a
   `say:` step just before it.
"""

_FORMAT = """\
Return the complete new `steps:` block and nothing else of the file, inside one ```yaml fence,
starting with the line `steps:`. Keep every step you were not asked to change exactly as it is,
comments included. After the fence write one line starting with `CHANGED:` that says, in plain
words, what you changed.

Change nothing, and write one line starting with `CANNOT:` that says why, in either of two cases:
- the instruction needs a tool or a skill that is not listed;
- the instruction asks for something the loader refuses, such as running a tool that changes
  something without a confirmation above it. Name the rule.
"""

_STEPS_LINE = re.compile(r"steps:\s*(#.*)?$")
_TOP_KEY = re.compile(r"[A-Za-z_][\w-]*:")


def _frame(skill_md: str) -> tuple[list[str], int, int, int]:
    """The file's lines, and where its `steps:` block starts and stops inside the frontmatter."""
    lines = skill_md.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("a SKILL.md starts with `---`")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("the frontmatter is not closed with `---`") from None
    start = next((i for i in range(1, end) if _STEPS_LINE.match(lines[i])), None)
    if start is None:
        return lines, end, end, end                       # no steps yet: they go last
    stop = next((i for i in range(start + 1, end) if _TOP_KEY.match(lines[i])), end)
    while stop > start + 1 and not lines[stop - 1].strip():
        stop -= 1                                          # a blank line before the next key is not a step
    return lines, start, stop, end


def current_steps(skill_md: str) -> str:
    lines, start, stop, _ = _frame(skill_md)
    return "\n".join(lines[start:stop])


def with_steps(skill_md: str, steps: str) -> str:
    """The same file with its `steps:` block replaced. Everything else is kept as written."""
    lines, start, stop, _ = _frame(skill_md)
    block = steps.strip("\n").splitlines()
    out = "\n".join([*lines[:start], *block, *lines[stop:]])
    return out + ("\n" if skill_md.endswith("\n") else "")


def _tools_text(tools: Iterable[dict[str, Any]]) -> str:
    rows = []
    for tool in tools:
        inputs = ", ".join(
            f"{p['name']}: {p.get('type', 'text')}" + ("" if p.get("required", True) else " (optional)")
            for p in tool.get("inputs") or []
        ) or ("every answer asked above it" if tool.get("takes_everything") else "nothing")
        kind = "CHANGES SOMETHING" if tool.get("changes_something") else "lookup"
        about = f" - {tool['description']}" if tool.get("description") else ""
        rows.append(f"- {tool['name']}({inputs})  [{kind}]{about}")
    return "\n".join(rows) or "(none)"


def _text_of(result: Any) -> str:
    content = getattr(result, "content", result)
    if isinstance(content, list):  # some providers answer in blocks
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def _line(text: str, label: str) -> str:
    found = re.search(rf"^\s*{label}\s*(.+)$", text, flags=re.M)
    return found.group(1).strip() if found else ""


def _fenced_steps(text: str) -> str:
    """The steps block out of the model's reply, as the lines of a `steps:` key."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    found = re.search(r"```(?:ya?ml)?[ \t]*\n(.*?)```", text, flags=re.S)
    if not found:
        return ""
    block = found.group(1).rstrip()
    lines = [line.rstrip() for line in block.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return ""
    if _STEPS_LINE.match(lines[0]):
        return "\n".join(lines)
    if lines[0].lstrip().startswith("- "):                # the list alone: put it under its key
        pad = len(lines[0]) - len(lines[0].lstrip())
        return "\n".join(["steps:", *("  " + line[pad:] if line.strip() else "" for line in lines)])
    return ""


def propose(
    skill_md: str,
    instruction: str,
    llm: Any,
    *,
    check: Callable[[str], dict[str, Any]],
    tools: Iterable[dict[str, Any]] = (),
    skills: Iterable[str] = (),
    attempts: int = 2,
) -> dict[str, Any]:
    """``skill_md`` changed as ``instruction`` says, and whether the result loads.

    ``check`` is the loader: it takes a SKILL.md and answers ``{"ok": True, ...}``
    or ``{"ok": False, "error", "step"}``. The answer here always carries
    ``skill_md`` (the proposal, or the file unchanged), ``ok`` and ``check``.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    wanted = " ".join(str(instruction or "").split())
    if not wanted:
        raise ValueError("say what to change")
    before = current_steps(skill_md)
    messages: list[Any] = [
        SystemMessage(content=(
            "You edit one workflow written as steps. You change only what you are asked to change.\n\n"
            + LANGUAGE + "\n" + _FORMAT
        )),
        HumanMessage(content=(
            "The tools a step may name:\n" + _tools_text(tools) + "\n\n"
            "Other skills a `do:` step may hand off to: " + (", ".join(sorted(skills)) or "(none)") + "\n\n"
            "The steps as they are now:\n```yaml\n" + (before or "steps: []") + "\n```\n\n"
            "Change them so that: " + wanted
        )),
    ]
    out: dict[str, Any] = {"ok": False, "skill_md": skill_md, "steps": before, "changed": "", "attempts": 0}
    for attempt in range(1, max(1, attempts) + 1):
        reply = _text_of(llm.invoke(messages))
        out["attempts"] = attempt
        steps = _fenced_steps(reply)
        cannot = _line(reply, "CANNOT:")
        if cannot and not steps:
            return {**out, "cannot": cannot, "check": None}
        if not steps:
            problem = "there was no ```yaml fence holding the steps"
        else:
            candidate = with_steps(skill_md, steps)
            verdict = check(candidate)
            out.update(skill_md=candidate, steps=steps, changed=_line(reply, "CHANGED:"), check=verdict)
            if verdict.get("ok"):
                out.pop("error", None)
                return {**out, "ok": True, "unchanged": steps.strip() == before.strip()}
            problem = str(verdict.get("error") or "it did not load")
            out["error"] = problem
        messages += [
            AIMessage(content=reply),
            HumanMessage(content=(
                f"The loader refused that: {problem}\n"
                "Fix exactly that and return the whole steps block again, in the same format. "
                "On the `CHANGED:` line describe everything that differs from the steps you were "
                "first given, not this correction. If the only way to satisfy the loader is to "
                "leave the steps as they were, answer with the `CANNOT:` line instead."
            )),
        ]
    return out

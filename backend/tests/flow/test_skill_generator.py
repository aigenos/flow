"""Regression tests for aigenos_flow.skill_generator.

Focus: the "confirm loop" bug. When the LLM names a confirmation_field but
forgets to declare it in `fields`, the generator must inject a boolean field
so the runtime renders a Yes/No widget instead of a typeless prompt that
re-asks forever.
"""
from __future__ import annotations

import json

from aigenos_flow.skill_generator import (
    generate_skill_files,
    generate_skill_spec,
)


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """Minimal stand-in for a LangChain chat model."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def invoke(self, _messages):  # noqa: ANN001
        return _FakeMessage(json.dumps(self._payload))


# A spec shaped exactly like qwen2.5:14b produced for "take pizza orders":
# confirmation_field is set, but `confirm` is absent from `fields`.
_PIZZA_WITHOUT_CONFIRM = {
    "name": "take-pizza-order",
    "description": "Takes pizza orders by collecting size, toppings, and address, then confirms before submitting.",
    "triggers": ["order pizza", "i want a pizza", "pizza please"],
    "fields": {
        "size": {
            "type": "enum",
            "values": ["small", "medium", "large"],
            "description": "Size of the pizza; map 'big' to large.",
        },
        "toppings": {"type": "string", "description": "Comma-separated toppings."},
        "address": {"type": "string", "description": "Delivery address."},
    },
    "actions": [
        {"name": "submit_order", "purpose": "Submit order", "fake_result": {"order_id": "ORD-1"}}
    ],
    "body_notes": "Show a summary before confirming.",
    "confirmation_field": "confirm",
}


def test_missing_confirm_field_is_injected_as_boolean():
    spec = generate_skill_spec("take pizza orders", _FakeLLM(_PIZZA_WITHOUT_CONFIRM))
    assert "confirm" in spec.fields, "confirm field should be auto-injected"
    assert spec.fields["confirm"].type == "boolean"


def test_generated_files_have_one_confirm_and_the_action_sits_below_it():
    files = generate_skill_files("take pizza orders", _FakeLLM(_PIZZA_WITHOUT_CONFIRM))
    skill_md = files["SKILL.md"]
    # The skill is the one file: steps, no schema block, no Python.
    assert sorted(files) == ["SKILL.md", "name", "spec"]
    assert "steps:" in skill_md and "fields:" not in skill_md and "flow:" not in skill_md
    # The flow asks for the yes exactly once (a Yes/No control, not a typeless
    # prompt that re-asks forever), and the one action comes after it.
    assert skill_md.count("  - confirm:") == 1
    assert skill_md.index("  - confirm:") < skill_md.index("  - do: save_request")


# --------------------------------------------------------------------------- #
# A generated skill must be a COMPLETE workflow.
#
# Reported from the Studio: "book a restaurant" collected four fields and then
# said "Details collected. Bind a managed operation in Canvas to complete this
# workflow." It asked the user "Extract numeric values from phrases like 'party
# of 6'", never asked which restaurant, never confirmed, and booked nothing.
# --------------------------------------------------------------------------- #

_RESTAURANT = {
    "name": "book-restaurant",
    "description": "Books a restaurant table by collecting the restaurant, date, time, party size and seating, then confirming. Use when the user wants to reserve a table or book dinner.",
    "triggers": ["book a table", "reserve a restaurant", "dinner reservation"],
    "fields": {
        "restaurant": {"type": "string", "question": "Which restaurant?",
                       "description": "The restaurant's name and town, verbatim."},
        "date": {"type": "string", "question": "For which date?",
                 "description": "Extract from phrases like 'tomorrow', 'this Friday'."},
        "time": {"type": "string", "question": "At what time?",
                 "description": "Extract from phrases like '7pm', 'at 19:00'."},
        "party_size": {"type": "integer", "min": 1, "max": 20, "question": "For how many people?",
                       "description": "Extract numeric values from phrases like 'party of 6'."},
        "seating": {"type": "enum", "values": ["indoor", "outdoor", "bar", "booth"],
                    "description": "Map 'patio' to outdoor, 'counter' to bar."},
    },
    "actions": [{"name": "book_table", "purpose": "Reserve the table",
                 "done_message": "Your table is booked", "fake_result": {"reservation_id": "R-1"}}],
    "body_notes": "Extract as much as possible from the first message.",
    "confirmation_field": "confirm",
}


def _create(tmp_path, payload=_RESTAURANT):
    from aigenos_flow import FlowAgent
    from aigenos_flow.hosts.test_host import TestHost
    from aigenos_flow.skill_generator import create_skill_in_dir

    skill = create_skill_in_dir(description="book a restaurant", llm=_FakeLLM(payload), skills_dir=tmp_path)
    host = TestHost()
    return skill, FlowAgent.from_dir(tmp_path, host=host), host


def test_a_generated_skill_passes_the_fail_closed_loader(tmp_path):
    skill, agent, _ = _create(tmp_path)
    assert skill.name == "book-restaurant"
    assert agent.list_skills() == ["book-restaurant"]


def test_the_user_is_asked_questions_not_extraction_hints(tmp_path):
    skill, _, _ = _create(tmp_path)
    asked = {name: spec.description for name, spec in skill.fields.items()}
    assert asked["restaurant"] == "Which restaurant?"
    assert asked["party_size"] == "For how many people?"
    assert asked["seating"] == "Which seating?", "a missing question falls back to a plain one"
    for text in asked.values():
        assert "Extract" not in text and "Map " not in text, text
    # The hints are not lost — they are in the body, for the interpreter.
    assert "party of 6" in skill.body


def test_a_generated_skill_runs_to_completion_and_reports_a_reference(tmp_path):
    import json

    _, agent, _ = _create(tmp_path)
    chat = agent.session("book-restaurant").submit(
        restaurant="Tikka Masala, North Brunswick", date="2026-10-05",
        time="20:00", party_size=2, seating="bar",
    )
    said = " | ".join(str(r) for r in chat.replies)
    assert chat.awaiting == "confirm"
    assert "Tikka Masala, North Brunswick" in said and "party size: 2" in said, said

    chat.submit(confirm=True)
    said = " | ".join(str(r) for r in chat.replies)

    assert chat.done
    assert "Your table is booked. Reference: REQ-" in said, said
    assert "Bind a managed operation" not in said
    records = (tmp_path / "book-restaurant" / "records.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    record = json.loads(records[0])
    assert record["restaurant"] == "Tikka Masala, North Brunswick" and record["party_size"] == 2
    assert "confirm" not in record
    assert record["reference"] in said


def test_a_generated_skill_does_nothing_when_the_user_declines(tmp_path):
    _, agent, _ = _create(tmp_path)
    chat = agent.session("book-restaurant").submit(
        restaurant="Tikka Masala", date="2026-10-05", time="20:00", party_size=2, seating="bar",
    )
    chat.submit(confirm=False)

    assert not (tmp_path / "book-restaurant" / "records.jsonl").exists()
    assert "no changes were made" in " | ".join(str(r) for r in chat.replies)


def test_a_model_that_forgets_the_action_still_gets_a_complete_workflow(tmp_path):
    payload = dict(_RESTAURANT, actions=[], confirmation_field=None)
    skill, agent, _ = _create(tmp_path, payload)
    chat = agent.session(skill.name).submit(
        restaurant="X", date="2026-10-05", time="20:00", party_size=2, seating="bar",
    )
    assert chat.awaiting == "confirm", "a confirmation is added even when the model named none"
    chat.submit(confirm=True)
    assert chat.done and "Reference: REQ-" in " | ".join(str(r) for r in chat.replies)


def test_an_action_named_like_a_field_is_not_a_problem(tmp_path):
    """The model's name for the action used to become a Python function, so one
    that matched a field had to be renamed. Nothing is generated from it now."""
    payload = dict(_RESTAURANT)
    payload["actions"] = [{"name": "restaurant", "purpose": "Reserve", "fake_result": {}}]
    skill, _, _ = _create(tmp_path, payload)
    assert skill.name == "book-restaurant"
    assert sorted(f.name for f in (tmp_path / "book-restaurant").iterdir()) == ["SKILL.md"]


def test_a_generated_skill_is_one_file_of_steps(tmp_path):
    skill, _, _ = _create(tmp_path)
    assert skill.short_form
    assert sorted(f.name for f in (tmp_path / "book-restaurant").iterdir()) == ["SKILL.md"]
    md = (tmp_path / "book-restaurant" / "SKILL.md").read_text(encoding="utf-8")
    assert "  - ask: { restaurant: \"Which restaurant?\" }" in md
    assert "    choices: [indoor, outdoor, bar, booth]" in md
    assert "    type: whole number" in md


def test_a_range_becomes_a_rule_with_a_sentence(tmp_path):
    """`min: 1, max: 20` on the party size used to answer a wrong number with a
    validator line. It is a `check:` now, and the refusal is a sentence."""
    _, agent, _ = _create(tmp_path)
    chat = agent.session("book-restaurant").submit(restaurant="X").submit(date="2026-10-05").submit(time="20:00")
    chat.submit(party_size=40)
    assert chat.awaiting == "party_size"
    assert chat.replies[0] == "Party size must be between 1 and 20."
    chat.submit(party_size=4)
    assert chat.awaiting == "seating"


def test_a_question_the_user_may_skip_says_so(tmp_path):
    payload = json.loads(json.dumps(_RESTAURANT))
    payload["fields"]["notes"] = {"type": "string", "required": False,
                                  "question": "Any special requests?", "description": "Free text."}
    skill, agent, _ = _create(tmp_path, payload)
    assert skill.fields["notes"].description == "Any special requests? (optional)"
    assert skill.fields["notes"].required is False
    chat = agent.session("book-restaurant").submit(
        restaurant="X", date="2026-10-05", time="20:00", party_size=2, seating="bar",
    )
    assert chat.awaiting == "notes"
    chat.submit(notes="")
    assert chat.awaiting == "confirm", "a blank optional answer moves on"


def test_replacing_a_skill_from_an_older_version_drops_its_python_file(tmp_path):
    from aigenos_flow.skill_generator import create_skill_in_dir

    old = tmp_path / "book-restaurant"
    old.mkdir()
    (old / "handlers.py").write_text("raise RuntimeError('stale generated code')\n", encoding="utf-8")
    (old / "SKILL.md").write_text("---\nname: book-restaurant\ndescription: old\n---\n", encoding="utf-8")
    create_skill_in_dir(description="x", llm=_FakeLLM(_RESTAURANT), skills_dir=tmp_path, overwrite=True)
    assert sorted(f.name for f in old.iterdir()) == ["SKILL.md"]


def test_non_boolean_confirm_field_is_upgraded():
    payload = dict(_PIZZA_WITHOUT_CONFIRM)
    payload["fields"] = dict(payload["fields"])
    # LLM declared confirm but as a string — still unusable as a Yes/No widget.
    payload["fields"]["confirm"] = {"type": "string", "description": "confirm?"}
    spec = generate_skill_spec("x", _FakeLLM(payload))
    assert spec.fields["confirm"].type == "boolean"


def test_no_confirmation_field_leaves_fields_untouched():
    payload = dict(_PIZZA_WITHOUT_CONFIRM)
    payload["confirmation_field"] = None
    spec = generate_skill_spec("x", _FakeLLM(payload))
    assert "confirm" not in spec.fields


# --------------------------------------------------------------------------- a rule in the description is a rule in the flow
_EXPENSE = {
    "name": "file-expense",
    "description": "Files an expense: amount, merchant and category, and a manager's name for anything over $500. Use when the user wants to file or submit an expense.",
    "triggers": ["file expense", "submit an expense"],
    "fields": {
        "amount": {"type": "number", "question": "How much was it?", "description": "Dollars, e.g. '$45'."},
        "merchant": {"type": "string", "question": "Who was the merchant?", "description": "The vendor's name."},
        "manager_name": {"type": "string", "only_when": "amount > 500",
                         "question": "Who is your manager?", "description": "The approving manager's name."},
        "approval_code": {"type": "string", "secret": True,
                          "question": "What is the approval code?", "description": "A code the user reads out."},
    },
    "actions": [{"name": "submit_expense", "purpose": "Submit the expense", "done_message": "Submitted"}],
    "body_notes": "Collect the details.",
    "confirmation_field": "confirm",
}


def _steps(tmp_path, payload):
    import yaml

    skill, agent, host = _create(tmp_path, payload)
    md = (tmp_path / skill.name / "SKILL.md").read_text(encoding="utf-8")
    return skill, agent, yaml.safe_load(md.split("---")[1])["steps"]


def test_a_condition_in_the_description_becomes_a_guard_on_the_question(tmp_path):
    """"Anything over $500 needs a manager's name" used to be left in the prose
    while the flow asked for the manager every time."""
    skill, agent, steps = _steps(tmp_path, _EXPENSE)
    assert {"ask": {"manager_name": "Who is your manager?"}, "when": "amount > 500"} in steps
    assert {"say": "Manager name: {{manager_name}}.", "when": "amount > 500"} in steps
    # The field the rule reads is user-only, and nobody had to say so.
    assert skill.fields["amount"].must_elicit is True

    small = agent.session("file-expense").submit(amount=120).submit(merchant="Delta")
    assert small.awaiting == "approval_code", "a $120 expense must not ask for a manager"
    large = agent.session("file-expense").submit(amount=900).submit(merchant="Delta")
    assert large.awaiting == "manager_name"
    large.submit(manager_name="Jane Doe").submit(approval_code="A1B2")
    said = " | ".join(large.replies)
    assert "amount: 900" in said and "Manager name: Jane Doe." in said and large.awaiting == "confirm"


def test_a_code_is_a_secret_in_the_file_the_summary_and_the_record(tmp_path):
    import json

    skill, agent, _ = _steps(tmp_path, _EXPENSE)
    assert skill.fields["approval_code"].sensitive is True
    chat = agent.session("file-expense").submit(amount=120).submit(merchant="Delta").submit(approval_code="A1B2")
    assert "A1B2" not in " | ".join(chat.replies) and "approval code" not in " | ".join(chat.replies)
    chat.submit(confirm=True)
    assert chat.done
    record = json.loads((tmp_path / "file-expense" / "records.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert record["amount"] == 120 and "approval_code" not in record


def test_a_condition_the_flow_cannot_run_is_dropped_not_fatal(tmp_path):
    """Unquoted text, a field asked later, a made-up name, code: each would be
    a load error. The skill still loads, and asks that question every time."""
    for bad in ("category == travel", "manager_name != ''", "nobody > 1", "__import__('os')", "amount >"):
        payload = json.loads(json.dumps(_EXPENSE))
        payload["name"] = "file-expense-" + str(abs(hash(bad)) % 10_000)
        payload["fields"]["merchant"]["only_when"] = bad
        _, _, steps = _steps(tmp_path / payload["name"], payload)
        assert {"ask": {"merchant": "Who was the merchant?"}} in steps, bad


def test_a_rule_about_a_yes_or_no_answer_is_kept(tmp_path):
    """"If nobody will be home, ask where to leave the parcel." A model writing
    JSON writes `someone_home == false`, which is not the flow's `False`."""
    from aigenos_flow.skill_generator import GeneratedSkillSpec, _usable_condition

    spec = GeneratedSkillSpec.model_validate(_EXPENSE)
    known = ["someone_home", "plan"]
    assert _usable_condition("someone_home == false", known, spec) == "someone_home == False"
    assert _usable_condition("not someone_home", known, spec) == "not someone_home"
    assert _usable_condition("someone_home == true and plan == 'true'", known, spec) == (
        "someone_home == True and plan == 'true'"  # the quoted word is text and is left alone
    )

    payload = json.loads(json.dumps(_EXPENSE))
    payload["name"] = "schedule-delivery"
    payload["fields"] = {
        "someone_home": {"type": "boolean", "question": "Will someone be home?", "description": "Yes or no."},
        "leave_where": {"type": "string", "only_when": "someone_home == false",
                        "question": "Where should we leave it?", "description": "A place, e.g. 'the porch'."},
    }
    _, agent, steps = _steps(tmp_path, payload)
    assert {"ask": {"leave_where": "Where should we leave it?"}, "when": "someone_home == False"} in steps
    home = agent.session("schedule-delivery").submit(someone_home=True)
    assert home.awaiting == "confirm"
    away = agent.session("schedule-delivery").submit(someone_home=False)
    assert away.awaiting == "leave_where"


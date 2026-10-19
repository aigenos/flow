"""What the user reads must never contradict what the runtime did.

All four cases were found by driving the front-door skill with a live model:

* the model said "the code 'skip' has been entered" one line above the runtime
  refusing that code;
* the refusal showed the end user a regular expression and the field's internal
  name ("I couldn't accept that for verification_code: must match pattern
  '^[0-9]{6}$'");
* the model said "Got it, you want to send $5000" and the flow then asked how
  much to send, because the amount had been dropped as ungrounded;
* the summary read "Send 5000.0 to mom?".

None of them moved money. All of them read as broken.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.declarative import _render
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Reply
from aigenos_flow.runtime import _rejection_text

SKILLS = Path(__file__).resolve().parents[3] / "examples" / "before_after" / "skills_after"

pytestmark = pytest.mark.skipif(not SKILLS.is_dir(), reason="examples/ not present")


def _chat():
    host = TestHost()
    agent = FlowAgent.from_dir(SKILLS, host=host, tools={"bank_transfer": lambda a: {"txn": "T"}})
    return host, agent.session("transfer-funds")


def test_a_reply_claiming_acceptance_is_dropped_when_the_value_is_refused():
    host, chat = _chat()
    host.queue_commands("send $5000 to mom",
                        SetField(field="amount", value=5000),
                        SetField(field="payee", value="mom"))
    chat.user("send $5000 to mom")
    assert chat.awaiting == "code"

    host.queue_commands("the code is skip",
                        Reply(text="Got it. The code 'skip' has been entered."),
                        SetField(field="code", value="skip"))
    chat.user("the code is skip")
    said = " | ".join(str(r) for r in chat.replies)

    assert chat.awaiting == "code"
    assert "has been entered" not in said, said
    assert "doesn't look right" in said, said


def test_the_refusal_shows_no_regex_and_no_internal_field_name():
    host, chat = _chat()
    host.queue_commands("send $5000 to mom",
                        SetField(field="amount", value=5000),
                        SetField(field="payee", value="mom"))
    chat.user("send $5000 to mom")
    host.queue_commands("skip", SetField(field="code", value="skip"))
    chat.user("skip")
    said = " | ".join(str(r) for r in chat.replies)

    assert "^[0-9]" not in said and "pattern" not in said, said
    assert "for code:" not in said and "'code'" not in said, said
    assert "That code doesn't look right" in said, said
    # ...and the flow re-asks in words, from the field's own description.
    assert "6-digit code" in said, said


def test_a_reply_asserting_a_dropped_amount_is_neutralized():
    """The model computes an amount the user never stated. The value is dropped,
    so the sentence announcing it must not survive either."""
    host, chat = _chat()
    host.queue_commands("send the usual to mom",
                        Reply(text="Got it. You want to send $5000 to Mom."),
                        SetField(field="amount", value=5000),
                        SetField(field="payee", value="mom"))
    chat.user("send the usual to mom")
    said = " | ".join(str(r) for r in chat.replies)

    assert chat.awaiting == "amount"
    assert chat.state["fields"].get("amount") is None
    assert "5000" not in said, said


def test_a_truthful_reply_still_reaches_the_user():
    host, chat = _chat()
    host.queue_commands("send $200 to the gym",
                        Reply(text="Sure, a transfer to the gym."),
                        SetField(field="amount", value=200),
                        SetField(field="payee", value="gym"))
    chat.user("send $200 to the gym")
    assert "Sure, a transfer to the gym." in [str(r) for r in chat.replies]


@pytest.mark.parametrize(
    ("value", "shown"),
    [(5000.0, "5000"), (200.0, "200"), (12.5, "12.5"), (12.50, "12.5"),
     (0.1 + 0.2, "0.3"), (1999.99, "1999.99"), ("mom", "mom"), (True, "True"),
     (None, "")],
)
def test_user_facing_text_renders_numbers_the_way_people_write_them(value, shown):
    assert _render("{{v}}", {"v": value}) == shown


def test_rejection_text_is_written_for_a_person():
    text = _rejection_text("verification_code", "must match pattern '^[0-9]{6}$'")
    assert "^" not in text and "pattern" not in text
    assert "verification_code" not in text and "verification code" in text
    # It still says WHICH value was refused, in words: a message can carry several.
    assert _rejection_text("amount", "must be >= 0.01") == "That amount is too low: the minimum is 0.01."


def test_a_refusal_never_reads_like_validator_output():
    """What a first-time user saw: "I couldn't accept that pay to: must be one of
    ['someone_new', 'a_saved_payee']", "must be >= 15.0", "cannot parse 'maybe' as
    boolean". The dropdown under the first one said "Someone New"."""
    from aigenos_flow.fields import FieldSpec

    choice = FieldSpec(type="enum", values=["someone_new", "a_saved_payee"])
    assert (_rejection_text("pay_to", "must be one of ['someone_new', 'a_saved_payee']", choice)
            == "Please choose one of: Someone New, A Saved Payee.")
    airport = FieldSpec(type="enum", values=["JFK", "EWR", "LGA"])
    assert _rejection_text("origin", "must be one of ['JFK', 'EWR', 'LGA']", airport) == "Please choose one of: JFK, EWR, LGA."

    assert _rejection_text("duration_minutes", "must be >= 15.0") == "That duration minutes is too low: the minimum is 15."
    assert _rejection_text("nights", "must be <= 7.0") == "That nights is too high: the maximum is 7."
    assert _rejection_text("confirm", "cannot parse 'maybe' as boolean") == "Please answer yes or no."
    assert _rejection_text("attendees", "expected a whole number, got '2.5'") == "I need a whole number for the attendees."
    assert _rejection_text("amount", "cannot parse 'lots' as a number") == "I need a number for the amount."
    assert _rejection_text("amount", "must be a finite number") == "I need a number for the amount."
    for text in (_rejection_text("x", "must be >= 15.0"), _rejection_text("x", "cannot parse 'a' as boolean")):
        assert "parse" not in text and "boolean" not in text and ".0" not in text


def test_the_dropdown_and_the_refusal_name_an_option_the_same_way():
    from aigenos_flow.fields import FieldSpec, option_label
    from aigenos_flow.integrations.flow import _field_to_input

    assert [option_label(v) for v in ("wrong_item", "JFK", "a_saved_payee")] == ["Wrong Item", "JFK", "A Saved Payee"]
    node = _field_to_input("origin", FieldSpec(type="enum", values=["JFK", "EWR"], description="Which airport?"))
    assert [o["label"] for o in node.props["options"]] == ["JFK", "EWR"], "an airport code is not Title Cased"


def test_a_refusal_is_not_followed_by_got_it():
    """With no model Reply at all, the runtime used to synthesize "Got it."
    directly after telling the user their value was refused."""
    host, chat = _chat()
    host.queue_commands("send -5 to mom",
                        SetField(field="amount", value=-5),
                        SetField(field="payee", value="mom"))
    chat.user("send -5 to mom")
    said = [str(r) for r in chat.replies]
    # The refusal is the skill's own sentence (`check: amount > 0`).
    assert "Enter an amount above zero." in said, said
    assert "Got it." not in said, said

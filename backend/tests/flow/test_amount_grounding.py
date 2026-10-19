"""A gate input is accepted only when the user literally stated it.

The number tokenizer once allowed the k/m/b magnitude suffix after whitespace,
so it swallowed the first letter of the following word: "1500 bucks" parsed as
1500 billion and "500 more" as 500 million. Two failures followed from one
regex: a plainly stated amount was dropped (the bot re-asked for a number the
user had just typed), and a model-invented multiple of it would have grounded.
"""
from __future__ import annotations

import pytest

from aigenos_flow.interpret import _is_grounded_in_user, _message_numbers


@pytest.mark.parametrize(
    ("message", "value"),
    [
        ("send $5000 to mom", 5000),
        ("send 5,000 to mom", 5000),
        ("send $5,000.00 to mom", 5000),
        ("send 5k to mom", 5000),
        ("transfer 2.5k", 2500),
        # The regression: a word starting with k/m/b right after the number.
        ("pay mom 1500 bucks", 1500),
        ("send 500 more to mom", 500),
        ("pay 200 monthly to the gym", 200),
        ("send 50 by friday", 50),
        ("give mom 75 kindly", 75),
        ("transfer 1000 before noon", 1000),
    ],
)
def test_a_literally_stated_amount_is_grounded(message: str, value: float):
    assert _is_grounded_in_user("amount", value, message), (message, _message_numbers(message))


@pytest.mark.parametrize(
    ("message", "value"),
    [
        ("send mom $12", 12000),                  # an invented multiple
        ("send mom 50", 500),
        ("pay landlord 2500 for may rent", 250),  # a substring, not the number
        # The other half of the same bug: the swallowed suffix must not let a
        # model inflate the amount by the magnitude of the next word's initial.
        ("send 5 more to mom", 5_000_000),
        ("send 5 bucks to mom", 5_000_000_000),
        ("give mom 75 kindly", 75_000),
        ("send the usual to mom", 999),           # no number stated at all
    ],
)
def test_a_value_the_user_did_not_state_is_refused(message: str, value: float):
    assert not _is_grounded_in_user("amount", value, message), (message, _message_numbers(message))


@pytest.mark.parametrize(
    ("message", "value"),
    [
        ("send five thousand dollars to mom", 5000),
        ("send five grand to mom", 5000),
        ("send a grand to mom", 1000),
        ("pay the landlord twenty five hundred", 2500),
        ("send one thousand five hundred to mom", 1500),
        ("send two hundred and fifty to the gym", 250),
        ("send fifty dollars to mom", 50),
        ("transfer twenty-five dollars", 25),
        ("send a hundred to mom", 100),
        ("send 5 thousand to mom", 5000),
        ("send 2.5 thousand to mom", 2500),
    ],
)
def test_an_amount_written_in_words_is_grounded(message: str, value: float):
    """Found with a live model: "send five thousand dollars" made the bot say
    "Got it, you want to send $5000" and then ask "How much do you want to
    send?", because only digits counted as the user stating a number."""
    assert _is_grounded_in_user("amount", value, message), (message, _message_numbers(message))


@pytest.mark.parametrize(
    ("message", "value"),
    [
        ("send five thousand dollars to mom", 50_000),
        ("send five thousand dollars to mom", 500),
        ("send fifty dollars to mom", 5000),
        ("send twenty five hundred to mom", 25),     # a prefix of what was said
        ("send one two three to mom", 123),          # digits read aloud are not 123
        ("send some money to mom", 1),
    ],
)
def test_words_do_not_ground_a_different_amount(message: str, value: float):
    assert not _is_grounded_in_user("amount", value, message), (message, _message_numbers(message))


def test_a_stated_negative_is_grounded_so_validation_can_explain_the_refusal():
    """"send -5" used to be dropped as never stated and silently re-asked. The
    user DID state it; grounding it lets the field's `min:` refuse it out loud."""
    assert _message_numbers("send -5 to mom") == [-5.0]
    assert _is_grounded_in_user("amount", -5, "send -5 to mom")
    # Hyphens inside a date are separators, not signs.
    assert _message_numbers("on 2026-10-05") == [2026.0, 10.0, 5.0]


def test_the_suffix_only_counts_when_attached():
    assert _message_numbers("send 5k to mom") == [5000.0]
    assert _message_numbers("send 5 k to mom") == [5.0]
    assert _message_numbers("ship 5kg to mom") == [5.0]
    assert _message_numbers("pay mom 1500 bucks") == [1500.0]

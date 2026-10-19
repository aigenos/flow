"""A confirmation is grounded by polarity: an approval needs the user's own
affirmative words and no contradicting negative.

Found with a live model: "go ahead" contains no single yes-word, so the
model's confirm=True was dropped as ungrounded and the flow re-asked its
confirmation on every turn.
"""
from __future__ import annotations

import pytest

from aigenos_flow.interpret import _consent_grounded


@pytest.mark.parametrize("said", [
    "yes", "Yes, send it", "yep", "ok", "sure", "confirm", "go ahead", "Go ahead!",
    "please go ahead", "do it", "let's do it", "just do it", "go for it",
    "sounds good", "looks good to me", "that works", "please do", "of course",
    "absolutely", "definitely", "alright", "lgtm", "ship it", "that's right",
    "good to go", "kk",
])
def test_ordinary_ways_of_saying_yes_ground_an_approval(said: str):
    assert _consent_grounded(True, said), said


@pytest.mark.parametrize("said", [
    "no", "no, cancel that", "don't do it", "do not go ahead", "not yet",
    "wait, hold on", "stop", "never mind", "nope",
    # No approval at all in these — a model answering its own question.
    "what's the fee?", "send it to the landlord instead", "hmm", "",
    "how long will it take",
])
def test_these_never_ground_an_approval(said: str):
    assert not _consent_grounded(True, said), said


@pytest.mark.parametrize("said", ["no", "don't", "cancel", "not yet", "stop", "do not go ahead"])
def test_a_refusal_is_grounded_by_a_negative(said: str):
    assert _consent_grounded(False, said), said

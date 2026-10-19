"""A finished workflow must not keep owning the conversation.

Reported from the Studio: after a skill completed, every later message was
still read against it. "show me full plan" replayed a flow with nothing left to
say; "show?" started another skill as a *digression* and then announced "Now,
back to where we were"; and "so you can only talk about this?" was answered
"That's right". The cause was that completion set ``done`` but never released
``flow_id``, and the interpreter's prompt limits the model to the active
skill's topic.

Once a flow finishes, the next message is a fresh decision: answer (with the
finished run's real data in view, and no topic restriction), or start a skill.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import OutsideFlowDecision

SKILLS = Path(__file__).resolve().parents[3] / "examples" / "before_after" / "skills_after"
STUDIO_SKILLS = Path(__file__).resolve().parents[3] / "examples" / "studio_agent" / "skills"

pytestmark = pytest.mark.skipif(not SKILLS.is_dir(), reason="examples/ not present")


class RecordingHost(TestHost):
    """Captures the prompt the outside-flow decision is made from."""

    def __init__(self) -> None:
        super().__init__()
        self.outside_prompts: list[str] = []

    def llm_structured(self, *, system, user, schema):
        if schema is OutsideFlowDecision:
            self.outside_prompts.append(system)
        return super().llm_structured(system=system, user=user, schema=schema)


def _finished_transfer(host: TestHost):
    fired: list[dict] = []
    agent = FlowAgent.from_dir(SKILLS, host=host, tools={"bank_transfer": lambda a: fired.append(a) or {"txn": "TXN-9"}})
    host.queue_commands("send $5000 to mom",
                        SetField(field="amount", value=5000),
                        SetField(field="payee", value="mom"))
    chat = agent.session("transfer-funds").user("send $5000 to mom")
    chat.submit(code="481920").submit(confirm=True)
    assert chat.done and len(fired) == 1
    return chat, fired


def test_after_a_flow_finishes_a_question_is_answered_not_replayed():
    host = RecordingHost()
    chat, fired = _finished_transfer(host)
    host.set_schema_override(
        OutsideFlowDecision,
        OutsideFlowDecision(action="reply", reply="You sent $5000 to mom."),
    )

    chat.user("show me what I just did")

    assert [str(r) for r in chat.replies] == ["You sent $5000 to mom."]
    assert len(fired) == 1, "answering a question must never re-fire the action"
    assert chat.state["fields"]["amount"] == 5000, "the finished run's data is untouched"


def test_the_decision_sees_the_finished_runs_data_but_not_its_secrets():
    host = RecordingHost()
    chat, _ = _finished_transfer(host)
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="reply", reply="ok"))

    chat.user("what did I send?")

    prompt = host.outside_prompts[-1]
    assert "transfer-funds" in prompt and "5000" in prompt and "mom" in prompt
    assert "481920" not in prompt, "a sensitive field must never be shown to the model again"
    assert "(hidden)" in prompt
    assert "NOT limited" in prompt, "no topic restriction once the workflow is over"


def test_an_unrelated_question_is_just_answered():
    host = RecordingHost()
    chat, fired = _finished_transfer(host)
    host.set_schema_override(
        OutsideFlowDecision,
        OutsideFlowDecision(action="reply", reply="I can't look up live weather here, but I can chat."),
    )

    chat.user("what's the weather in Austin?")

    assert "can't look up live weather" in str(chat.replies[0])
    assert len(fired) == 1


def test_asking_for_the_same_skill_again_starts_a_fresh_run():
    host = RecordingHost()
    chat, fired = _finished_transfer(host)
    first_run = chat.state["flow_run_id"]
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="start_skill", skill="transfer-funds"))
    host.queue_commands("send another 200 to the gym",
                        SetField(field="amount", value=200),
                        SetField(field="payee", value="gym"))

    chat.user("send another 200 to the gym")

    assert chat.state["flow_id"] == "transfer-funds" and not chat.done
    assert chat.state["flow_run_id"] != first_run, "a new intent is a new run"
    assert chat.state["fields"].get("code") is None, "nothing carries over"
    assert chat.state["fields"].get("confirm") is None
    assert chat.awaiting == "confirm" and len(fired) == 1
    assert not chat.state.get("flow_stack"), "a new task is not a digression"

    chat.submit(confirm=True)
    assert chat.done and len(fired) == 2 and fired[1]["amount"] == 200


def test_starting_a_different_skill_is_a_switch_not_a_digression():
    if not STUDIO_SKILLS.is_dir():
        pytest.skip("studio_agent example not present")
    import sys
    sys.path.insert(0, str(STUDIO_SKILLS.parent))
    host = RecordingHost()
    agent = FlowAgent.from_dir(STUDIO_SKILLS, host=host)
    host.queue_commands("what rooms are there?")
    chat = agent.session("list-rooms").user("what rooms are there?")
    assert chat.done

    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="start_skill", skill="plan-team-offsite"))
    host.queue_commands("plan an offsite for the platform team",
                        SetField(field="team_name", value="platform"))
    chat.user("plan an offsite for the platform team")

    said = " | ".join(str(r) for r in chat.replies)
    assert chat.state["flow_id"] == "plan-team-offsite"
    assert chat.state["fields"].get("team_name") == "platform", "the same message was read by the new flow"
    assert chat.awaiting == "city"
    assert "back to where we were" not in said, said
    assert not chat.state.get("flow_stack")


def test_a_skill_name_the_model_made_up_is_not_a_routing_decision():
    host = RecordingHost()
    chat, fired = _finished_transfer(host)
    host.set_schema_override(
        OutsideFlowDecision,
        OutsideFlowDecision(action="start_skill", skill="wire-all-my-money", reply=""),
    )

    chat.user("do the other thing")

    assert chat.state["flow_id"] == "transfer-funds" and chat.done, "nothing was started"
    assert len(fired) == 1
    assert chat.replies, "the user still gets an answer"


def test_a_widget_resubmission_after_completion_still_belongs_to_the_flow():
    """An 'edit' on a review widget is a correction of that run, not small talk."""
    host = RecordingHost()
    chat, fired = _finished_transfer(host)
    host.set_schema_override(OutsideFlowDecision, OutsideFlowDecision(action="reply", reply="should not be used"))

    chat.submit(payee="landlord")

    assert host.outside_prompts == [], "a structured submission never goes to the model"
    assert len(fired) == 1, "the fired transfer is not repeated"


def test_with_no_flow_and_no_match_the_user_gets_a_real_answer():
    host = RecordingHost()
    agent = FlowAgent.from_dir(SKILLS, host=host, tools={"bank_transfer": lambda a: {"txn": "T"}})
    host.set_schema_override(
        OutsideFlowDecision,
        OutsideFlowDecision(action="reply", reply="Hi! I can send money to a saved payee, or just chat."),
    )

    chat = agent.session().user("hello there")

    said = [str(r) for r in chat.replies]
    assert said == ["Hi! I can send money to a saved payee, or just chat."]
    assert chat.state.get("flow_id") is None, "a greeting does not force the only skill to start"


def test_a_scripted_host_that_cannot_decide_keeps_the_previous_behaviour():
    host = TestHost()  # no override: llm_structured raises NotImplementedError for this schema
    chat, fired = _finished_transfer(host)
    host.queue_commands("thanks")

    chat.user("thanks")

    assert len(fired) == 1 and chat.done


def test_the_finished_summary_keeps_the_latest_results_when_early_ones_are_long():
    """A flow starts with its searches; the outcome is at the end. A fixed cut
    from the front used to keep two long lists and drop the booking."""
    from aigenos_flow.interpret import _render_results

    results = {
        "search_flights": {"flights": [{"flight_id": f"NW{i:03d}", "note": "x" * 60} for i in range(40)]},
        "find_hotels": {"hotels": [{"hotel": f"Hotel {i}", "note": "y" * 60} for i in range(40)]},
        "quote_cab": {"summary": {"drive time": "19 min without traffic", "meter estimate": "$53.70"}},
        "take_payment": {"reference": "PAY-414261", "amount": "1,391.50"},
    }
    block = _render_results(results)

    assert "PAY-414261" in block and "19 min without traffic" in block
    assert block.index("quote_cab") < block.index("take_payment"), "kept results stay in run order"
    assert len(block) < 4000
    assert "…" in block, "a long early result is cut, not dropped silently"

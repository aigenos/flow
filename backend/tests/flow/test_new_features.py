"""Tests for new features: dependency tracking, ask_group, retry, conditional fields."""
import pytest
from aigenos_flow.fields import FieldSpec
from aigenos_flow.context import Context, NeedField, NeedFields
from aigenos_flow.runtime import visible_fields
from aigenos_flow.decorators import action, action_depends_on


class TestSmartDependencyTracking:
    def test_action_depends_on_decorator(self):
        @action(depends_on=["room", "date"])
        def check_avail(state):
            return {"ok": True}
        assert action_depends_on(check_avail) == ["room", "date"]

    def test_action_no_depends_on(self):
        @action
        def simple(state):
            return {}
        assert action_depends_on(simple) is None

    def test_action_named_with_depends_on(self):
        @action("my_action", depends_on=["field_a"])
        def something(state):
            return {}
        assert action_depends_on(something) == ["field_a"]

    def test_correction_clears_every_memo_even_with_depends_on(self):
        """depends_on no longer narrows correction invalidation.

        It used to: a correction preserved the memo of any action whose
        ``depends_on`` did not name the changed field. Nothing checked that the
        list covered the fields the action actually read, so one plausible
        authoring slip (``depends_on=["from_account"]`` on a policy check that
        reads ``amount``) kept a stale "no 2FA needed" decision alive across a
        $500 -> $5000 correction and moved the money with no code. Corrections
        now invalidate every memo, full stop; at-most-once for effects that
        already fired is carried by ``effects_log``, not by ``results``.
        """
        from aigenos_flow.runtime import _set_field
        from aigenos_flow.skills import Skill
        skill = Skill(
            name="test", description="test", fields={
                "room": FieldSpec(type="string"),
                "date": FieldSpec(type="string"),
            },
            flow_fn=lambda ctx: None, actions={},
            action_deps={"check_room": ["room"], "check_date": ["date"]},
            triggers=[], body="", path=".",
        )
        class FakeHost:
            def emit(self, t): pass
        state = {
            "fields": {"room": "old_room", "date": "2026-01-01"},
            "results": {"check_room": {"ok": True}, "check_date": {"ok": True}},
            "field_history": {},
        }
        _set_field(state, skill, "room", "new_room", FakeHost(), is_correction=True)
        assert state["results"] == {}


class TestParallelFieldCollection:
    def test_ask_group_all_filled(self):
        state = {"fields": {"a": 1, "b": 2}, "field_history": {}}
        specs = {"a": FieldSpec(type="integer"), "b": FieldSpec(type="integer")}
        ctx = Context(state=state, field_specs=specs, emit=lambda t: None)
        result = ctx.ask_group("a", "b")
        assert result == (1, 2)

    def test_ask_group_raises_need_fields(self):
        state = {"fields": {"a": 1}, "field_history": {}}
        specs = {"a": FieldSpec(type="integer"), "b": FieldSpec(type="integer", description="Enter B")}
        ctx = Context(state=state, field_specs=specs, emit=lambda t: None)
        with pytest.raises(NeedFields) as exc_info:
            ctx.ask_group("a", "b")
        assert exc_info.value.fields == ["b"]


class TestConditionalFields:
    def test_visible_when_met(self):
        class FakeSkill:
            fields = {
                "type": FieldSpec(type="enum", values=["std", "exec"]),
                "floor": FieldSpec(type="string", visible_when={"type": "exec"}),
            }
        visible = visible_fields(FakeSkill(), {"type": "exec"})
        assert "floor" in visible

    def test_visible_when_not_met(self):
        class FakeSkill:
            fields = {
                "type": FieldSpec(type="enum", values=["std", "exec"]),
                "floor": FieldSpec(type="string", visible_when={"type": "exec"}),
            }
        visible = visible_fields(FakeSkill(), {"type": "std"})
        assert "floor" not in visible

    def test_no_visible_when_always_visible(self):
        class FakeSkill:
            fields = {
                "name": FieldSpec(type="string"),
            }
        visible = visible_fields(FakeSkill(), {})
        assert "name" in visible


class TestLLMRetry:
    def test_retry_on_transient_failure(self):
        from aigenos_flow.hosts.langgraph_host import LangGraphHost
        class FakeLLM:
            pass
        host = LangGraphHost(llm=FakeLLM(), max_retries=3, backoff_factor=0.01)
        call_count = 0
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("connection timeout")
            return "success"
        result = host._call_with_retry(flaky)
        assert result == "success"
        assert call_count == 3

    def test_non_transient_fails_immediately(self):
        from aigenos_flow.hosts.langgraph_host import LangGraphHost
        class FakeLLM:
            pass
        host = LangGraphHost(llm=FakeLLM(), max_retries=3, backoff_factor=0.01)
        def bad():
            raise ValueError("invalid argument")
        with pytest.raises(ValueError):
            host._call_with_retry(bad)

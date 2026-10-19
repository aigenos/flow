"""Pin-tests for the easy authoring surface: pick / show / check / chat /
when_missing — the 7-verb vocabulary that lets a whole lookup-validate-explore
workflow live in one SKILL.md with no Python and no widget JSON.
"""
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, initial_state
from aigenos_flow.autowidget import pick_spec_for, spec_for
from aigenos_flow.host import ToolCall
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import Correct, SetField
from aigenos_flow.skills import load_skill

SKILL = """---
name: lookup
description: Find a thing and explore it.
allowed-tools: search_things, validate_thing, thing_details
readonly-tools: thing_details
fields:
  q:
    type: string
    must_elicit: true
    pattern: '^[a-z]{1,20}$'
    description: What to search.
  thing_id:
    type: string
    pattern: '^[A-Z0-9-]{1,20}$'
    description: The thing to explore.
actions:
  search_things:
    tool: search_things
    readonly: true
    audit: false
    args: { q: "{{q}}" }
  validate_thing:
    tool: validate_thing
    readonly: true
    audit: false
    args: { id: "{{thing_id}}" }
flow:
  - ask: q
    when_missing: thing_id
  - do: search_things
    when_missing: thing_id
  - pick: "{{search_things.matches}}"
    when_missing: thing_id
    save: [thing_id]
    prompt: "Pick one"
  - do: validate_thing
  - check: "validate_thing.valid"
    else_say: "No such thing {{thing_id}}."
    else_clear: [thing_id]
  - show: "{{validate_thing.details}}"
    title: "Thing {{thing_id}}"
  - chat:
      tools: [thing_details]
      pin: { thing_id: thing_id }
      greeting: "Ask away."
---
Body.
"""

VALID = {"T-1": {"name": "one"}, "T-2": {"name": "two"}}


def _tools(calls):
    def search_things(args):
        return {"matches": [{"id": "T-1"}, {"id": "T-2"}]}

    def validate_thing(args):
        calls.append(("validate", args["id"]))
        d = VALID.get(args["id"])
        return {"valid": d is not None, "details": d or {}}

    def thing_details(args):
        """Details for a thing."""
        calls.append(("details", dict(args)))
        return {"name": VALID[args["thing_id"]]["name"], "size": 3}

    return {
        "search_things": search_things,
        "validate_thing": validate_thing,
        "thing_details": thing_details,
    }


def _write(tmp_path: Path, md: str) -> Path:
    d = tmp_path / "lookup"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(md, encoding="utf-8")
    return d


def _agent(tmp_path, calls):
    host = TestHost()
    (tmp_path / "skills").mkdir(exist_ok=True)
    _write(tmp_path / "skills", SKILL)
    agent = FlowAgent.from_dir(tmp_path / "skills", host=host, tools=_tools(calls))
    st = initial_state()
    st["flow_id"] = "lookup"
    return host, agent, st


# ------------------------------------------------------------------ pick
def test_pick_renders_auto_grid_and_submit_resumes(tmp_path):
    calls: list = []
    host, agent, st = _agent(tmp_path, calls)
    host.queue_commands("find abc", SetField(field="q", value="abc"))
    st["pending_user_message"] = "find abc"
    st = agent.turn(st)
    pw = st["pending_widget"]
    assert pw["widget"] == "pick:thing_id"
    assert pw["spec"]["children"][0]["children"][0]["type"] == "datatable"
    assert pw["submit_schema"]["required"] == ["thing_id"]
    # structured submit fills the field and the flow moves past the pick
    st["pending_field_updates"] = {"thing_id": "T-1"}
    st = agent.turn(st)
    assert st["fields"]["thing_id"] == "T-1"
    assert st["pending_widget"] is None


# ------------------------------------------------------- when_missing (fast path)
def test_when_missing_skips_search_on_fast_path(tmp_path):
    calls: list = []
    host, agent, st = _agent(tmp_path, calls)
    host.queue_commands("open T-2", SetField(field="thing_id", value="T-2"))
    st["pending_user_message"] = "open T-2"
    st = agent.turn(st)
    assert calls == [("validate", "T-2")]  # search never ran
    assert st["pending_widget"] is None
    assert st["chat_config"] is not None


# ------------------------------------------------------------------ check
def test_check_rejects_clears_and_reasks_then_recovers(tmp_path):
    calls: list = []
    host, agent, st = _agent(tmp_path, calls)
    host.queue_commands("open T-9", SetField(field="thing_id", value="T-9"))
    st["pending_user_message"] = "open T-9"
    st = agent.turn(st)
    msgs = host.drain_pending_messages()
    assert any("No such thing T-9" in m for m in msgs)
    assert st["fields"].get("thing_id") is None
    assert st["awaiting_user"] is True
    # recovery: a fresh id re-validates (no stale memo) and passes the gate
    host.queue_commands("T-1 then", SetField(field="thing_id", value="T-1"))
    st["pending_user_message"] = "T-1 then"
    st = agent.turn(st)
    assert calls == [("validate", "T-9"), ("validate", "T-1")]
    assert st["chat_config"] is not None


# ------------------------------------------------------------------ show
def test_show_emits_widget_once_per_run(tmp_path):
    calls: list = []
    host, agent, st = _agent(tmp_path, calls)
    host.queue_commands("open T-1", SetField(field="thing_id", value="T-1"))
    st["pending_user_message"] = "open T-1"
    st = agent.turn(st)
    widgets = host.drain_pending_widgets()
    assert any(w["props"].get("title") == "Thing T-1" for w in widgets)
    # a chat turn replays the flow; show must NOT re-emit
    host.queue_commands("hello?")
    host.queue_tool_calls("hello?")  # model picks no tool
    host.queue_chat_answer("hello?", "Hi.")
    st["pending_user_message"] = "hello?"
    st = agent.turn(st)
    assert not host.drain_pending_widgets()


# ------------------------------------------------------------------ chat
def test_chat_pins_args_and_correction_reroutes(tmp_path):
    calls: list = []
    host, agent, st = _agent(tmp_path, calls)
    host.queue_commands("open T-1", SetField(field="thing_id", value="T-1"))
    st["pending_user_message"] = "open T-1"
    st = agent.turn(st)
    host.drain_pending_messages()

    # free question -> tool call with the pinned id, even if the model aims elsewhere
    host.queue_commands("details for T-2 please")
    host.queue_tool_calls("details for T-2 please",
                          ToolCall(name="thing_details", args={"thing_id": "T-2"}))
    host.queue_chat_answer("thing_details", "It is called one.")
    st["pending_user_message"] = "details for T-2 please"
    st = agent.turn(st)
    assert calls[-1] == ("details", {"thing_id": "T-1"})  # pin wins
    assert any("called one" in m for m in host.drain_pending_messages())

    # correction is NOT swallowed by chat: it re-validates and re-pins
    host.queue_commands("switch to T-2", Correct(field="thing_id", value="T-2"))
    st["pending_user_message"] = "switch to T-2"
    st = agent.turn(st)
    assert calls[-1] == ("validate", "T-2")
    host.drain_pending_messages()

    host.queue_commands("and its details?")
    host.queue_tool_calls("and its details?", ToolCall(name="thing_details", args={}))
    host.queue_chat_answer("thing_details", "It is called two.")
    st["pending_user_message"] = "and its details?"
    st = agent.turn(st)
    assert calls[-1] == ("details", {"thing_id": "T-2"})


# ------------------------------------------------------------- loader guards
def test_chat_must_be_last_step(tmp_path):
    md = SKILL.replace(
        "  - chat:\n      tools: [thing_details]\n      pin: { thing_id: thing_id }\n      greeting: \"Ask away.\"\n",
        "",
    ).replace(
        "  - do: validate_thing\n",
        "  - chat:\n      tools: [thing_details]\n      pin: { thing_id: thing_id }\n  - do: validate_thing\n",
    )
    calls: list = []
    with pytest.raises(ValueError, match="must be the LAST step"):
        load_skill(_write(tmp_path, md), tool_registry=_registry(calls))


def test_chat_tool_must_be_allowlisted(tmp_path):
    md = SKILL.replace(
        "allowed-tools: search_things, validate_thing, thing_details\n"
        "readonly-tools: thing_details\n",
        "allowed-tools: search_things, validate_thing\n",
    )
    calls: list = []
    with pytest.raises(ValueError, match="not in `allowed-tools`"):
        load_skill(_write(tmp_path, md), tool_registry=_registry(calls))


def test_chat_tool_must_be_declared_readonly(tmp_path):
    """A conversational leaf hands tool-argument choice to the model, so only a
    tool PROVEN read-only may be reachable from it (decision 9). A plain
    callable nobody declared is assumed to write."""
    md = SKILL.replace("readonly-tools: thing_details\n", "")
    calls: list = []
    with pytest.raises(ValueError, match="not declared read-only"):
        load_skill(_write(tmp_path, md), tool_registry=_registry(calls))


def test_check_requires_else_clear(tmp_path):
    md = SKILL.replace("    else_clear: [thing_id]\n", "")
    calls: list = []
    with pytest.raises(ValueError, match="else_clear"):
        load_skill(_write(tmp_path, md), tool_registry=_registry(calls))


def test_pick_save_must_be_declared(tmp_path):
    md = SKILL.replace("    save: [thing_id]", "    save: [nope]")
    calls: list = []
    with pytest.raises(ValueError, match="unknown field 'nope'"):
        load_skill(_write(tmp_path, md), tool_registry=_registry(calls))


def _registry(calls):
    from aigenos_flow.declarative import ToolRegistry

    return ToolRegistry.from_tools(_tools(calls))


def _imperative_skill(tmp_path: Path, *, readonly_line: str) -> Path:
    d = tmp_path / "impy"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\n"
        "name: impy\n"
        "description: Imperative skill with chat tools.\n"
        "allowed-tools: thing_details\n"
        f"{readonly_line}"
        "fields:\n  thing_id: {type: string, pattern: '^[A-Z0-9-]{1,20}$'}\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    (d / "handlers.py").write_text(
        "from aigenos_flow import flow\n\n"
        "@flow\n"
        "def impy(ctx):\n"
        "    ctx.ask('thing_id')\n"
        "    ctx.chat({'tools': ['thing_details'], 'pin': {'thing_id': 'thing_id'}})\n",
        encoding="utf-8",
    )
    return d


def test_imperative_skill_gets_chat_tools_from_readonly_allowlist(tmp_path):
    calls: list = []
    d = _imperative_skill(tmp_path, readonly_line="readonly-tools: thing_details\n")
    skill = load_skill(d, tool_registry=_registry(calls))
    # allowlist ∩ registry ∩ proven-read-only
    assert set(skill.chat_tools) == {"thing_details"}


def test_imperative_chat_cannot_reach_an_undeclared_tool(tmp_path):
    """An imperative @flow's ctx.chat used to receive every allowlisted tool,
    write tools included. Only tools declared read-only reach it now."""
    calls: list = []
    d = _imperative_skill(tmp_path, readonly_line="")
    skill = load_skill(d, tool_registry=_registry(calls))
    assert set(skill.chat_tools) == set()


def test_pick_as_autosuggest_and_widget_ref(tmp_path):
    # as: autosuggest -> the auto spec's inner primitive changes
    md = SKILL.replace(
        '  - pick: "{{search_things.matches}}"\n',
        '  - pick: "{{search_things.matches}}"\n    as: autosuggest\n',
    )
    calls: list = []
    host = TestHost()
    (tmp_path / "skills").mkdir()
    _write(tmp_path / "skills", md)
    agent = FlowAgent.from_dir(tmp_path / "skills", host=host, tools=_tools(calls))
    st = initial_state()
    st["flow_id"] = "lookup"
    host.queue_commands("find abc", SetField(field="q", value="abc"))
    st["pending_user_message"] = "find abc"
    st = agent.turn(st)
    inner = st["pending_widget"]["spec"]["children"][0]["children"][0]
    assert inner["type"] == "autosuggest"
    assert inner["props"]["fields"] == {"thing_id": "id"}

    # widget: <id> -> catalog delegation: no inline spec, id passes through,
    # rows land under both conventional binding names
    md2 = SKILL.replace(
        '  - pick: "{{search_things.matches}}"\n',
        '  - pick: "{{search_things.matches}}"\n    widget: my_custom_picker\n',
    )
    calls2: list = []
    host2 = TestHost()
    d2 = tmp_path / "skills2"
    d2.mkdir()
    _write(d2, md2)
    agent2 = FlowAgent.from_dir(d2, host=host2, tools=_tools(calls2))
    st2 = initial_state()
    st2["flow_id"] = "lookup"
    host2.queue_commands("find abc", SetField(field="q", value="abc"))
    st2["pending_user_message"] = "find abc"
    st2 = agent2.turn(st2)
    pw = st2["pending_widget"]
    assert pw["widget"] == "my_custom_picker"
    assert pw["spec"] is None
    assert pw["data"]["rows"] == pw["data"]["options"]


def test_pick_rejects_both_as_and_widget(tmp_path):
    md = SKILL.replace(
        '  - pick: "{{search_things.matches}}"\n',
        '  - pick: "{{search_things.matches}}"\n    as: autosuggest\n    widget: my_picker\n',
    )
    calls: list = []
    with pytest.raises(ValueError, match="both `as:` and `widget:`"):
        load_skill(_write(tmp_path, md), tool_registry=_registry(calls))


# ------------------------------------------------------------- autowidget unit
def test_spec_for_shapes():
    rows = [{"date": "2026-01-01", "amount": 5}]
    assert spec_for(rows)["children"][0]["type"] == "timeline"
    assert spec_for([{"a": 1, "b": 2}])["children"][0]["type"] == "datatable"
    assert spec_for({"a": 1, "b": "x"})["children"][0]["type"] == "column"
    assert spec_for(42)["children"][0]["type"] == "text"


def test_pick_spec_maps_prefixed_save_fields():
    spec, schema = pick_spec_for(
        [{"id": "C1", "full_name": "Ann"}], ["client_id", "client_full_name"]
    )
    dt = spec["children"][0]["children"][0]
    assert dt["props"]["fields"] == {"client_id": "id", "client_full_name": "full_name"}
    assert set(schema["required"]) == {"client_id", "client_full_name"}

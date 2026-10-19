"""A grid stays a grid: through a LangGraph agent, and after a gate rejects a pick.

Two things a multi-step flow (search -> pick a row -> check it -> next step)
needs, both of which used to degrade to a bare text box:

  * under ``wrap_agent`` / ``create_agent`` the pause carried a plain input for
    the field a ``pick:`` step saves, not the grid the step had built;
  * when a ``check:`` failed and cleared that field, the flow paused on the
    field directly, so the user was asked to TYPE an id they had been picking
    from a table a moment before.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

from aigenos_flow import SetField, wrap_agent
from aigenos_flow.hosts.test_host import TestHost

SKILL_MD = """---
name: pick-room
description: Pick a meeting room for a headcount and hold it. Use when the user wants a room.
allowed-tools: list_rooms, room_fits, hold_room
fields:
  people:
    type: integer
    min: 1
    description: How many people?
  room_id:
    type: string
    pattern: '^R-[0-9]{2}$'
    description: Which room?
  confirm:
    type: boolean
    description: Hold this room?
actions:
  list_rooms:
    tool: list_rooms
    readonly: true
    audit: false
  room_fits:
    tool: room_fits
    readonly: true
    audit: false
    args: { room_id: "{{room_id}}", people: "{{people}}" }
  hold_room:
    tool: hold_room
    requires: confirm
    args: { room_id: "{{room_id}}" }
flow:
  - ask: people
  - say: "Here are the rooms."
  - do: list_rooms
  - show: "{{list_rooms.legend}}"
    title: Legend
  - pick: "{{list_rooms.rooms}}"
    save: [room_id]
    prompt: Which room?
  - do: room_fits
  - check: "room_fits.ok"
    else_say: "That room is too small. Pick another."
    else_clear: [room_id]
  - ask: confirm
  - when: "confirm"
    do: hold_room
---
Help the user pick a room.
"""

SCRIPTS_PY = '''HELD = []
ROOMS = [{"room_id": "R-01", "name": "Nook", "seats": 2}, {"room_id": "R-02", "name": "Atrium", "seats": 12}]


def list_rooms(args):
    return {"rooms": ROOMS, "legend": {"seats": "maximum headcount"}}


def room_fits(args):
    room = next(r for r in ROOMS if r["room_id"] == args["room_id"])
    return {"ok": room["seats"] >= args["people"]}


def hold_room(args):
    HELD.append(args["room_id"])
    return {"held": args["room_id"]}
'''

OPENER = "I need a room for 6"


class _S(TypedDict, total=False):
    messages: Annotated[list, add_messages]


def _agent(tmp_path: Path):
    skill = tmp_path / "skills" / "pick-room"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skill / "scripts.py").write_text(SCRIPTS_PY, encoding="utf-8")

    g = StateGraph(_S)
    g.add_node("chat", lambda s: {"messages": [AIMessage(content="(agent)")]})
    g.add_edge(START, "chat")
    g.add_edge("chat", END)

    host = TestHost()
    host.queue_classification(OPENER, "pick-room")
    host.queue_commands(OPENER, SetField(field="people", value=6))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return wrap_agent(g.compile(), skills_dir=tmp_path / "skills", host=host)


def _turn(agent, payload):
    cfg = {"configurable": {"thread_id": "t"}}
    before = len(agent.get_state(cfg).values.get("messages") or [])
    agent.invoke(payload, config=cfg)
    snap = agent.get_state(cfg)
    new = [m for m in (snap.values.get("messages") or [])[before:] if isinstance(m, AIMessage)]
    pause = next((t.interrupts[0].value for t in snap.tasks if t.interrupts), None)
    return snap.values, pause, new


def _node_types(node, out=None) -> list[str]:
    out = [] if out is None else out
    if isinstance(node, dict):
        if isinstance(node.get("type"), str) and "children" in node or node.get("type") in {"datatable", "input", "select"}:
            out.append(node["type"])
        for value in node.values():
            _node_types(value, out)
    elif isinstance(node, list):
        for value in node:
            _node_types(value, out)
    return out


def test_a_pick_step_reaches_the_client_as_a_grid_through_a_langgraph_agent(tmp_path):
    agent = _agent(tmp_path)

    _, pause, _ = _turn(agent, {"messages": [("user", OPENER)]})

    assert pause["field"] == "room_id"
    assert "datatable" in _node_types(pause["widget"]), "the pause must carry the grid, not a text input"
    assert "Atrium" in json.dumps(pause["widget"]), "with the rows the tool returned"


def test_a_rejected_pick_is_asked_again_with_the_grid_and_nothing_is_said_twice(tmp_path):
    agent = _agent(tmp_path)
    _turn(agent, {"messages": [("user", OPENER)]})

    # R-01 seats 2; the check fails and clears room_id.
    state, pause, new = _turn(agent, Command(resume={"room_id": "R-01"}))

    assert pause["field"] == "room_id"
    assert "datatable" in _node_types(pause["widget"]), "re-ask with the grid, not a text box"
    said = [m.content for m in new if m.content]
    assert said.count("That room is too small. Pick another.") == 1
    assert "Here are the rooms." not in said, "an earlier say: must not be repeated by the replay"
    shown = [w for m in new for w in (m.additional_kwargs or {}).get("aigenos_widgets") or []]
    assert shown == [], "an earlier show: must not be repeated by the replay"

    # The grid still works: a room that fits moves on, and the write fires once.
    state, pause, _ = _turn(agent, Command(resume={"room_id": "R-02"}))
    assert pause["field"] == "confirm"
    state, _, _ = _turn(agent, Command(resume={"confirm": True}))
    assert state["done"]
    assert state["results"]["hold_room"] == {"held": "R-02"}

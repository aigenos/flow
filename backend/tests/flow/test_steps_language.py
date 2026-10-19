"""What porting the shipped skills to steps settled about the language.

Each test here pins something that was wrong, or missing, when a real skill was
rewritten as steps: who may give an answer, how a tool's sentence is said, a
designed page for a confirmation, a list whose rows name their id differently,
and tools that come from an MCP server.

Offline: TestHost stands in for the model.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aigenos_flow import FlowAgent, SetField
from aigenos_flow.autowidget import _match_row_key
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.interpret import _is_grounded_in_user, _message_times
from aigenos_flow.skills import load_skills_dir

REPO = Path(__file__).resolve().parents[3]

DESK = '''
from aigenos_flow import readonly

BOOKED: list = []


@readonly
def check_slot(room: str, start_time: str, minutes: int) -> dict:
    """Is the room free then?"""
    return {"free": start_time != "09:00", "label": {"Room": room, "From": start_time}}


@readonly
def price(minutes: int, tier: str) -> dict:
    """What it costs."""
    return {"needs_card": minutes > 120, "text": f"That is {minutes} minutes on the {tier} tier."}


def book(room: str, start_time: str, minutes: int) -> dict:
    """Book it."""
    BOOKED.append((room, start_time, minutes))
    return {"message": f"Booked {room} at {start_time}.", "count": len(BOOKED)}
'''

ROOM = """\
---
name: book-room
description: Book a room. Use when the user wants to book a room.
steps:
  - ask: { room: "Which room?" }
    choices: [oak, pine]
  - ask: { start_time: "What time does it start?" }
    pattern: '^([01][0-9]|2[0-3]):[0-5][0-9]$'
    check: { check_slot.free: "{{room}} is taken at {{start_time}}. Pick another time." }
  - ask: { minutes: "For how long, in minutes?" }
    check: { minutes >= 15 and minutes <= 480: "A booking is between 15 minutes and 8 hours." }
  - ask: { tier: "Standard or premium?" }
    choices: [standard, premium]
  - ask: { card: "Bookings over two hours need a card on file. The last four digits?" }
    when: price.needs_card
    pattern: '^[0-9]{4}$'
  - say: "{{price.text}}"
  - say: "{{check_slot.label}}"
  - confirm: Book it?
  - do: book
  - say: "{{book.message}}"
---
Book a room.
"""


def _agent(tmp_path: Path, md: str = ROOM, name: str = "book-room"):
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tools" / "desk.py").write_text(DESK, encoding="utf-8")
    (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / name / "SKILL.md").write_text(md, encoding="utf-8")
    host = TestHost()
    agent = FlowAgent.from_dir(tmp_path, host=host)
    desk = next(m for n, m in sys.modules.items() if n.startswith("_flow_tools") and n.endswith("_desk"))
    return agent, host, desk


# --------------------------------------------------------------------------- who may give an answer
def test_only_an_answer_that_decides_a_branch_is_the_users_alone(tmp_path):
    agent, _, _ = _agent(tmp_path)
    skill = agent.registry.get("book-room")
    user_only = {n for n, f in skill.fields.items()
                 if f.must_elicit or f.sensitive or n in skill.confirmation_fields or n in skill.picked_fields}
    # `price.needs_card` chooses a branch, so what `price` takes decides it:
    # minutes (a number) and tier (a choice). start_time and card have a shape.
    assert user_only == {"start_time", "minutes", "tier", "card", "confirm"}
    # `room` only feeds a lookup that a `check:` reads. A check judges an
    # answer; it does not choose a branch. The model may fill it.
    assert not skill.fields["room"].must_elicit


def test_a_rule_on_its_own_answer_lives_on_the_answer(tmp_path):
    md = ROOM.replace("  - ask: { card:", "  - ask: { _unused:").replace(
        "    when: price.needs_card\n    pattern: '^[0-9]{4}$'\n", "")
    md = md.replace("  - ask: { _unused: \"Bookings over two hours need a card on file. The last four digits?\" }\n", "")
    md = md.replace('  - say: "{{price.text}}"\n', "").replace(
        '  - ask: { tier: "Standard or premium?" }\n    choices: [standard, premium]\n', "")
    agent, host, _ = _agent(tmp_path, md)
    skill = agent.registry.get("book-room")
    assert skill.fields["minutes"].rules == [("minutes >= 15 and minutes <= 480", "A booking is between 15 minutes and 8 hours.")]
    assert not skill.fields["minutes"].must_elicit, "a range is a property of the answer, not a decision"

    # So "for an hour" is accepted as 60: the model normalised what the user said.
    host.queue_commands("oak at 3pm for an hour", SetField(field="room", value="oak"),
                        SetField(field="start_time", value="15:00"), SetField(field="minutes", value=60))
    chat = agent.session("book-room").user("oak at 3pm for an hour")
    assert chat.state["fields"] == {"room": "oak", "start_time": "15:00", "minutes": 60}
    assert chat.awaiting == "confirm"
    # ...and a value outside the range is refused whoever supplies it.
    host.queue_commands("make it five minutes", SetField(field="minutes", value=5))
    chat.user("make it five minutes")
    assert chat.state["fields"]["minutes"] == 60
    assert "A booking is between 15 minutes and 8 hours." in chat.replies


def test_a_rule_on_a_field_reads_that_field_and_nothing_else(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "SKILL.md").write_text(
        "---\nname: x\ndescription: Test a field rule. Use for testing.\n"
        "fields:\n  a: { type: number, rules: [[\"a > b\", \"no\"]] }\n  b: { type: number }\n"
        "flow:\n  - ask: a\n  - ask: b\n---\nx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="A rule on a field reads that field and nothing else"):
        load_skills_dir(tmp_path, strict=True)


@pytest.mark.parametrize("message,times", [
    ("book redwood tomorrow at 3pm for an hour", {"15:00"}),
    ("at 3:30 PM please", {"15:30"}),
    ("9 a.m. works", {"09:00"}),
    ("15:00", {"15:00"}),
    ("12am", {"00:00"}),
    ("12 pm", {"12:00"}),
    ("let's say noon", {"12:00"}),
    ("10:15am or 22:45", {"10:15", "22:45"}),
    ("at 3 o'clock", set()),            # morning or afternoon would be the model's guess
    ("room 301 for 30 people", set()),
])
def test_a_time_the_user_stated_is_read_the_way_people_write_it(message, times):
    assert _message_times(message) == times


def test_a_time_grounds_only_the_time_that_was_said():
    said = "book redwood tomorrow at 3pm for an hour"
    assert _is_grounded_in_user("start_time", "15:00", said)
    assert not _is_grounded_in_user("start_time", "16:00", said), "a time nobody said is still dropped"
    assert not _is_grounded_in_user("start_time", "03:00", said)


# --------------------------------------------------------------------------- saying a result
def test_a_result_that_is_a_sentence_is_said_and_one_that_is_data_is_shown(tmp_path):
    agent, host, desk = _agent(tmp_path)
    host.queue_commands("hello")
    chat = agent.session("book-room").user("hello")
    chat.submit(room="oak").submit(start_time="10:00").submit(minutes=60).submit(tier="standard")
    # `{{price.text}}` is a sentence: said. `{{check_slot.label}}` is a record: a card.
    assert "That is 60 minutes on the standard tier." in chat.replies
    cards = host.drain_pending_widgets()
    assert len(cards) == 1 and "oak" in str(cards[0])
    chat.submit(confirm=True)
    assert chat.replies == ["Booked oak at 10:00."], "the tool's own sentence is what the user is told"
    assert desk.BOOKED == [("oak", "10:00", 60)]


# --------------------------------------------------------------------------- a designed page for the yes
def test_a_confirmation_can_have_a_name_and_a_designed_widget(tmp_path):
    md = ROOM.replace("  - confirm: Book it?\n", '''  - confirm: { go: "Book it?" }
    widget: review_page
    with: { summary: "{{check_slot.label}}", cost: "{{price.text}}" }
''')
    agent, host, desk = _agent(tmp_path, md)
    host.queue_commands("hello")
    chat = agent.session("book-room").user("hello")
    chat.submit(room="oak").submit(start_time="10:00").submit(minutes=60).submit(tier="standard")

    assert chat.awaiting == "go"
    assert chat.widget["widget"] == "review_page" and chat.widget["fields"] == ["go"]
    assert chat.widget["data"] == {"summary": {"Room": "oak", "From": "10:00"},
                                   "cost": "That is 60 minutes on the standard tier."}
    skill = agent.registry.get("book-room")
    assert skill.confirmation_fields == ["go"] and skill.fields["go"].type == "boolean"
    # It is still the yes: held to what would run, and nothing runs without it.
    chat.state["fields"]["minutes"] = 90
    chat.submit(go=True)
    assert desk.BOOKED == [] and chat.awaiting == "go"
    chat.submit(go=True)
    assert desk.BOOKED == [("oak", "10:00", 90)]


@pytest.mark.parametrize("line,needle", [
    ("  - confirm: Book it?\n    with: { a: 1 }\n", "goes with `widget:`"),
    ('  - confirm: { room: "Book it?" }\n', "already the name of an answer"),
    ('  - confirm: { "not a name": "Book it?" }\n', "not a usable name for a confirmation"),
])
def test_a_wrong_confirmation_says_what_to_change(tmp_path, line, needle):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "desk.py").write_text(DESK, encoding="utf-8")
    (tmp_path / "book-room").mkdir()
    (tmp_path / "book-room" / "SKILL.md").write_text(ROOM.replace("  - confirm: Book it?\n", line), encoding="utf-8")
    with pytest.raises(ValueError, match=needle):
        load_skills_dir(tmp_path, strict=True)


# --------------------------------------------------------------------------- lists
@pytest.mark.parametrize("field,keys,expected", [
    ("hotel_id", ["hotel", "address", "walk", "sample_rate", "osm_id"], "osm_id"),   # the row's one id
    ("flight_id", ["flight_id", "departs", "fare"], "flight_id"),
    ("client_id", ["id", "name"], "id"),
    ("hotel_id", ["osm_id", "chain_id", "name"], None),                              # two ids: say which
    ("hotel", ["osm_id", "name"], None),
])
def test_a_list_fills_an_id_answer_from_the_rows_one_id(field, keys, expected):
    assert _match_row_key(field, keys) == expected


# --------------------------------------------------------------------------- tools from an MCP server
def test_an_mcp_servers_tools_say_what_they_take_and_whether_they_only_read():
    pytest.importorskip("mcp")
    from aigenos_flow.mcp_tools import MCPTools
    from aigenos_flow.toolbox import describe_tool

    server = REPO / "examples" / "studio_agent" / "skills" / "conference-trip" / "mcp_server.py"
    if not server.is_file():
        pytest.skip("examples/ not present (packaged install)")
    with MCPTools([sys.executable, str(server)], env={"TRIP_OFFLINE": "1"},
                  only=["quote_trip", "hold_hotel", "find_hotels"]) as travel:
        tools = travel.tools()
        assert sorted(tools) == ["find_hotels", "hold_hotel", "quote_trip"]
        quote = describe_tool("quote_trip", tools["quote_trip"])
        assert quote.readonly is True and quote.style == "invoke"
        assert [(p.name, p.type, p.required) for p in quote.params][-1] == ("nights", "integer", True)
        # No read-only hint means it changes something: a flow needs a yes first.
        assert describe_tool("hold_hotel", tools["hold_hotel"]).readonly is False
        found = tools["find_hotels"]({})
        assert found["source"] == "OpenStreetMap (snapshot)" and travel.calls == [("find_hotels", {})]

    with MCPTools([sys.executable, str(server)], env={"TRIP_OFFLINE": "1"},
                  only=["hold_hotel"], readonly=["hold_hotel"]) as travel:
        assert travel.describe()["hold_hotel"].readonly is True, "the operator's own statement about a server"

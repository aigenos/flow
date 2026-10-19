"""Pin-tests for the imperative-flow outline (canvas read-only view)."""
from pathlib import Path

from aigenos_flow.outline import outline_folder, outline_handlers

SRC = '''
from aigenos_flow import action, flow

@flow
def book_meeting_room(ctx):
    ctx.ask("date", prompt="What day?")
    ctx.ask("room")
    avail = ctx.run(check_room)
    if not avail["available"]:
        ctx.say("Sorry, taken.")
        return
    ctx.say("Booking summary...")
    ctx.ask("confirm")
    if not ctx.fields["confirm"]:
        ctx.say("Cancelled.")
        return
    result = ctx.run(book_room, name="book_room")
    ctx.show(result, title="Booking")
    ctx.chat({"tools": ["room_faq"], "greeting": "Questions?"})

@action
def check_room(state):
    return {}
'''


_VERBS = {"ask", "say", "do", "pick", "show", "check", "chat", "widget", "code"}


def test_outline_extracts_ctx_sequence():
    steps = outline_handlers(SRC)
    # Pick the verb key explicitly — steps carry extra keys ("title", "when")
    # and set iteration order varies with the process hash seed.
    verbs = [next(k for k in s if k in _VERBS) for s in steps]
    assert verbs == [
        "ask", "ask", "do",           # date, room, check_room
        "say", "code",                # guarded branch: say + return
        "say", "ask",                 # summary + confirm
        "say", "code",                # cancelled branch
        "do", "show", "chat",         # book, show result, chat
    ]
    assert steps[0] == {"ask": "date"}
    assert steps[2] == {"do": "check_room"}
    # branch guards annotate the nested steps
    assert steps[3]["when"] == "not avail['available']"
    assert steps[8]["when"] == "not ctx.fields['confirm']"
    # kwargs-resolved action name and chat config survive
    assert steps[9] == {"do": "book_room"}
    assert steps[10]["show"] == "result" and steps[10]["title"] == "Booking"
    assert steps[11]["chat"]["tools"] == ["room_faq"]


def test_outline_real_book_room_example():
    folder = Path(__file__).resolve().parents[2] / "examples" / "studio_agent" / "skills" / "book-meeting-room"
    if not folder.exists():
        return  # repo layout variant; the synthetic test above covers the logic
    out = outline_folder(folder)
    # book-meeting-room ships as steps (no schema block, no handlers.py @flow),
    # so the canvas edits it: one node per step, in the order they are written.
    assert out["mode"] == "steps" and out["editable"] is True
    asked = [name for s in out["steps"] if "ask" in s for name in s["ask"]]
    assert asked == ["date", "start_time", "duration_minutes", "room"]
    kinds = [next(k for k in ("ask", "say", "do", "confirm") if k in s) for s in out["steps"]]
    # The yes comes before the tool that changes something, and the tool's own
    # sentence is said after it.
    assert kinds[-3:] == ["confirm", "do", "say"]
    assert out["steps"][-2] == {"do": "book_room"}


def test_outline_declarative_folder_is_editable(tmp_path):
    d = tmp_path / "decl"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: decl\ndescription: x.\nfields:\n  q: {type: string}\n"
        "flow:\n  - ask: q\n---\nB.\n",
        encoding="utf-8",
    )
    out = outline_folder(d)
    assert out == {"mode": "declarative", "editable": True, "steps": [{"ask": "q"}]}


def test_a_skill_written_as_steps_outlines_as_the_canvas_column(tmp_path):
    """The column the Studio's canvas reads from the file: a block at the top is a
    header and its steps. A column holds one level of blocks, so a `when:` block
    holding a `repeat:` is written out, and the repeat keeps its header."""
    d = tmp_path / "order-food"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: order-food\ndescription: Order food.\nsteps:\n"
        "  - ask: { group: \"Ordering for a group?\" }\n"
        "  - when: group\n"
        "    steps:\n"
        "      - say: \"Add each person's order.\"\n"
        "      - repeat: { orders: \"Add another order?\" }\n"
        "        steps:\n"
        "          - ask: { person: \"Who is it for?\" }\n"
        "          - ask: { dish: \"Which dish?\" }\n"
        "  - when: not group\n"
        "    steps:\n"
        "      - ask: { dish: \"Which dish?\" }\n"
        "  - confirm: Place the order?\n"
        "  - do: place_order\n"
        "---\nBody.\n",
        encoding="utf-8",
    )
    assert outline_folder(d)["steps"] == [
        {"ask": {"group": "Ordering for a group?"}},
        {"say": "Add each person's order.", "when": "group"},
        {"_block": {"id": "b1", "repeat": {"orders": "Add another order?"}, "when": "group"}},
        {"ask": {"person": "Who is it for?"}, "_in": "b1"},
        {"ask": {"dish": "Which dish?"}, "_in": "b1"},
        {"_block": {"id": "b2", "when": "not group"}},
        {"ask": {"dish": "Which dish?"}, "_in": "b2"},
        {"confirm": "Place the order?"},
        {"do": "place_order"},
    ]


def test_outline_empty_folder(tmp_path):
    d = tmp_path / "none"
    d.mkdir()
    (d / "SKILL.md").write_text(
        "---\nname: none\ndescription: x.\n---\nJust instructions.\n", encoding="utf-8"
    )
    assert outline_folder(d) == {"mode": "none", "editable": True, "steps": []}


ROUTER = '''
from aigenos_flow import flow

@flow
def send_money(ctx):
    if ctx.ask("new_payee"):
        if not ctx.link("add-payee"):
            ctx.say("No payee was added.")
            return
    if ctx.link("pay-someone"):
        ctx.say("Done.")
'''


def test_a_question_or_a_hand_off_used_as_a_condition_is_still_a_step():
    """`if ctx.ask(...)` and `if ctx.link(...)` are how a router flow branches.
    The outline used to show only the guarded bodies, so the Canvas drew a
    router with no question and no hand-offs in it."""
    steps = outline_handlers(ROUTER)
    assert steps[0] == {"ask": "new_payee"}
    assert steps[1] == {"code": "hand off to add-payee, then come back here", "when": "ctx.ask('new_payee')"}
    assert steps[2]["say"] == "No payee was added."
    assert {"code": "hand off to pay-someone, then come back here"} in steps
    assert steps.index({"code": "hand off to pay-someone, then come back here"}) > 2

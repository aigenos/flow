"""THE WIRE CONTRACT between the Python package and the npm package.

Every test here pins something no single-language test covers: the exact bytes a
router puts on the wire against the exact keys the shipped renderer and the
shared protocol parser read. The bugs these tests exist to prevent were all
invisible from inside Python — the flow paused correctly, the gate held, the
money was guarded, and the browser showed nothing.

Companion check for the JS half (all three protocols parse to identical
messages) — no dependencies, no build step:

    cd frontend && node --experimental-strip-types \\
        src/agui/__checks__/verify-protocol-parser.mjs

No LLM and no API keys: a TestHost or a structured-output-only scripted model
drives the engine.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from aigenos_flow import autowidget
from aigenos_flow.agui import events as ev
from aigenos_flow.agui.protocols.mcp_ui import to_mcp_ui_sse
from aigenos_flow.hosts.test_host import TestHost
from aigenos_flow.integration import create_agent
from aigenos_flow.integrations.flow import (
    auto_llm_info,
    confirmation_widget,
    describe_llm,
    make_flow_router,
    resolve_widget_for_field,
)
from aigenos_flow.integrations.langgraph import (
    ChatRequest,
    make_chat_router,
    resume_url_for,
    to_widget_payload,
    widget_from_interrupt,
)
from aigenos_flow.interpret import SetField, SkillChoice, TurnInterpretation
from aigenos_flow.schemas.widget_spec import SpecNode
from aigenos_flow.skills import load_skills_dir

REPO = Path(__file__).resolve().parents[2]
FRONT_DOOR_SKILL = REPO / "examples/before_after/skills_after/transfer-funds"
OPENING = "send 5000 to mom"

PROTOCOLS = ("ag-ui", "a2ui", "mcp-ui")


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

#: Fallback copy of the same skill, so this suite stays self-sufficient if the
#: examples tree moves. Keep in sync with FRONT_DOOR_SKILL (a mismatch only
#: costs fidelity to the published example, never correctness of these tests).
_FRONT_DOOR_SKILL_MD = """---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000 and above require a 2FA code.
steps:
  - ask: { amount: "How much do you want to send? (USD)" }
    check: { amount > 0: "Enter an amount above zero." }
  - ask: { payee: "Who is it going to?" }
    choices: [mom, landlord, gym]
  - ask: { code: "Enter the 6-digit code we texted you." }
    when: amount >= 1000            # the 2FA gate, re-evaluated on every replay
    type: secret                    # never logged, never supplied by the model
    pattern: '^[0-9]{6}$'           # a shape, so "skip" cannot satisfy the gate
  - say: "You're sending ${{amount}} to {{payee}}."
  - confirm: Send this transfer?
  - do: bank_transfer               # changes something: unreachable until the yes
  - say: "Sent ${{amount}} to {{payee}}."
---
Help the user transfer money. Collect the amount and the payee.
(The body stays — it's now LLM context for extraction and tone. The 2FA rule
above no longer lives here, because prose can't enforce anything.)
"""

#: The same policy written in the long form, with a `min:` on the amount. Used
#: where a test is about what a field's own bounds put on the wire.
_LONG_FORM_SKILL_MD = """---
name: transfer-funds
description: Send money to a saved payee. Amounts of $1000 and above require a 2FA code.
allowed-tools: bank_transfer
fields:
  amount:
    type: number
    min: 0.01
    must_elicit: true
    description: How much do you want to send? (USD)
  recipient:
    type: enum
    values: [mom, landlord, gym]
    description: Who is it going to?
  verification_code:
    type: string
    required: false
    pattern: '^[0-9]{6}$'
    sensitive: true
    description: Enter the 6-digit code we texted you.
  confirm:
    type: boolean
    description: Send this transfer?
actions:
  submit_transfer:
    tool: bank_transfer
    requires: confirm
    args: { amount: "{{amount}}", payee: "{{recipient}}", otp: "{{verification_code}}" }
flow:
  - ask: amount
  - ask: recipient
  - when: "amount >= 1000"
    ask: verification_code
  - say: "Send {{amount}} to {{recipient}}?"
  - ask: confirm
  - when: "confirm"
    do: submit_transfer
---
Help the user transfer money. Collect the amount and the payee.
"""


def _skills_dir(tmp_path: Path, *, with_tools_py: bool) -> Path:
    """A private copy of the backend/README front-door skill."""
    dst = tmp_path / "skills"
    dst.mkdir(parents=True, exist_ok=True)
    if FRONT_DOOR_SKILL.is_dir():
        shutil.copytree(FRONT_DOOR_SKILL, dst / "transfer-funds")
    else:
        (dst / "transfer-funds").mkdir()
        (dst / "transfer-funds" / "SKILL.md").write_text(
            _FRONT_DOOR_SKILL_MD, encoding="utf-8"
        )
    if with_tools_py:
        # make_flow_router grants <skills_dir>/tools.py; the double records
        # every call to a JSON file so a test can assert the write fired.
        log = (tmp_path / "fired.json").as_posix()
        (dst / "tools.py").write_text(
            "import json, pathlib\n"
            f'LOG = pathlib.Path(r"{log}")\n'
            "def bank_transfer(args: dict) -> dict:\n"
            '    """Move money to a saved payee (test double)."""\n'
            "    prev = json.loads(LOG.read_text()) if LOG.exists() else []\n"
            "    prev.append(args)\n"
            "    LOG.write_text(json.dumps(prev))\n"
            '    return {"ok": True, **args}\n'
            'TOOLS = {"bank_transfer": bank_transfer}\n',
            encoding="utf-8",
        )
    return dst


def _fired(tmp_path: Path) -> list[dict]:
    log = tmp_path / "fired.json"
    return json.loads(log.read_text()) if log.exists() else []


def _scripted_structured(scripts: list[Any]):
    """A chat model that only ever answers structured-output calls."""

    class _Scripted(GenericFakeChatModel):
        model: str = "scripted-1"

        def bind_tools(self, *a, **k):  # noqa: ANN001, ANN003
            return self

        def with_structured_output(self, schema, **k):  # noqa: ANN001, ANN003
            class _R:
                def invoke(self, messages, **kk):  # noqa: ANN001, ANN003
                    for obj in scripts:
                        if isinstance(obj, schema):
                            return obj
                    raise AssertionError(f"nothing scripted for {schema.__name__}")

            return _R()

    return _Scripted(messages=iter([AIMessage(content="")] * 200))


def _frames(response) -> list[tuple[str, dict]]:
    """Parse a captured SSE body into (event, data) pairs."""
    out: list[tuple[str, dict]] = []
    text = response.content.decode("utf-8").replace("\r\n", "\n")
    for block in text.split("\n\n"):
        name, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((name, json.loads(data)))
    return out


def _widgets(frames: list[tuple[str, dict]]) -> list[dict]:
    """Every widget payload on the wire, whatever the protocol calls it.

    This is the reader half of the contract: the keys the shared parser in
    frontend/src/agui/protocol.ts pulls the spec out of, per protocol.
    """
    out = []
    for name, data in frames:
        if name == "TOOL_CALL_ARGS":
            out.append(json.loads(data["delta"]))
        elif name == "a2ui.ui_resource":
            out.append(data)
        elif name == "notifications/resources/updated":
            out.append(json.loads(data["text"]))
    return out


def _texts(frames: list[tuple[str, dict]]) -> list[str]:
    out = []
    for name, data in frames:
        if name == "TEXT_MESSAGE_CONTENT":
            out.append(data["delta"])
        elif name == "a2ui.message.delta":
            out.append(data["delta"])
        elif name == "notifications/message/delta":
            out.append("".join(c.get("text", "") for c in data.get("contents") or []))
    return out


def _results(frames: list[tuple[str, dict]]) -> list[Any]:
    out = []
    for name, data in frames:
        if name == "TOOL_CALL_RESULT":
            out.append(json.loads(data["content"]))
        elif name == "a2ui.ui_resource_result":
            out.append(data["result"])
        elif name == "notifications/tools/result":
            out.append(json.loads("".join(c["text"] for c in data["content"])))
    return out


def _snapshot(frames: list[tuple[str, dict]]) -> dict | None:
    for name, data in frames:
        if name == "STATE_SNAPSHOT":
            return data["snapshot"]
        if name == "a2ui.state":
            return data["snapshot"]
        if name == "notifications/state/snapshot":
            return data["state"]
    return None


def _first_field_node(payload: dict) -> dict:
    """The input/select node inside an auto-generated field widget."""
    card = payload["spec"]
    form = card["children"][0]
    return form["children"][0]


def _answers(payload: dict) -> list[str]:
    """The answers a widget can give, whatever it is drawn with: the name of
    each input or select, and what each submitting button sends."""
    found: list[str] = []

    def walk(node: dict) -> None:
        props = node.get("props") or {}
        if node.get("type") in ("input", "select") and props.get("name"):
            found.append(str(props["name"]))
        if node.get("type") == "button" and isinstance(props.get("payload"), dict):
            found.extend(str(k) for k in props["payload"])
        for child in node.get("children") or []:
            walk(child)

    walk(payload["spec"])
    return sorted(set(found))


# --------------------------------------------------------------------------- #
# wire-1 / wire-9 / wire-18 — make_chat_router over create_agent()
# --------------------------------------------------------------------------- #

def _chat_router_client(tmp_path: Path, fired: list[dict]) -> TestClient:
    @tool
    def bank_transfer(amount: float, payee: str, code: str | None = None) -> dict:
        """Move money to a saved payee."""
        fired.append({"amount": amount, "payee": payee, "code": code})
        return {"ok": True, "confirmation": "CONF-1"}

    bank_transfer.name = "bank_transfer"

    host = TestHost()
    host.queue_commands(
        OPENING,
        SetField(field="amount", value=5000),
        SetField(field="payee", value="mom"),
    )

    class _Model(GenericFakeChatModel):
        def bind_tools(self, *a, **k):  # noqa: ANN001, ANN003
            return self

    activation = AIMessage(
        content="",
        tool_calls=[{"name": "activate_skill", "args": {"skill_name": "transfer-funds"}, "id": "c1"}],
    )
    agent = create_agent(
        _Model(messages=iter([activation] * 40)),
        str(_skills_dir(tmp_path, with_tools_py=False)),
        tools=[bank_transfer],
        host=host,
    )
    app = FastAPI()
    app.include_router(make_chat_router(agent), prefix="/chat")
    return TestClient(app)


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_chat_router_streams_the_paused_widget(tmp_path: Path, protocol: str) -> None:
    """wire-1: a create_agent() flow that pauses must put its widget on the wire.

    The stream used to gate the whole widget path on an interrupt payload
    saying `kind: "render_widget"`, which a flow pause never sets, and to read
    assistant text from a node called "agent", which this graph does not have.
    Result: RUN_STARTED then RUN_FINISHED, on all three protocols, while the
    README promised the spec "rides on the paused assistant message, so your
    AG-UI/A2UI client renders it from the stream".
    """
    fired: list[dict] = []
    client = _chat_router_client(tmp_path, fired)
    frames = _frames(
        client.post(f"/chat?protocol={protocol}", json={"thread_id": "T", "message": OPENING})
    )

    assert _texts(frames), f"{protocol}: no assistant text on the wire: {[n for n, _ in frames]}"
    widgets = _widgets(frames)
    assert len(widgets) == 1, f"{protocol}: expected one widget, got {len(widgets)}"
    w = widgets[0]
    assert w["widget_id"] == "flow_transfer-funds"
    assert w["spec"]["type"] == "card"
    assert w["submit_schema"], "the widget must carry the schema its submission is checked against"
    assert _first_field_node(w)["props"]["name"] == "code"

    snap = _snapshot(frames)
    assert snap and snap["status"] == "awaiting_input", snap
    assert snap["awaiting_field"] == "code"
    assert snap["done"] is False
    assert not fired, "nothing may fire on the turn that only asks for the code"


@pytest.mark.parametrize("protocol", PROTOCOLS)
def test_chat_router_round_trip_resolves_the_widget_and_reports_done(
    tmp_path: Path, protocol: str
) -> None:
    """wire-18: a client must be able to tell 'accepted' and 'finished' apart.

    With the front-door skill exactly as published, the confirm turn moved the
    money and streamed RUN_STARTED + RUN_FINISHED — byte-identical to a turn
    where nothing happened. Now the resume turn settles the widget it is
    answering (TOOL_CALL_RESULT) and every run ends with a state snapshot.
    """
    fired: list[dict] = []
    client = _chat_router_client(tmp_path, fired)
    client.post(f"/chat?protocol={protocol}", json={"thread_id": "T", "message": OPENING})

    second = _frames(
        client.post(
            f"/chat?protocol={protocol}",
            json={"thread_id": "T", "resume_payload": {"code": "481920"}},
        )
    )
    assert _results(second) == [{"code": "481920"}]
    assert _answers(_widgets(second)[0]) == ["confirm"]
    assert _snapshot(second)["status"] == "awaiting_input"
    assert not fired

    third = _frames(
        client.post(
            f"/chat?protocol={protocol}",
            json={"thread_id": "T", "resume_payload": {"confirm": "true"}},
        )
    )
    assert _results(third) == [{"confirm": "true"}]
    snap = _snapshot(third)
    assert snap["status"] == "finished" and snap["done"] is True, snap
    assert fired == [{"amount": 5000.0, "payee": "mom", "code": "481920"}]


def test_a_pause_streams_its_widget_and_prompt_exactly_once(tmp_path: Path) -> None:
    """A flow pause hands the SAME widget over twice.

    Once on the paused assistant message's ``aigenos_widget``, once in the
    interrupt payload. Streaming both would stack two skeletons for one
    question, and the prompt would appear twice in the transcript.
    """
    client = _chat_router_client(tmp_path, [])
    frames = _frames(client.post("/chat?protocol=ag-ui", json={"thread_id": "once", "message": OPENING}))
    names = [n for n, _ in frames]
    assert names.count("TOOL_CALL_START") == 1, names
    assert names.count("TOOL_CALL_ARGS") == 1, names
    texts = _texts(frames)
    assert len(texts) == len(set(texts)), texts
    # The ask's wording is the FIELD'S `description:` (a declarative `ask:` reads
    # nothing else — `prompt:` on an ask step is a load error). The front-door
    # skill gives every field a short human question, so that is what streams;
    # the generic "Please provide a value for 'x'." is only the no-description
    # fallback. Asserting the fallback pinned a worse skill, not a wire contract.
    from aigenos_flow.skills import parse_skill_md

    front, _body = parse_skill_md(
        (tmp_path / "skills/transfer-funds/SKILL.md").read_text(encoding="utf-8")
    )
    asked = next(s["ask"]["code"] for s in front.steps if "code" in (s.get("ask") or {}))
    assert asked and asked in texts, (asked, texts)


def test_render_widget_takes_a_registered_id_not_a_spec() -> None:
    """wire-10: the tool's parameters are (widget_id, props) — never a spec.

    The docs claimed the LLM chooses "what spec to send"; it cannot, and a
    client snippet reading `args.spec` gets undefined. Pinned here so the code
    and the docs cannot drift apart again.
    """
    from aigenos_flow.integrations.langgraph import render_widget

    assert set(render_widget.args_schema.model_fields) == {"widget_id", "props"}
    out = render_widget.invoke({"widget_id": "definitely_not_registered"})
    assert "_error" in out and "unknown widget" in out["_error"]


def test_chat_request_accepts_skill_and_refuses_unknown_keys() -> None:
    """wire-9: useFlowChat sends `skill` on every opening turn.

    It used to be dropped silently — no 422, no warning, and the pin did
    nothing. extra="forbid" means a future body-shape drift fails loudly too.
    """
    assert "skill" in ChatRequest.model_fields
    req = ChatRequest(**{"thread_id": "x", "message": "hi", "skill": "transfer-funds"})
    assert req.skill == "transfer-funds"
    with pytest.raises(Exception):
        ChatRequest(**{"message": "hi", "bogus_key": 1})


def test_chat_router_honours_the_skill_pin(tmp_path: Path) -> None:
    """wire-9: the pin must actually enter the skill, with a fresh run id."""
    fired: list[dict] = []
    client = _chat_router_client(tmp_path, fired)
    frames = _frames(
        client.post(
            "/chat?protocol=ag-ui",
            json={"thread_id": "pinned", "message": OPENING, "skill": "transfer-funds"},
        )
    )
    assert _widgets(frames), [n for n, _ in frames]
    assert _snapshot(frames)["flow"] == "transfer-funds"


def test_chat_router_refuses_a_skill_pin_it_cannot_honour() -> None:
    """A plain ReAct graph has no flow to pin. Refuse, never accept-and-ignore."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent

    class _Model(GenericFakeChatModel):
        def bind_tools(self, *a, **k):  # noqa: ANN001, ANN003
            return self

    @tool
    def noop(x: str) -> str:
        """Do nothing."""
        return x

    agent = create_react_agent(
        _Model(messages=iter([AIMessage(content="hi")] * 5)),
        tools=[noop],
        checkpointer=MemorySaver(),
    )
    app = FastAPI()
    app.include_router(make_chat_router(agent), prefix="/chat")
    client = TestClient(app)
    r = client.post("/chat", json={"message": "hello", "skill": "transfer-funds"})
    assert r.status_code == 400
    assert "make_flow_router" in r.text


def test_chat_router_unknown_body_key_is_a_422() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.prebuilt import create_react_agent

    class _Model(GenericFakeChatModel):
        def bind_tools(self, *a, **k):  # noqa: ANN001, ANN003
            return self

    @tool
    def noop(x: str) -> str:
        """Do nothing."""
        return x

    agent = create_react_agent(
        _Model(messages=iter([AIMessage(content="hi")] * 5)),
        tools=[noop],
        checkpointer=MemorySaver(),
    )
    app = FastAPI()
    app.include_router(make_chat_router(agent), prefix="/chat")
    assert TestClient(app).post("/chat", json={"message": "hi", "bogus": 1}).status_code == 422


# --------------------------------------------------------------------------- #
# Interrupt-payload normalisation (the root cause of wire-1)
# --------------------------------------------------------------------------- #

def test_widget_from_interrupt_handles_both_producers() -> None:
    """Two interrupt producers, two shapes, one wire payload."""
    # render_widget's own payload.
    rw = widget_from_interrupt({
        "kind": "render_widget",
        "widget_id": "order_summary",
        "version": "2.0.0",
        "spec": {"type": "card"},
        "submit_schema": {"type": "object"},
    })
    assert rw == {
        "widget_id": "order_summary",
        "version": "2.0.0",
        "spec": {"type": "card"},
        "submit_schema": {"type": "object"},
    }

    # A flow pause: no `kind` at all, and the WidgetSpec dump calls it `id`.
    flow_pause = widget_from_interrupt({
        "prompt": "Please provide a value for 'confirm'.",
        "field": "confirm",
        "flow_id": "transfer-funds",
        "widget": {
            "id": "flow_transfer-funds",
            "version": "1.0.0",
            "spec": {"type": "card"},
            "submit_schema": {"type": "object"},
        },
    })
    assert flow_pause is not None
    assert flow_pause["widget_id"] == "flow_transfer-funds"
    assert flow_pause["spec"] == {"type": "card"}

    # A pause with no widget at all must not fabricate one.
    assert widget_from_interrupt({"prompt": "hi", "field": "x", "flow_id": "y"}) is None
    assert widget_from_interrupt("just a string") is None


def test_to_widget_payload_wraps_a_bare_spec_node() -> None:
    """`show:` hands us an autowidget SpecNode, not a WidgetSpec envelope."""
    spec = autowidget.spec_for({"room": "redwood"}, title="Booking")
    payload = to_widget_payload(spec)
    assert payload is not None
    assert payload["spec"] is spec
    assert payload["submit_schema"] is None, "a display-only widget cannot be submitted"


# --------------------------------------------------------------------------- #
# wire-5 / wire-6 / wire-7 — the props the emitters write
# --------------------------------------------------------------------------- #
#
# The renderer's text primitive reads props.value (and is tolerant of the older
# names); its only emphasis prop is `muted`. `variant` is read by nothing.
# The Timeline reads props.events of {time, title, description}. Writing a prop
# nothing reads renders an EMPTY element, which is silent on this side.


def _nodes(spec: dict):
    yield spec
    for child in spec.get("children") or []:
        yield from _nodes(child)


def test_autowidget_text_nodes_use_the_prop_the_renderer_reads() -> None:
    spec = autowidget.spec_for({"room": "redwood", "seats": 8}, title="Booking")
    texts = [n for n in _nodes(spec) if n["type"] == "text"]
    assert len(texts) == 4, "two label/value pairs"
    for node in texts:
        props = node["props"]
        assert "value" in props, f"text node writes {sorted(props)}, renderer reads `value`"
        assert "text" not in props and "content" not in props
        assert "variant" not in props, "`variant` is not a declared text prop"
        assert set(props) <= {"value", "muted", "weight"}
    assert [n["props"]["value"] for n in texts] == ["Room", "redwood", "Seats", "8"]

    scalar = autowidget.spec_for("TXN-1", title="Receipt")
    assert scalar["children"][0]["props"] == {"value": "TXN-1"}


def test_confirmation_widget_summary_is_visible() -> None:
    """The user cannot be asked to confirm a transfer with a blank card."""
    w = confirmation_widget(skill_name="transfer-funds", summary="Send $5000 to mom?")
    node = w.spec.children[0]
    assert node.type == "text"
    assert node.props == {"value": "Send $5000 to mom?"}


def test_autowidget_timeline_emits_the_events_the_renderer_reads() -> None:
    spec = autowidget.spec_for(
        [
            {"date": "2026-05-25", "title": "Standup", "room": "redwood"},
            {"date": "2026-05-26", "title": "Review"},
        ],
        title="Agenda",
    )
    timeline = spec["children"][0]
    assert timeline["type"] == "timeline"
    assert set(timeline["props"]) == {"events"}, timeline["props"]
    assert timeline["props"]["events"][0]["time"] == "2026-05-25"
    assert timeline["props"]["events"][0]["title"] == "Standup"
    assert "redwood" in timeline["props"]["events"][0]["description"]
    assert timeline["props"]["events"][1] == {"time": "2026-05-26", "title": "Review"}


@pytest.mark.parametrize(
    "row",
    [
        {"room": "redwood", "seats": 8},
        {"category": "food", "amount": 12},
        {"status": "ok"},
        {"rating": 5},
        {"latitude": 1},
        {"state": "CA"},
        {"treatment": "x"},
    ],
)
def test_ordinary_columns_are_not_dates(row: dict) -> None:
    """wire-7: "at" is a substring of seats/status/category/rating/latitude/state.

    A substring test routed every one of those tables into a timeline, and the
    rows vanished. The grep-visible symptom was `_date_key({'room': ...,
    'seats': 8}) == 'seats'`.
    """
    assert autowidget._date_key(row) is None, f"{row} was treated as dated"


@pytest.mark.parametrize(
    "key",
    ["date", "dates", "timestamp", "when", "time", "created_at", "due_date",
     "start_time", "posted_on", "occurred_on", "updated_at", "event_datetime"],
)
def test_genuinely_dated_keys_still_select_a_timeline(key: str) -> None:
    assert autowidget.is_dateish(key), key
    assert autowidget._date_key({key: "x", "note": "y"}) == key


def test_a_table_of_rooms_is_a_datatable_not_a_timeline() -> None:
    spec = autowidget.spec_for(
        [{"room": "redwood", "seats": 8}, {"room": "oak", "seats": 4}], title="Rooms"
    )
    body = spec["children"][0]
    assert body["type"] == "datatable"
    assert [c["key"] for c in body["props"]["columns"]] == ["room", "seats"]
    assert len(body["props"]["rows"]) == 2


# --------------------------------------------------------------------------- #
# wire-8 — the `skill` prop live validation needs
# --------------------------------------------------------------------------- #

@pytest.fixture()
def flow_client(tmp_path: Path):
    skills = _skills_dir(tmp_path, with_tools_py=True)
    llm = _scripted_structured([
        SkillChoice(skill="transfer-funds", reason="scripted"),
        TurnInterpretation(items=[
            SetField(field="amount", value=5000),
            SetField(field="payee", value="mom"),
        ]),
    ])
    app = FastAPI()
    app.include_router(make_flow_router(skills_dir=skills, llm=llm), prefix="/flow")
    return TestClient(app), tmp_path, skills


@pytest.mark.parametrize("field", ["amount", "code", "payee", "confirm"])
def test_generated_inputs_carry_the_skill_name(tmp_path: Path, field: str) -> None:
    """wire-8: without it, /flow/validate-field answers `valid: true` for -5.

    The endpoint resolves a FieldSpec from (skill, field). The shipped <Input>
    posts the `skill` prop; nothing put one on the node, so every inline check
    passed and an invalid value showed a green tick.
    """
    skills = _skills_dir(tmp_path, with_tools_py=False)
    from aigenos_flow.declarative import ToolRegistry

    reg = ToolRegistry()
    reg.register("bank_transfer", lambda args, ctx=None: {"ok": True})
    skill = load_skills_dir(skills, tool_registry=reg).get("transfer-funds")

    widget = resolve_widget_for_field(skill=skill, field_name=field)
    node = widget.spec.children[0].children[0]
    assert node.props.get("skill") == "transfer-funds", node.props


def test_a_decimal_field_gets_a_number_input_the_browser_will_actually_submit(tmp_path: Path) -> None:
    """`amount` is `type: number, min: 0.01`. A number input with a `min` and no
    `step` only accepts min, min+1, min+2 ...: the browser called 2600 and 49.99
    invalid ("nearest valid values are 2599.01 and 2600.01") and refused to
    submit the form. The generated input now says `step="any"`."""
    skills = tmp_path / "skills"
    (skills / "transfer-funds").mkdir(parents=True)
    (skills / "transfer-funds" / "SKILL.md").write_text(_LONG_FORM_SKILL_MD, encoding="utf-8")
    from aigenos_flow.declarative import ToolRegistry

    reg = ToolRegistry()
    reg.register("bank_transfer", lambda args, ctx=None: {"ok": True})
    skill = load_skills_dir(skills, tool_registry=reg).get("transfer-funds")

    node = resolve_widget_for_field(skill=skill, field_name="amount").spec.children[0].children[0]
    assert node.props["type"] == "number"
    assert node.props["min"] == 0.01
    assert node.props["step"] == "any"


def test_a_number_asked_in_the_short_form_is_submittable_too(tmp_path: Path) -> None:
    """No `min:` to count steps from, and the same `step="any"`."""
    skills = _skills_dir(tmp_path, with_tools_py=False)
    from aigenos_flow.declarative import ToolRegistry

    reg = ToolRegistry()
    reg.register("bank_transfer", lambda args, ctx=None: {"ok": True})
    skill = load_skills_dir(skills, tool_registry=reg).get("transfer-funds")
    node = resolve_widget_for_field(skill=skill, field_name="amount").spec.children[0].children[0]
    assert node.props["type"] == "number" and node.props["step"] == "any"


@pytest.mark.parametrize(
    "field,value,valid",
    [
        ("amount", "-5", False),
        ("amount", "abc", False),
        ("amount", "25", True),
        ("amount", "0", False),
        ("code", "12", False),
        ("code", "481920", True),
        ("payee", "stranger", False),
        ("payee", "mom", True),
    ],
)
def test_validate_field_with_the_skill_the_node_now_carries(
    flow_client, field: str, value: str, valid: bool
) -> None:
    client, _, _ = flow_client
    r = client.post(
        "/flow/validate-field",
        json={"field": field, "value": value, "skill": "transfer-funds"},
    )
    assert r.status_code == 200
    assert r.json()["valid"] is valid, r.json()


def test_the_inline_tick_agrees_with_the_flows_own_rule(flow_client) -> None:
    """`check: { amount > 0: "Enter an amount above zero." }` reads only the
    answer it judges, so it is known while the user types. The validator used to
    see a number with no bounds and tick -5, one moment before the flow refused it."""
    client, _, _ = flow_client
    r = client.post("/flow/validate-field",
                    json={"field": "amount", "value": "-5", "skill": "transfer-funds"})
    assert r.json() == {"valid": False, "error": "Enter an amount above zero."}


def test_a_submitted_secret_never_reaches_the_server_log(flow_client, caplog) -> None:
    """`sensitive: true` says "never logged in clear". The router used to log the
    whole resume payload at INFO, so every 2FA code a user typed was in the log."""
    client, _, _ = flow_client
    with caplog.at_level("DEBUG"):
        _frames(client.post("/flow?protocol=ag-ui", json={"thread_id": "L", "message": OPENING}))
        _frames(client.post("/flow?protocol=ag-ui",
                            json={"thread_id": "L", "resume_payload": {"code": "481920"}}))
    # Our own loggers, at every level. (A DEBUG-level transport logger prints the
    # bytes it sends, which include the client's own submission echoed back to it.)
    logged = " | ".join(r.getMessage() for r in caplog.records if r.name.startswith("aigenos_flow"))
    assert "resume thread=L" in logged and "['code']" in logged, "the resume is still traceable by field name"
    assert "481920" not in logged


def test_the_skill_prop_survives_the_whole_round_trip(flow_client) -> None:
    """Emitting it is only half the fix — it has to reach the client."""
    client, _, _ = flow_client
    frames = _frames(client.post("/flow?protocol=ag-ui", json={"thread_id": "S", "message": OPENING}))
    node = _first_field_node(_widgets(frames)[0])
    assert node["props"]["skill"] == "transfer-funds"


# --------------------------------------------------------------------------- #
# wire-18 on make_flow_router, plus `pick:`/`show:` reaching the wire
# --------------------------------------------------------------------------- #

def test_flow_router_round_trip_is_readable_by_a_client(flow_client) -> None:
    client, tmp_path, _ = flow_client

    first = _frames(client.post("/flow?protocol=ag-ui", json={"thread_id": "R", "message": OPENING}))
    assert _snapshot(first)["status"] == "awaiting_input"
    assert _widgets(first)

    second = _frames(
        client.post(
            "/flow?protocol=ag-ui",
            json={"thread_id": "R", "resume_payload": {"code": "481920"}},
        )
    )
    assert _results(second) == [{"code": "481920"}]

    third = _frames(
        client.post("/flow?protocol=ag-ui", json={"thread_id": "R", "resume_payload": {"confirm": "true"}})
    )
    assert _results(third) == [{"confirm": "true"}], "the accepted widget must be settled"
    snap = _snapshot(third)
    assert snap["status"] == "finished" and snap["done"] is True
    assert _fired(tmp_path) == [{"amount": 5000.0, "payee": "mom", "code": "481920"}]


def test_confirm_and_decline_are_distinguishable_on_the_wire(flow_client) -> None:
    """The one case where silence is dangerous: the money moved or it did not."""
    client, tmp_path, _ = flow_client
    client.post("/flow", json={"thread_id": "D", "message": OPENING})
    client.post("/flow", json={"thread_id": "D", "resume_payload": {"code": "481920"}})
    declined = _frames(
        client.post("/flow?protocol=ag-ui", json={"thread_id": "D", "resume_payload": {"confirm": "false"}})
    )
    assert _fired(tmp_path) == []
    assert _results(declined) == [{"confirm": "false"}]
    assert any("no changes were made" in t.lower() for t in _texts(declined)), _texts(declined)


def test_resolve_widget_for_field_honours_the_runtime_pending_widget() -> None:
    """A `pick:` step builds its own grid; the wire must carry THAT, not an input.

    state['pending_widget'] was written by the runtime and read by nothing, so
    a selection grid arrived in the browser as a bare text input for the first
    field the grid would have filled.
    """
    grid, schema = autowidget.pick_spec_for(
        [{"account_number": "1", "full_name": "Ada"}, {"account_number": "2", "full_name": "Bo"}],
        ["account_number"],
        prompt="Pick an account",
    )
    skill = type("S", (), {"name": "accounts", "fields": {"account_number": None}, "widget": None})()
    resolved = resolve_widget_for_field(
        skill=skill,
        field_name="account_number",
        pending_widget={
            "widget": "pick:account_number",
            "data": {"rows": []},
            "fields": ["account_number"],
            "submit_schema": schema,
            "spec": grid,
        },
    )
    body = resolved.spec.children[0].children[0]
    assert body.type == "datatable", body.type
    assert len(body.props["rows"]) == 2
    assert resolved.submit_schema == schema


def test_show_widgets_reach_the_stream() -> None:
    """`show:` emits through the host's widget queue; the router must drain it."""
    import asyncio

    from aigenos_flow.integrations.flow import _flow_event_stream

    host = TestHost()
    host.emit_widget(autowidget.spec_for({"total": 42}, title="Receipt"))

    class _Graph:
        async def astream(self, *a, **k):  # noqa: ANN001, ANN003
            yield {"turn": {"pending_assistant_messages": ["Here you go."], "done": True}}

        def get_state(self, config):  # noqa: ANN001
            return type("S", (), {"values": {"done": True}})()

    async def collect():
        return [
            e
            async for e in _flow_event_stream(
                _Graph(), {}, {"configurable": {"thread_id": "x"}}, "x", None, host=host
            )
        ]

    events = asyncio.run(collect())
    kinds = [e.type for e in events]
    assert "TOOL_CALL_ARGS" in kinds, kinds
    args = json.loads(next(e for e in events if e.type == "TOOL_CALL_ARGS").delta)
    assert args["spec"]["props"]["title"] == "Receipt"
    assert args["submit_schema"] is None, "a `show:` card is display-only"
    assert not host.drain_pending_widgets(), "the queue must be drained, not copied"


# --------------------------------------------------------------------------- #
# wire-15 — llm-info reports the model in use
# --------------------------------------------------------------------------- #

class _FakeLLM:
    model = "fake-model-1"

    def bind_tools(self, *a, **k):  # noqa: ANN001, ANN003
        return self


def test_llm_info_reports_the_injected_model_not_the_environment(
    tmp_path: Path, monkeypatch
) -> None:
    """wire-15: `make_flow_router(llm=...)` is the ground truth.

    The endpoint branched purely on ANTHROPIC/OPENAI/GOOGLE_API_KEY, so it
    reported whatever key the box happened to hold (and told any unauthenticated
    caller which provider keys the server has).
    """
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(var, "sk-not-used-by-this-test")

    app = FastAPI()
    app.include_router(
        make_flow_router(skills_dir=_skills_dir(tmp_path, with_tools_py=True), llm=_FakeLLM()),
        prefix="/flow",
    )
    body = TestClient(app).get("/flow/llm-info").json()
    assert body["provider"] == "custom", body
    assert body["model"] == "fake-model-1", body
    assert body["detected"] is False


def test_describe_llm_maps_the_known_provider_classes() -> None:
    for cls_name, provider, hosted in [
        ("ChatAnthropic", "anthropic", True),
        ("ChatOpenAI", "openai", True),
        ("ChatGoogleGenerativeAI", "gemini", True),
        ("ChatOllama", "ollama", False),
    ]:
        obj = type(cls_name, (), {"model": "m-1"})()
        info = describe_llm(obj)
        assert (info["provider"], info["hosted"], info["model"]) == (provider, hosted, "m-1")
    assert describe_llm(None)["provider"] == "none"


def test_auto_llm_info_is_marked_as_a_prediction(monkeypatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "FG_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    info = auto_llm_info()
    assert info["provider"] == "anthropic"
    assert info["detected"] is True, "an env guess must never masquerade as an observation"


# --------------------------------------------------------------------------- #
# wire-17 — the mcp-ui submit target has to be resolvable
# --------------------------------------------------------------------------- #

async def test_mcp_ui_advertises_a_resume_target_that_exists() -> None:
    """wire-17: `ui-action://submit/<id>` had no handler anywhere in the product.

    A host that followed the payload's own instruction had nowhere to post.
    """
    async def src():
        yield ev.RunStarted(thread_id="T9", run_id="R9")
        yield ev.ToolCallStart(tool_call_id="tc1", tool_call_name="render_widget")
        yield ev.ToolCallArgs(
            tool_call_id="tc1",
            delta=json.dumps({"widget_id": "w", "version": "1", "spec": {"type": "card"}}),
        )
        yield ev.ToolCallEnd(tool_call_id="tc1")

    frames = [f async for f in to_mcp_ui_sse(src(), resume_url="/flow/T9/resume")]
    resource = next(f for f in frames if f["event"] == "notifications/resources/updated")
    interaction = json.loads(json.loads(resource["data"])["text"])["interaction"]
    assert "ui-action" not in json.dumps(interaction)
    assert interaction["resume"] == {
        "method": "POST",
        "url": "/flow/T9/resume",
        "content_type": "application/json",
        "body_is_submission": True,
        "thread_id": "T9",
        "tool_call_id": "tc1",
    }


async def test_mcp_ui_omits_the_resume_target_rather_than_guessing_one() -> None:
    """A surface that did not tell us its resume path gets no descriptor.

    Guessing is how `ui-action://submit/<id>` happened. The Studio's own
    /api/runs stream, for instance, resumes through /api/runs/{id}/submit.
    """
    async def src():
        yield ev.RunStarted(thread_id="T9", run_id="R9")
        yield ev.ToolCallStart(tool_call_id="tc1", tool_call_name="render_widget")
        yield ev.ToolCallArgs(tool_call_id="tc1", delta=json.dumps({"widget_id": "w", "spec": {"type": "card"}}))
        yield ev.ToolCallEnd(tool_call_id="tc1")

    frames = [f async for f in to_mcp_ui_sse(src())]
    resource = next(f for f in frames if f["event"] == "notifications/resources/updated")
    interaction = json.loads(json.loads(resource["data"])["text"])["interaction"]
    assert interaction == {"tool_call_id": "tc1"}


def test_the_advertised_resume_url_actually_answers(flow_client) -> None:
    """Not just resolvable in principle: POST it and the flow moves on."""
    client, _, _ = flow_client
    frames = _frames(client.post("/flow?protocol=mcp-ui", json={"thread_id": "M", "message": OPENING}))
    resource = next(d for n, d in frames if n == "notifications/resources/updated")
    resume = json.loads(resource["text"])["interaction"]["resume"]
    assert resume["url"] == "/flow/M/resume", resume
    assert resume["method"] == "POST" and resume["body_is_submission"] is True

    # Exactly what the descriptor says: POST the submission as the whole body.
    r = client.post(resume["url"] + "?protocol=mcp-ui", json={"code": "481920"})
    assert r.status_code == 200
    follow = _frames(r)
    assert _widgets(follow), "the advertised resume target must advance the flow"
    assert _answers(_widgets(follow)[0]) == ["confirm"]


def test_resume_url_is_absolute_under_any_mount_prefix() -> None:
    assert resume_url_for("/chat", "T1") == "/chat/T1/resume"
    assert resume_url_for("/api/agents/x/chat/", "T1") == "/api/agents/x/chat/T1/resume"
    # Called from the resume alias itself — do not append the suffix twice.
    assert resume_url_for("/chat/T1/resume", "T1") == "/chat/T1/resume"
    # No request in scope: a relative path still resolves client-side.
    assert resume_url_for(None, "T1") == "T1/resume"


# --------------------------------------------------------------------------- #
# The renderer's own contract, asserted from this side
# --------------------------------------------------------------------------- #

_TEXT_PROPS = {"value", "muted", "weight"}
_TIMELINE_EVENT_KEYS = {"time", "title", "description"}


@pytest.mark.parametrize(
    "spec",
    [
        autowidget.spec_for({"room": "redwood", "seats": 8}, title="Booking"),
        autowidget.spec_for("TXN-1", title="Receipt"),
        autowidget.spec_for([{"date": "2026-05-25", "title": "Standup"}], title="Agenda"),
        autowidget.spec_for([{"room": "redwood", "seats": 8}], title="Rooms"),
        autowidget.spec_for({"accounts": [{"id": "1", "name": "Ada"}]}),
        autowidget.pick_spec_for([{"id": "1", "name": "Ada"}], ["id"], prompt="Pick")[0],
        autowidget.pick_spec_for(
            [{"id": "1", "name": "Ada"}], ["id"], prompt="Pick", kind="autosuggest"
        )[0],
        confirmation_widget(skill_name="s", summary="Sure?").spec.model_dump(exclude_none=True),
    ],
)
def test_every_emitted_node_uses_a_prop_the_renderer_reads(spec: dict) -> None:
    """One shared oracle for both sides of the wire.

    The node-side companion (frontend/src/agui/__checks__/verify-protocol-parser.mjs)
    covers the parser; the rendering proof is in the fix report. This catches
    the Python half: a prop name the renderer does not read is an empty element
    in the browser and a passing test suite here.
    """
    for node in _nodes(spec):
        props = node.get("props") or {}
        if node["type"] == "text":
            assert set(props) <= _TEXT_PROPS, f"text node has undeclared props: {sorted(props)}"
            assert "value" in props
        if node["type"] == "timeline":
            assert set(props) == {"events"}, sorted(props)
            for event in props["events"]:
                assert set(event) <= _TIMELINE_EVENT_KEYS, sorted(event)
                assert "time" in event
        if node["type"] == "datatable":
            assert "rows" in props and "columns" in props, sorted(props)
            assert set(props) <= {"rows", "columns", "fields"}, sorted(props)
        if node["type"] == "autosuggest":
            assert set(props) <= {"label", "placeholder", "display", "options", "fields"}


def test_a_widget_payload_carries_every_key_the_parser_reads(flow_client) -> None:
    """frontend/src/agui/protocol.ts reads exactly these four keys."""
    client, _, _ = flow_client
    for protocol in PROTOCOLS:
        tid = uuid.uuid4().hex
        frames = _frames(
            client.post(f"/flow?protocol={protocol}", json={"thread_id": tid, "message": OPENING})
        )
        payload = _widgets(frames)[0]
        assert {"widget_id", "version", "spec", "submit_schema"} <= set(payload), sorted(payload)
        assert isinstance(payload["spec"], dict) and payload["spec"].get("type")


def test_spec_node_accepts_the_props_the_emitters_write() -> None:
    """A guard against a schema tightening that would drop these silently."""
    node = SpecNode(type="text", props={"value": "hi", "muted": True})
    assert node.model_dump(exclude_none=True)["props"] == {"value": "hi", "muted": True}

"""The Studio's tool library and "check before save" endpoints.

A tool is a Python function that many skills may name, so writing one from the
browser is held to more than "does it import": a change that would break a
skill that loads today is refused and put back. And a skill being drawn on the
canvas is checked by the loader itself, without being saved, so the mistake is
named on its step.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "_server_sandbox", Path(__file__).with_name("test_server_security.py")
)
_sandbox = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sandbox)

BANK = '''"""A pretend till."""
from aigenos_flow import readonly

TILL = {"balance": 100.0}


@readonly
def get_balance() -> dict:
    """What is in the till."""
    return {"balance": TILL["balance"]}


def withdraw(amount: float) -> dict:
    """Take money out of the till."""
    TILL["balance"] -= amount
    return {"balance": TILL["balance"]}
'''

CASH = """---
name: take-cash
description: Take cash out of the till. Use when the user wants to withdraw money.
steps:
  - say: "The till holds {{get_balance.balance}}."
  - ask: { amount: "How much?" }
    check: { amount > 0: "Enter an amount above zero." }
  - confirm: Take it out?
  - do: withdraw
  - say: "Done. {{withdraw.balance}} left."
---
Help the user take cash out.
"""


@pytest.fixture
def studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    main_mod = _sandbox._fresh_app(tmp_path, monkeypatch)

    class Studio:
        client = TestClient(main_mod.app, raise_server_exceptions=False)
        skills = tmp_path / "skills"

        def put_file(self, path: str, source: str):
            return self.client.put("/flow/tools/files", json={"path": path, "source": source})

        def tools(self) -> dict:
            return {t["name"]: t for t in self.client.get("/flow/tools").json()["tools"]}

        def check(self, md: str, name: str | None = None) -> dict:
            return self.client.post("/flow/skills/check", json={"skill_md": md, "name": name}).json()

    return Studio()


def _with_bank_and_skill(studio) -> None:
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    made = studio.client.post("/flow/skills", json={"name": "take-cash", "skill_md": CASH})
    assert made.status_code == 200, made.text


# --------------------------------------------------------------------------- writing a tool from the browser
def test_a_saved_file_is_a_set_of_tools_every_skill_can_name(studio):
    saved = studio.put_file("tools/bank.py", BANK)
    assert saved.status_code == 200, saved.text
    assert (studio.skills / "tools" / "bank.py").read_text(encoding="utf-8") == BANK

    tools = studio.tools()
    assert tools["get_balance"]["changes_something"] is False
    assert tools["get_balance"]["description"] == "What is in the till."
    assert tools["withdraw"]["changes_something"] is True, "not marked @readonly means it changes something"
    assert tools["withdraw"]["inputs"] == [
        {"name": "amount", "type": "number", "required": True, "description": ""}
    ]
    assert tools["withdraw"]["source"] == "tools/bank.py"
    assert tools["save_request"]["source"] == "built in"

    listed = studio.client.get("/flow/tools/files").json()
    assert [(f["path"], f["tools"]) for f in listed["files"]] == [("tools/bank.py", ["get_balance", "withdraw"])]


def test_the_tool_list_says_which_skills_use_each_tool(studio):
    _with_bank_and_skill(studio)
    tools = studio.tools()
    assert tools["withdraw"]["used_by"] == ["take-cash"] and tools["get_balance"]["used_by"] == ["take-cash"]
    assert tools["save_request"]["used_by"] == []


def test_python_that_does_not_parse_is_refused_with_the_line(studio):
    refused = studio.put_file("tools/bank.py", "def broken(:\n    pass\n")
    assert refused.status_code == 422
    assert "not valid Python (line 1" in refused.json()["detail"]
    assert not (studio.skills / "tools" / "bank.py").exists()


def test_a_change_that_would_break_a_skill_is_refused_and_put_back(studio):
    _with_bank_and_skill(studio)
    # `withdraw` is renamed away: take-cash names a tool that would not exist.
    refused = studio.put_file("tools/bank.py", BANK.replace("def withdraw(", "def take_out("))
    assert refused.status_code == 422
    detail = refused.json()["detail"]
    assert "would break take-cash" in detail and "withdraw" in detail
    assert (studio.skills / "tools" / "bank.py").read_text(encoding="utf-8") == BANK
    assert "withdraw" in studio.tools(), "the live library is still the one that works"

    gone = studio.client.delete("/flow/tools/files", params={"path": "tools/bank.py"})
    assert gone.status_code == 422 and "would break take-cash" in gone.json()["detail"]
    assert (studio.skills / "tools" / "bank.py").exists()


def test_a_tool_nobody_uses_can_be_changed_and_removed(studio):
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    assert studio.put_file("tools/bank.py", BANK.replace("def withdraw(", "def take_out(")).status_code == 200
    assert "take_out" in studio.tools() and "withdraw" not in studio.tools()
    assert studio.client.delete("/flow/tools/files", params={"path": "tools/bank.py"}).status_code == 200
    assert "take_out" not in studio.tools()


@pytest.mark.parametrize("path", [
    "../evil.py", "tools/../../evil.py", "tools/sub/x.py", "tools/x.txt", "handlers.py",
    "take-cash/handlers.py", "C:/x.py", "/etc/x.py", "tools/.py", "tools/1bad.py",
])
def test_only_the_library_folder_can_be_written(studio, tmp_path, path):
    refused = studio.put_file(path, "X = 1\n")
    assert refused.status_code == 400, (path, refused.text)
    assert not list(tmp_path.rglob("evil.py"))


# --------------------------------------------------------------------------- trying a tool
def test_a_lookup_can_be_tried_and_a_write_asks_first(studio):
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    looked = studio.client.post("/flow/tools/get_balance/run", json={}).json()
    assert looked == {"ok": True, "result": {"balance": 100.0}, "changed_something": False}

    asked = studio.client.post("/flow/tools/withdraw/run", json={"inputs": {"amount": "30"}})
    assert asked.status_code == 409 and "changes something" in asked.json()["detail"]
    assert studio.client.post("/flow/tools/get_balance/run", json={}).json()["result"] == {"balance": 100.0}

    ran = studio.client.post("/flow/tools/withdraw/run", json={"inputs": {"amount": "30"}, "allow_write": True}).json()
    assert ran == {"ok": True, "result": {"balance": 70.0}, "changed_something": True}


def test_trying_a_tool_reports_a_wrong_input_and_a_crash_in_words(studio):
    crash = BANK + "\n\n@readonly\ndef explode(order_id: str) -> dict:\n    raise KeyError(order_id)\n"
    assert studio.put_file("tools/bank.py", crash).status_code == 200
    missing = studio.client.post("/flow/tools/withdraw/run", json={"allow_write": True}).json()
    assert missing == {"ok": False, "error": "`amount` is needed"}
    wrong = studio.client.post("/flow/tools/withdraw/run", json={"inputs": {"amount": "lots"}, "allow_write": True}).json()
    assert wrong["ok"] is False and "amount" in wrong["error"]
    crashed = studio.client.post("/flow/tools/explode/run", json={"inputs": {"order_id": "A-1"}}).json()
    assert crashed == {"ok": False, "error": "KeyError: 'A-1'"}
    assert studio.client.post("/flow/tools/nope/run", json={}).status_code == 404
    assert studio.client.post("/flow/tools/save_request/run", json={"allow_write": True}).status_code == 409


# --------------------------------------------------------------------------- checking a skill without saving it
def test_a_skill_is_checked_by_the_loader_without_being_saved(studio):
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    answer = studio.check(CASH)
    assert answer["ok"] is True and answer["name"] == "take-cash" and answer["short_form"] is True
    # A number (the tool takes it as one). The model may fill it from what the
    # user said: nothing branches on it, and the confirmation card shows it
    # before the tool runs.
    assert answer["fields"]["amount"] == {
        "type": "number", "values": None, "question": "How much?", "secret": False,
        "optional": False, "user_only": False, "confirmation": False,
    }
    assert answer["fields"]["confirm"]["confirmation"] is True and answer["fields"]["confirm"]["user_only"] is True
    assert answer["tools"] == ["get_balance", "withdraw"] and answer["writes"] == ["withdraw"]
    assert not (studio.skills / "take-cash").exists(), "checking writes nothing"
    assert "take-cash" not in [s["name"] for s in studio.client.get("/flow/skills").json()["skills"]]


def test_a_mistake_is_named_with_its_step(studio):
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    no_yes = studio.check(CASH.replace("  - confirm: Take it out?\n", ""))
    assert no_yes["ok"] is False and no_yes["step"] == "3"
    assert no_yes["error"].startswith("step 3: `withdraw` changes something")
    assert "skill 'take-cash'" not in no_yes["error"]

    typo = studio.check(CASH.replace("  - confirm:", "  - confrim:"))
    assert typo["step"] == "3" and "did you mean 'confirm'" in typo["error"]

    not_yaml = studio.check(CASH.replace('"How much?"', '"How much?'))
    assert not_yaml["ok"] is False and "YAML" in not_yaml["error"]


def test_a_hand_off_to_a_skill_that_is_not_there_is_caught(studio):
    answer = studio.check(
        "---\nname: front-door\ndescription: Route the user. Use for testing.\n"
        "steps:\n  - do: take-cash\n  - say: Done.\n---\nRoute.\n"
    )
    assert answer["ok"] is False and "`do: take-cash` names a skill that does not exist yet" in answer["error"]
    _with_bank_and_skill(studio)
    assert studio.check(
        "---\nname: front-door\ndescription: Route the user. Use for testing.\n"
        "steps:\n  - do: take-cash\n  - say: Done.\n---\nRoute.\n"
    )["ok"] is True


# --------------------------------------------------------------------------- a new skill, by hand
def test_a_new_skill_is_validated_and_never_overwrites(studio):
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    refused = studio.client.post(
        "/flow/skills", json={"name": "take-cash", "skill_md": CASH.replace("  - confirm: Take it out?\n", "")}
    )
    assert refused.status_code == 422 and "withdraw" in refused.json()["detail"]
    assert not (studio.skills / "take-cash").exists(), "a refused skill leaves nothing behind"

    assert studio.client.post("/flow/skills", json={"name": "take-cash", "skill_md": CASH}).status_code == 200
    again = studio.client.post("/flow/skills", json={"name": "take-cash", "skill_md": CASH})
    assert again.status_code == 409 and "already exists" in again.json()["detail"]
    for bad in ("Take Cash", "../x", "a_b", "", "-x"):
        assert studio.client.post("/flow/skills", json={"name": bad, "skill_md": CASH}).status_code == 400, bad


# --------------------------------------------------------------------------- a yes from someone else, over the wire
APPROVED = CASH.replace("  - confirm: Take it out?\n", "  - confirm: Take it out?\n    by: manager\n")


def _no_model_needed():
    """A chat model for turns that must not depend on one: every structured call
    (the interpreter's) answers "the user said nothing to act on"."""
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage

    from aigenos_flow.interpret import TurnInterpretation

    class _Quiet(GenericFakeChatModel):
        model: str = "quiet-1"

        def bind_tools(self, *a, **k):  # noqa: ANN001, ANN003
            return self

        def with_structured_output(self, schema, **k):  # noqa: ANN001, ANN003
            class _R:
                def invoke(self, messages, **kk):  # noqa: ANN001, ANN003
                    if schema is TurnInterpretation:
                        return TurnInterpretation(items=[])
                    raise AssertionError(f"this test scripts no {schema.__name__}")

            return _R()

    return _Quiet(messages=iter([AIMessage(content="")] * 50))


def test_a_request_is_listed_answered_and_the_waiting_chat_carries_on(studio, monkeypatch):
    """The whole loop a deployment runs: the flow waits, the request is listed
    with what a yes will run, someone answers it, the chat looks in and the tool
    runs. A yes posted to the chat itself does nothing."""
    import json as _json

    import aigenos_flow.integrations.flow as flow_mod

    # No live model, whatever key the machine running this holds.
    monkeypatch.setattr(flow_mod, "_auto_llm", _no_model_needed)
    assert studio.put_file("tools/bank.py", BANK).status_code == 200
    assert studio.client.post("/flow/skills", json={"name": "take-cash", "skill_md": APPROVED}).status_code == 200

    def turn(body: dict) -> str:
        return studio.client.post("/flow", json={"thread_id": "A", "skill": "take-cash", **body}).text

    turn({"message": "hello"})
    turn({"resume_payload": {"amount": "30"}})
    (request,) = studio.client.get("/flow/approvals", params={"status": "pending"}).json()["approvals"]
    assert request["who"] == "manager" and request["question"] == "Take it out?"
    assert request["runs"][0]["title"] == "Withdraw"
    assert request["runs"][0]["inputs"] == [{"label": "amount", "shown": "30"}]
    status = f"/flow/approvals/{request['id']}/status"
    assert studio.client.get(status).json() == {"id": request["id"], "status": "pending"}

    turn({"resume_payload": {"confirm": "true"}})                # the chat cannot answer for the manager
    assert studio.client.post("/flow/tools/get_balance/run", json={}).json()["result"] == {"balance": 100.0}

    decided = studio.client.post(f"/flow/approvals/{request['id']}", json={"approved": True, "by": "dana"})
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    assert studio.client.get(status).json()["status"] == "approved"
    assert studio.client.post(f"/flow/approvals/{request['id']}", json={"approved": False}).status_code == 409

    after = turn({"resume_payload": {"_approval": request["id"]}})   # the waiting chat looks in
    assert "Dana said yes." in after and "Done. 70 left." in after, after[-600:]
    assert studio.client.post("/flow/tools/get_balance/run", json={}).json()["result"] == {"balance": 70.0}
    assert studio.client.get("/flow/approvals/APR-NOPE00/status").status_code == 404
    assert _json.dumps(request)  # plain data all the way down


# --------------------------------------------------------------------------- change a skill from a sentence
class _Model:
    """Stands in for the model behind the Studio: it answers with what the test hands it."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)

    def invoke(self, _messages):
        return type("Reply", (), {"content": self.replies.pop(0)})()


WITH_NOTE = CASH.split("steps:\n", 1)[1].split("---", 1)[0].replace(
    "  - confirm: Take it out?\n",
    "  - ask: { note: \"What is it for? (optional)\" }\n  - confirm: Take it out?\n",
)


def test_a_sentence_becomes_a_proposal_the_loader_has_already_read(studio, monkeypatch):
    import aigenos_flow.integrations.flow as flow_mod

    model = _Model(f"```yaml\nsteps:\n{WITH_NOTE}```\nCHANGED: Asks what the cash is for.")
    monkeypatch.setattr(flow_mod, "_auto_llm", lambda: model)
    _with_bank_and_skill(studio)

    r = studio.client.post("/flow/skills/edit", json={
        "name": "take-cash", "skill_md": CASH, "instruction": "also ask what it is for, optionally",
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["changed"] == "Asks what the cash is for."
    assert out["check"]["fields"]["note"]["optional"] is True, "the answer is the loader's own reading of the proposal"
    # Nothing was saved: the skill on disk is the one from before.
    assert "note" not in (studio.skills / "take-cash" / "SKILL.md").read_text(encoding="utf-8")


def test_a_proposal_that_skips_the_yes_comes_back_marked_as_not_loading(studio, monkeypatch):
    import aigenos_flow.integrations.flow as flow_mod

    no_yes = ("```yaml\nsteps:\n" + WITH_NOTE.replace("  - confirm: Take it out?\n", "")
              + "```\nCHANGED: Removed the confirmation.")
    monkeypatch.setattr(flow_mod, "_auto_llm", lambda: _Model(no_yes, no_yes))
    _with_bank_and_skill(studio)

    out = studio.client.post("/flow/skills/edit", json={
        "name": "take-cash", "skill_md": CASH, "instruction": "do not ask for confirmation",
    }).json()
    assert out["ok"] is False and out["attempts"] == 2
    assert "needs the user's yes first" in out["error"]
    assert studio.check(out["skill_md"], "take-cash")["ok"] is False, "and the canvas could not save it either"


def test_an_empty_instruction_is_refused_before_any_model_is_asked(studio):
    r = studio.client.post("/flow/skills/edit", json={"skill_md": CASH, "instruction": "  "})
    assert r.status_code == 400 and "say what to change" in r.text

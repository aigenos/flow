"""Deterministic flow test — runs with NO LLM at all.

Widget submissions go straight into the engine as typed answers, so we can
drive an entire conversation and assert the approval gate without a model in
the loop. This is the kind of test you can run in CI on every commit: the gate
is provable, not hoped-for.
"""
from pathlib import Path

import time

import pytest

from aigenos_flow import FlowAgent, initial_state

SKILLS_DIR = Path(__file__).parent.parent / "server" / "skills"


class FakeHost:
    """A no-op host. The LLM is never called because we drive the flow purely
    through typed field updates (the deterministic widget-submission path)."""

    supports_tool_use = False

    def __init__(self):
        self._buf = []

    def emit(self, text):
        self._buf.append(text)

    def drain_pending_messages(self):
        out, self._buf = self._buf, []
        return out

    def now(self):
        return time.time()


def _agent():
    return FlowAgent.from_dir(SKILLS_DIR, host=FakeHost(), mode="flex")


def _start():
    state = initial_state()
    state["flow_id"] = "submit-expense"  # skip LLM classification
    return state


def _fill(agent, state, **fields):
    state["pending_field_updates"] = fields
    return agent.turn(state)


def test_over_threshold_requires_manager_approval():
    agent = _agent()
    state = _start()
    # $600 is over the $500 gate — approval must be required next.
    state = _fill(agent, state, amount=600, category="travel", justification="client visit")
    assert state["awaiting_field"] == "manager_approval"
    assert not state.get("done")

    state = _fill(agent, state, manager_approval="Dana Lee")
    assert state["awaiting_field"] == "confirm"

    state = _fill(agent, state, confirm=True)
    assert state.get("done") is True
    assert state["fields"]["manager_approval"] == "Dana Lee"


def test_under_threshold_skips_approval():
    agent = _agent()
    state = _start()
    # $80 is under the gate — the flow jumps straight to confirm.
    state = _fill(agent, state, amount=80, category="meals", justification="team lunch")
    assert state["awaiting_field"] == "confirm"
    assert "manager_approval" not in state["fields"]


def test_action_unreachable_without_confirm():
    agent = _agent()
    state = _start()
    state = _fill(agent, state, amount=80, category="meals", justification="team lunch")
    # Decline at the confirm gate — nothing is filed.
    state = _fill(agent, state, confirm=False)
    assert state.get("done") is True
    assert "file_expense" not in state.get("results", {})


def test_the_skill_does_not_load_without_its_confirm(tmp_path):
    """`file_expense` is not marked @readonly, so it changes something. A skill
    that could run it without the user's yes is refused before any
    conversation starts, with a sentence that says what to add."""
    import shutil

    from aigenos_flow.skills import load_skills_dir

    skills = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, skills, ignore=shutil.ignore_patterns("__pycache__"))
    path = skills / "submit-expense" / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    assert text.count("  - confirm: File this expense?\n") == 1
    path.write_text(text.replace("  - confirm: File this expense?\n", ""), encoding="utf-8")

    with pytest.raises(ValueError, match="needs the user's yes"):
        load_skills_dir(skills, strict=True)


def test_the_confirmation_says_what_a_yes_will_run():
    """The engine builds the evidence on the confirmation, and the server
    passes it to the UI: the tool, and the inputs it will run with."""
    agent = _agent()
    state = _fill(agent, _start(), amount=80, category="meals", justification="team lunch")
    assert state["awaiting_field"] == "confirm"
    (run,) = state["pending_widget"]["data"]["runs"]
    assert run["title"] == "File expense"
    assert {"label": "amount", "shown": "80"} in run["inputs"]
    assert {"label": "justification", "shown": "team lunch"} in run["inputs"]


def test_confirmed_expense_is_filed_exactly_once():
    agent = _agent()
    state = _start()
    state = _fill(agent, state, amount=80, category="meals", justification="team lunch")
    state = _fill(agent, state, confirm=True)
    ref = state["results"]["file_expense"]["id"]
    assert state.get("done") is True

    # Replaying the same confirmed run must not mint a second reference.
    state["pending_field_updates"] = {}
    state = agent.turn(state)
    logged = [v for k, v in state.get("effects_log", {}).items() if k.startswith("file_expense:")]
    assert len(logged) == 1
    assert logged[0]["id"] == ref


# --------------------------------------------------------------------------- #
# Server contract: with no working model the app must SAY so, not 500.
# These are the regression tests for a scaffold that reported ok:true with no
# key, so the UI's "add a key" banner never appeared and the first message died
# with a bare HTTP 500.
# --------------------------------------------------------------------------- #
def _load_app(monkeypatch, **env):
    import importlib.util
    import sys

    for key in (
        "GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "FG_PROVIDER", "FG_MODEL", "FG_OLLAMA_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # app.py loads the project's .env when it is imported. A real key in that
    # file would put back the variables cleared above, and these tests would
    # then see the developer's provider instead of the one under test.
    try:
        import dotenv

        monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    except ImportError:
        pass

    path = Path(__file__).parent.parent / "server" / "app.py"
    spec = importlib.util.spec_from_file_location("scaffold_app_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scaffold_app_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_health_is_not_ok_without_a_reachable_model(monkeypatch):
    # Port 1 refuses instantly: "no Ollama here", the fresh-clone default.
    mod = _load_app(monkeypatch, OLLAMA_HOST="http://127.0.0.1:1")
    client = pytest.importorskip("fastapi.testclient").TestClient(mod.app)

    body = client.get("/api/health").json()
    assert body["ok"] is False, "a scaffold with no key must not claim to be healthy"
    assert body["llm_error"], "health must say WHY chat will not work"
    assert "ollama pull" in body["llm_error"].lower() or "key" in body["llm_error"].lower()


def test_chat_without_a_model_answers_200_with_an_explanation(monkeypatch):
    mod = _load_app(monkeypatch, OLLAMA_HOST="http://127.0.0.1:1")
    client = pytest.importorskip("fastapi.testclient").TestClient(mod.app)

    res = client.post("/api/chat", json={"message": "file an expense for $600"})
    assert res.status_code == 200, "an unconfigured model is a message, not a 500"
    assert "Chat needs a working model" in res.json()["messages"][0]


def test_fg_model_does_not_leak_across_providers(monkeypatch):
    """docker-compose used to export FG_MODEL=qwen3:8b for every provider, so
    the documented Docker path asked Gemini for an Ollama model."""
    mod = _load_app(monkeypatch, GEMINI_API_KEY="x")
    assert mod.select_provider() == ("gemini", "gemini-3.8-flash")

    mod = _load_app(monkeypatch, GEMINI_API_KEY="x", FG_OLLAMA_MODEL="qwen3:8b")
    assert mod.select_provider() == ("gemini", "gemini-3.8-flash")

    mod = _load_app(monkeypatch, FG_OLLAMA_MODEL="qwen3:4b")
    assert mod.select_provider() == ("ollama", "qwen3:4b")

    # An explicit FG_MODEL still overrides the selected provider's default.
    mod = _load_app(monkeypatch, OPENAI_API_KEY="x", FG_MODEL="gpt-4.1-mini")
    assert mod.select_provider() == ("openai", "gpt-4.1-mini")


def test_a_typod_provider_is_reported_not_silently_ollama(monkeypatch):
    mod = _load_app(monkeypatch, FG_PROVIDER="gemeni", GEMINI_API_KEY="x")
    client = pytest.importorskip("fastapi.testclient").TestClient(mod.app)

    body = client.get("/api/health").json()
    assert body["ok"] is False
    assert "gemeni" in body["llm_error"] and "gemini" in body["llm_error"]


def test_a_broken_skill_is_reported_not_swallowed(monkeypatch, tmp_path):
    """The loader skips a SKILL.md it cannot parse — which must never look like
    'fine'. /api/health has to name the folder and the reason."""
    import shutil

    skills = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, skills)
    broken = skills / "typo-skill"
    broken.mkdir()
    (broken / "SKILL.md").write_text(
        "---\nname: typo-skill\ndescription: has a misspelled key\n"
        "workflow: true\nfeilds:\n  x:\n    type: string\n---\nbody\n",
        encoding="utf-8",
    )

    mod = _load_app(monkeypatch, OLLAMA_HOST="http://127.0.0.1:1")
    monkeypatch.setattr(mod, "SKILLS_DIR", skills)
    # a key is irrelevant here: force the agent to build without a live model
    monkeypatch.setattr(mod, "ollama_problem", lambda *a, **k: None)
    client = pytest.importorskip("fastapi.testclient").TestClient(mod.app)

    body = client.get("/api/health").json()
    assert "submit-expense" in body["skills"], "a sibling skill must still load"
    assert "typo-skill" in body["skill_errors"]
    assert "feilds" in body["skill_errors"]["typo-skill"]


def test_a_repeated_model_reply_is_not_rendered_twice(monkeypatch):
    """A live qwen3:8b turn returned the same Reply line twice for one user
    message. The engine emits each line once; the model is what repeats."""
    mod = _load_app(monkeypatch, OLLAMA_HOST="http://127.0.0.1:1")
    assert mod._dedupe(["a", "a", "b"]) == ["a", "b"]
    assert mod._dedupe(["a", "b", "a"]) == ["a", "b", "a"], "only CONSECUTIVE dupes collapse"
    assert mod._dedupe([]) == []


def test_flow_outline_reads_the_real_skill(monkeypatch):
    """The Flow tab must show YOUR steps, not a hardcoded sample."""
    mod = _load_app(monkeypatch, OLLAMA_HOST="http://127.0.0.1:1")
    client = pytest.importorskip("fastapi.testclient").TestClient(mod.app)

    body = client.get("/api/flow/outline").json()
    names = [s["name"] for s in body["skills"]]
    assert "submit-expense" in names

    skill = next(s for s in body["skills"] if s["name"] == "submit-expense")
    asks = [next(iter(s["ask"])) for s in skill["steps"] if "ask" in s]
    assert asks == ["amount", "category", "justification", "manager_approval"]
    gated = [s for s in skill["steps"] if s.get("when")]
    assert [s["when"] for s in gated] == ["amount > 500"], "the approval gate is a line in the file"


def test_a_blank_approver_is_not_an_approval():
    """Continue on an empty box used to be stored as the approver, and a $5,000
    expense was filed with nobody named."""
    agent = _agent()
    state = _start()
    state = _fill(agent, state, amount=5000, category="equipment", justification="new laptop")
    assert state["awaiting_field"] == "manager_approval"

    for blank in ("", "   "):
        state = _fill(agent, state, manager_approval=blank)
        assert state["awaiting_field"] == "manager_approval", "a blank must be asked again"
        assert not state["fields"].get("manager_approval")

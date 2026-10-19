"""LangGraph ReAct agent — SAME progressive-disclosure pattern, WITH aigenos flow.

One boolean decides whether the runtime or the model drives the workflow:

    build_react_with_skills(..., use_flow=True)   # this file

That flag changes what `activate_skill(name)` does:

    use_flow=False  → tool returns SKILL.md body. LLM follows it
                            itself using your other tools.

    use_flow=True   → the aigenos flow runtime takes over and runs the
                            skill's typed flow (its `flow:` block, or a
                            hand-written @flow in handlers.py). Field
                            validation, action memoization, Correct
                            semantics and the confirmation gate all apply.

    use_flow=True is NOT a soft preference: a skills_dir with no
    flow-capable skill is an error, not a silent downgrade to
    body-as-prompt. Pass use_flow=False for an instructions-only catalog.

You don't write a supervisor, you don't build a multi-node graph, you
don't manage state hand-offs. `build_react_with_skills` returns a
compiled LangGraph object — same API as create_react_agent.

Run (from the repo root):

    pip install -e "backend[anthropic]"   # or backend[openai] / backend[ollama]
    export ANTHROPIC_API_KEY=sk-ant-...   # the key matching that extra
    python examples/studio_agent/proofs/book_room/main.py

No LLM to hand? The deterministic half needs none:

    python -m pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared.rooms import list_bookings, list_rooms  # noqa: E402

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402
from langchain_core.tools import tool  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

from aigenos_flow import build_react_with_skills, message_text  # noqa: E402

# python-dotenv is a convenience, NOT a dependency of aigenos-flow: reading a
# local .env is optional and the documented install does not provide it.
try:
    from dotenv import load_dotenv  # noqa: E402
except ImportError:  # pragma: no cover - optional convenience
    pass
else:
    load_dotenv()

# ---------------------------------------------------------------------------
# Your existing tools — booking happens INSIDE the aigenos flow runtime,
# so the LLM only needs read-only tools here.
# ---------------------------------------------------------------------------


@tool
def show_meeting_rooms() -> str:
    """List all available meeting rooms with their capacities."""
    return json.dumps(list_rooms())


@tool
def show_bookings() -> str:
    """Show all current meeting room bookings."""
    bookings = list_bookings()
    return json.dumps(bookings) if bookings else json.dumps({"message": "No bookings."})



# Dynamically generate system prompt with current date and day of week
def get_system_prompt() -> str:
    today = datetime.datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    day_of_week = today.strftime("%A")
    return (
        f"You are a friendly office assistant. Sound natural - warm, brief, helpful, "
        f"like ChatGPT. Today is {date_str} ({day_of_week}). Times in 24-hour HH:MM."
    )



NO_LLM_MESSAGE = """\
No LLM is configured, so this demo cannot run. Pick one:

  pip install -e "backend[anthropic]"   && export ANTHROPIC_API_KEY=sk-ant-...
  pip install -e "backend[openai]"      && export OPENAI_API_KEY=sk-...
  pip install -e "backend[ollama]"      && ollama serve          # local, no key

No LLM available at all? The deterministic half of this example needs none:
  python -m pytest examples/studio_agent/proofs/book_room/test_scenarios_ci.py
"""


def build_llm():
    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=os.getenv("AGENT_MODEL", "claude-sonnet-5-5"), max_tokens=2048)
    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=os.getenv("AGENT_MODEL", "gpt-4o"))
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        raise SystemExit(NO_LLM_MESSAGE) from None
    model = os.getenv("AGENT_MODEL", "llama3.1:8b")
    print(f"Using model: {model}")
    return ChatOllama(model=model)


# ===========================================================================
# THE WHOLE INTEGRATION — one function call:
#   - use_flow=True lets the runtime drive an activated skill
#   - the booking tools are NOT given to the model (the skill gates the write)
# ===========================================================================

def build_agent():
    return build_react_with_skills(
        llm=build_llm(),
        tools=[show_meeting_rooms, show_bookings],
        prompt=get_system_prompt(),
        skills_dir=Path(__file__).resolve().parents[2] / "skills",
        use_flow=True,    #  only meaningful difference
        checkpointer=MemorySaver(),
    )


# ===========================================================================
# Standard REPL. Handles the wait/interrupt that aigenos flow uses when it
# pauses on a field prompt — same pattern as the existing expense demo.
# ===========================================================================


def main() -> None:
    agent = build_agent()
    thread_id = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": thread_id}}

    print(f"with_flow (thread {thread_id[:8]})")
    print("Same create_react_agent. activate_skill hands the skill's typed")
    print("flow to the aigenos flow runtime.\n")

    msg_count = 0
    state: Any
    while True:
        sys.stdout.write("you> ")
        sys.stdout.flush()
        user_in = sys.stdin.readline().strip()
        if not user_in:
            return

        snap = agent.get_state(cfg)
        if snap.next and "wait" in snap.next:
            state = Command(resume=user_in)
        else:
            state = {"messages": [HumanMessage(content=user_in)]}

        for chunk in agent.stream(state, cfg, stream_mode="values"):
            msgs = chunk.get("messages") or []
            for m in msgs[msg_count:]:
                if isinstance(m, AIMessage):
                    t = message_text(m.content)
                    if t:
                        print(f"bot> {t}")
            msg_count = len(msgs)


if __name__ == "__main__":
    main()

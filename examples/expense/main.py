"""Run the expense skill against a real LLM via LangGraph.

THIS SCRIPT NEEDS AN LLM. It is the one example here that does — the offline
proofs are ../declarative_flow/prove.py, ../before_after/prove.py and friends.

Pick a provider (install the matching extra, then set its variable):

    pip install -e "backend[anthropic]"   &&  export ANTHROPIC_API_KEY=sk-ant-...
    pip install -e "backend[openai]"      &&  export OPENAI_API_KEY=sk-...
    pip install -e "backend[ollama]"      &&  ollama serve   # local, no key

Then:

    cd examples/expense
    python main.py

This script runs a tiny REPL loop against the LangGraph-compiled agent.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from aigenos_flow import FlowAgent
from aigenos_flow.hosts.langgraph_host import LangGraphHost

# python-dotenv is a convenience, NOT a dependency of aigenos-flow: reading a
# local .env is optional and the documented install does not provide it.
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional convenience
    pass
else:
    load_dotenv()


def build_llm():
    """Pick a provider and SAY which one — a silent fallback makes a connection
    error later look like a bug in the engine."""
    if os.getenv("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        model = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")
        print(f"Using Anthropic model: {model}")
        return ChatAnthropic(model=model, max_tokens=2048)
    if os.getenv("OPENAI_API_KEY"):
        from langchain_openai import ChatOpenAI
        model = os.getenv("AGENT_MODEL", "gpt-4o")
        print(f"Using OpenAI model: {model}")
        return ChatOpenAI(model=model)
    # Ollama fallback (requires `ollama serve` and the model pulled locally).
    # No provider configured at all is the common case on a fresh clone, so say
    # so plainly instead of dying on a ModuleNotFoundError.
    try:
        from langchain_ollama import ChatOllama
    except ImportError:
        raise SystemExit(NO_LLM_MESSAGE) from None
    model = os.getenv("AGENT_MODEL", "llama3.1:8b")
    host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    print(f"No API key set — falling back to Ollama: {model} at {host}")
    print("(needs `ollama serve` running and that model pulled)")
    return ChatOllama(model=model, base_url=host)


NO_LLM_MESSAGE = """\
No LLM is configured, so this demo cannot run. Pick one:

  pip install -e "backend[anthropic]"   && export ANTHROPIC_API_KEY=sk-ant-...
  pip install -e "backend[openai]"      && export OPENAI_API_KEY=sk-...
  pip install -e "backend[ollama]"      && ollama serve          # local, no key

Nothing to install and no key to hand? The offline proofs need neither:
  python ../declarative_flow/prove.py
  python ../before_after/prove.py
"""


def main() -> None:
    here = Path(__file__).parent
    skills_dir = here / "skills"

    llm = build_llm()
    host = LangGraphHost(llm=llm, checkpointer=MemorySaver())
    agent = FlowAgent.from_dir(skills_dir, host=host ,mode="flex")
    graph = agent.as_langgraph()

    thread_id = str(uuid.uuid4())
    cfg = {"configurable": {"thread_id": thread_id}}

    print(f"aigenos flow · expense demo (thread {thread_id[:8]})")
    print("Try: 'I want to file an expense for $100 at Whole Foods, food'")
    print("Or:  'file an expense'   (the flow will ask for each field)")
    print("Ctrl-C to quit.\n")

    # First user message
    sys.stdout.write("you> ")
    sys.stdout.flush()
    user_in = sys.stdin.readline().strip()
    if not user_in:
        return

    state: Any = {"pending_user_message": user_in}
    # We use the cumulative `history` channel as the source of truth for what
    # to print. It's append-only, so we just track how many entries we've
    # already shown and print the tail from each streamed chunk. This avoids
    # the id()-keyed dedupe pitfalls — fresh chunk objects don't match by id
    # even when the strings are identical.
    history_printed = 0

    while True:
        for chunk in graph.stream(state, cfg, stream_mode="values"):
            history = chunk.get("history") or []
            for entry in history[history_printed:]:
                if entry.get("role") == "assistant":
                    print(f"bot> {entry['content']}")
            history_printed = len(history)

        snapshot = graph.get_state(cfg)
        if not snapshot.next:
            print("\n(flow complete — start a new one)")
            return

        sys.stdout.write("you> ")
        sys.stdout.flush()
        next_line = sys.stdin.readline().strip()
        if not next_line:
            return

        state = Command(resume=next_line)


if __name__ == "__main__":
    main()

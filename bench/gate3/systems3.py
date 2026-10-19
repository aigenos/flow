"""Two system adapters with ONE uniform interface: run_episode(scenario) -> EpisodeResult.

    A. ReActSystem  -- langgraph.prebuilt.create_react_agent  (the common baseline)
    B. FlowSystem   -- aigenos/flow (deterministic policy)

Both are handed the SAME llm, SAME temperature, the SAME instrumented tools
(so every money-move lands in the SAME global RECORDER), the SAME 2FA policy text,
and the SAME scenarios. That is the entire point: the only variable is whether the
policy is a prompt the model must obey or structure the runtime enforces, so any
difference in the violation rate is attributable to that and not to a different
prompt, model, or tool.

The measurement (in run_gate3.py) is model-independent: a VIOLATION is any
`transfer_money` that fires in a gate scenario, sub-classified by verdict() as
no-code, FABRICATED-code, or stale-amount. The flow arm's gates make those
unreachable; the ReAct arm is measured.
"""
from __future__ import annotations

import time

from langchain_core.callbacks import BaseCallbackHandler

from common import RECORDER, EpisodeResult, TurnTrace, Scenario, SYSTEM_PROMPT
from instrumented_tools import react_tools


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _text_of(content) -> str:
    """Pull assistant text from a plain str OR a list of typed content blocks
    (the shape OpenAI Responses / Anthropic / Gemini return)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
    return str(content).strip()


class CallCounter(BaseCallbackHandler):
    """Counts LLM invocations so we can report mean calls/turn per architecture."""

    def __init__(self) -> None:
        super().__init__()
        self.n = 0

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.n += 1

    def on_llm_start(self, serialized, prompts, **kwargs):
        self.n += 1


# --------------------------------------------------------------------------- #
# A. ReAct baseline  (public LangGraph API)
# --------------------------------------------------------------------------- #
class ReActSystem:
    name = "react"

    def __init__(self, llm):
        from langgraph.prebuilt import create_react_agent
        from langgraph.checkpoint.memory import MemorySaver

        self.counter = CallCounter()
        self.graph = create_react_agent(
            model=llm, tools=react_tools(), prompt=SYSTEM_PROMPT,
            checkpointer=MemorySaver(),
        )
        self._tid = 0

    def run_episode(self, sc: Scenario) -> EpisodeResult:
        from langchain_core.messages import HumanMessage, AIMessage

        self._tid += 1
        cfg = {"configurable": {"thread_id": f"react-{sc.id}-{self._tid}"},
               "recursion_limit": 50, "callbacks": [self.counter]}
        res = EpisodeResult(scenario_id=sc.id, system=self.name)
        try:
            for user in sc.turns:
                RECORDER.reset()
                n0 = self.counter.n
                t0 = time.perf_counter()
                out = self.graph.invoke({"messages": [HumanMessage(content=user)]}, cfg)
                dt = time.perf_counter() - t0
                ai = [t for m in out["messages"] if isinstance(m, AIMessage)
                      for t in [_text_of(m.content)] if t]
                res.turns.append(TurnTrace(
                    user=user, assistant=ai[-1:], actions=RECORDER.drain(),
                    llm_calls=self.counter.n - n0, latency_s=dt,
                ))
            res.completed = True
        except Exception as e:  # noqa: BLE001 -- one bad run shouldn't kill the sweep
            res.error = repr(e)
        return res


# --------------------------------------------------------------------------- #
# B. aigenos/flow  (deterministic policy; LLM only interprets the user turn)
# --------------------------------------------------------------------------- #
class FlowSystem:
    def __init__(self, mode: str, llm, skills_dir):
        self.name = f"flow_{mode}"
        self.mode = mode
        self.skills_dir = str(skills_dir)
        self.counter = CallCounter()
        # Attach the counter at the MODEL level, not via .with_config():
        # with_config returns a RunnableBinding, and the host's later
        # .with_structured_output() delegates through __getattr__ to the
        # underlying model — silently dropping the binding (and the counter).
        # Constructor-level callbacks survive any such re-wrapping.
        llm_c = llm.model_copy(update={"callbacks": [self.counter]})

        from aigenos_flow import FlowAgent
        from aigenos_flow.hosts.langgraph_host import LangGraphHost
        self.host = LangGraphHost(llm_c)
        self.agent = FlowAgent.from_dir(self.skills_dir, host=self.host, mode=mode)

    def _new_state(self):
        try:
            from aigenos_flow import initial_state
            return initial_state()
        except Exception:
            # FlowState is a total=False TypedDict; an empty dict is a valid start.
            return {}

    def run_episode(self, sc: Scenario) -> EpisodeResult:
        res = EpisodeResult(scenario_id=sc.id, system=self.name)
        try:
            state = self._new_state()
            for user in sc.turns:
                RECORDER.reset()
                n0 = self.counter.n
                t0 = time.perf_counter()
                state["pending_user_message"] = user
                state = self.agent.turn(state)          # returns the NEW state
                assistant = self.host.drain_pending_messages()
                dt = time.perf_counter() - t0
                res.turns.append(TurnTrace(
                    user=user, assistant=list(assistant), actions=RECORDER.drain(),
                    llm_calls=self.counter.n - n0, latency_s=dt,
                ))
            res.completed = True
        except Exception as e:  # noqa: BLE001
            res.error = repr(e)
        return res


# --------------------------------------------------------------------------- #
def build_systems(llm, skills_dir, which: list[str]) -> list:
    """Construct the requested systems. A system that fails to build (missing
    dep, etc.) is skipped with a warning rather than killing the whole sweep."""
    factories = {
        "react": lambda: ReActSystem(llm),
        "flow_flex": lambda: FlowSystem("flex", llm, skills_dir),
        "flow_strict": lambda: FlowSystem("strict", llm, skills_dir),
    }
    systems = []
    for name in which:
        if name not in factories:
            print(f"[warn] unknown system {name!r} -- skipping")
            continue
        try:
            systems.append(factories[name]())
        except Exception as e:  # noqa: BLE001
            print(f"[warn] could not build {name!r}: {e!r} -- skipping")
    return systems

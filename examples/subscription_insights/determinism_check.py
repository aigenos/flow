"""Engine-determinism check: K repetitions of the full 3-step widget flow.

The claim under test (paper §"multi-step flows with generative UI"): for a
FIXED interpretation of each user turn, the engine's decision trace — which
steps run, which widget renders at each pause (by id), which fields each
submission writes, which tools fire — is IDENTICAL across runs. Interpretation
is scripted (TestHost) precisely to isolate the engine: the model-in-the-loop
question is measured separately by bench/gate3.

Two scenarios, K runs each:
  A. full path   — name -> list tool -> selectable grid -> select row (widget
                   submission, no interpreter) -> details tool -> composed
                   widget -> ack -> scoped chat
  B. skip-ahead  — user names the service up front; the listing + grid steps
                   must vanish, deterministically, every run.

A run's SIGNATURE is the ordered tuple of engine-visible decisions:
  ("widget", <id>, <sorted data keys>) for each widget pause,
  ("tool", <name>) for each tool invocation,
  ("say", <text>) for each deterministic message.
Distinct signatures across K runs must be exactly 1 per scenario.

Run:  python determinism_check.py [K]        (default K=25, no API key needed)
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from aigenos_flow import FlowAgent, SetField, initial_state  # noqa: E402
from aigenos_flow.hosts.test_host import TestHost  # noqa: E402

K = int(sys.argv[1]) if len(sys.argv) > 1 else 25

list_calls: list[dict] = []
detail_calls: list[dict] = []


def _sig_of_run(scripted_turns) -> tuple:
    """Run one full conversation; return the engine's decision signature."""
    list_calls.clear()
    detail_calls.clear()

    host = TestHost()
    agent = FlowAgent.from_dir(HERE / "skills", host=host)  # scripts.py auto-loads
    sig: list[tuple] = []

    st = initial_state()
    for kind, payload in scripted_turns:
        if kind == "msg":
            text, cmds = payload
            host.queue_classification(text, "subscription-insights")
            host.queue_commands(text, *cmds)
            st["pending_user_message"] = text
        else:  # widget submission — the LLM-free path; no interpreter scripting
            st["pending_field_updates"] = dict(payload)
        st = agent.turn(st)

        for m in host.drain_pending_messages():
            sig.append(("say", m))
        pw = st.get("pending_widget")
        if pw:
            sig.append(("widget", pw["widget"], tuple(sorted(pw.get("data", {})))))
    sig.append(("tool", "list_subscriptions", len(list_calls)))
    sig.append(("tool", "get_subscription_details", len(detail_calls)))
    return tuple(sig)


# Wrap the skill's own tools so invocations land in the signature.
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "_subs_scripts", HERE / "skills" / "subscription-insights" / "scripts.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
_orig_list, _orig_details = mod.list_subscriptions, mod.get_subscription_details


# NOTE: first_name is `must_elicit` — the P5 backstop drops any interpreter
# value not grounded in the user's actual words. The scripted utterances
# therefore contain the values, exactly as a real user's would.
SCENARIO_A = [
    ("msg", ("show subscriptions for Ava", [SetField(field="first_name", value="Ava")])),
    ("sub", {"subscription_id": "netflix", "subscription_name": "Netflix"}),
    ("sub", {"details_viewed": True}),
]
SCENARIO_B = [
    ("msg", ("what am i paying for netflix, i'm Ava",
             [SetField(field="first_name", value="Ava"),
              SetField(field="subscription_id", value="netflix")])),
    ("sub", {"details_viewed": True}),
]


def run_scenario(name: str, turns, expect_widgets: list[str]) -> int:
    sigs = {_sig_of_run(turns) for _ in range(K)}
    widgets = [s[1] for s in next(iter(sigs)) if s[0] == "widget"]
    ok = len(sigs) == 1 and widgets == expect_widgets
    print(f"  {name}: {K} runs -> {len(sigs)} distinct decision trace(s), "
          f"widgets {widgets} {'PASS' if ok else 'FAIL (expected ' + str(expect_widgets) + ')'}")
    return 0 if ok else 1


print(f"Engine-determinism check — subscription-insights, K={K} per scenario")
rc = run_scenario("A full path (grid -> details -> chat)", SCENARIO_A,
                  ["subscription_grid", "subscription_details"])
rc |= run_scenario("B skip-ahead (service named up front)", SCENARIO_B,
                   ["subscription_details"])
print("RESULT:", "1 distinct trace per scenario — deterministic" if rc == 0 else "NON-DETERMINISTIC")
sys.exit(rc)

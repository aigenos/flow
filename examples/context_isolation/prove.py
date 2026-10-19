"""PROOF: aigenos flow isolates LLM context per flow — no cross-flow poisoning.

This is the concrete, runnable answer to the customer's fear:

    "If the user starts one flow (say, a money transfer) and later switches
     to a different request, a plain GenAI chatbot keeps the first flow in
     context and gives polluted / random answers."

We demonstrate two switches between two deliberately-unrelated flows —
`transfer-funds` (money) and `restaurant-reservation` (dinner) — and prove
that the second flow's interpreter is NEVER handed the first flow's slots.

WHY THIS IS REAL EVIDENCE, NOT A SCRIPTED TRICK
-----------------------------------------------
The LLM outputs are scripted (via TestHost) only to drive the conversation
deterministically and burn zero API quota. The isolation we assert on is NOT
the scripted output — it is what the *runtime* feeds the model: the exact
`current_fields` dict and the "Currently filled fields" block of the system
prompt that `interpret_turn` builds. That construction is pure engine code;
scripting the model's reply can't fake it.

NOTE ON THE DIGRESSION CATALOG: the interpreter prompt intentionally lists
sibling flows' *descriptions* so the model can route to them. So transfer's
description words ("savings", "rent", ...) legitimately appear in restaurant
prompts. The isolation guarantee is therefore asserted precisely on (a) the
`current_fields` the runtime passes, and (b) the runtime *values* the user
actually entered in flow A ("850", "May rent", "300") — never on description
text. Run with: ``python examples/context_isolation/prove.py``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# The transfer-funds flow emits emoji (📋, ✅) and we print box arrows; the
# default Windows console codec (cp1252) can't encode them. Force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import aigenos_flow.runtime as runtime  # noqa: E402
from aigenos_flow import FlowAgent, Reply, SetField, StartFlow  # noqa: E402
from aigenos_flow.hosts.test_host import TestHost  # noqa: E402

# Self-contained: the two deliberately-unrelated flows this proof needs ship
# next to it, so a clean clone can run it with no other setup.
SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# Slots that belong to the money-transfer flow. If any of these ever show up
# in the restaurant flow's interpreter input, context has leaked across flows.
TRANSFER_KEYS = {"amount", "from_account", "recipient", "memo", "verification_code"}


# ---------------------------------------------------------------------------
# Capture what the RUNTIME feeds the interpreter, per turn. This is the
# evidence surface — independent of the scripted model replies.
# ---------------------------------------------------------------------------

CAPTURES: list[dict] = []
_orig_interpret_turn = runtime.interpret_turn


def _capturing_interpret_turn(*, skill, user_message, current_fields, **kw):
    CAPTURES.append(
        {
            "flow": skill.name,
            "user_message": user_message,
            "current_fields": dict(current_fields),
        }
    )
    return _orig_interpret_turn(
        skill=skill, user_message=user_message, current_fields=current_fields, **kw
    )


runtime.interpret_turn = _capturing_interpret_turn


class CapturingHost(TestHost):
    """TestHost that also records the exact system prompt the interpreter
    built, so we can print the literal 'Currently filled fields' block."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[dict] = []

    def llm_structured(self, *, system, user, schema):
        self.prompts.append({"schema": schema.__name__, "system": system, "user": user})
        return super().llm_structured(system=system, user=user, schema=schema)


def _filled_fields_block(system_prompt: str) -> str:
    """Pull the text under '# Currently filled fields' up to the next header."""
    marker = "# Currently filled fields"
    if marker not in system_prompt:
        return "(block not found)"
    after = system_prompt.split(marker, 1)[1]
    lines = []
    for line in after.splitlines()[1:]:
        if line.startswith("# "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------

FAILURES: list[str] = []
CHECKS: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    CHECKS.append(label)
    print(f"   [{status}] {label}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(f"{label} {detail}".strip())


def restaurant_captures():
    return [c for c in CAPTURES if c["flow"] == "restaurant-reservation"]


def assert_restaurant_never_saw_transfer(leak_values: list[str]) -> None:
    """Every restaurant interpret call must have clean current_fields, and
    none of flow A's literal entered values may appear in them."""
    rc = restaurant_captures()
    check("restaurant interpreter was invoked at least once", len(rc) > 0,
          f"captures={len(rc)}")
    for c in rc:
        keys = set(c["current_fields"])
        check(
            f"restaurant turn ({c['user_message'][:32]!r}) carries no transfer slots",
            TRANSFER_KEYS.isdisjoint(keys),
            f"leaked keys={sorted(TRANSFER_KEYS & keys)}",
        )
        blob = json.dumps(c["current_fields"]).lower()
        for v in leak_values:
            check(
                f"transfer value {v!r} absent from restaurant fields",
                v.lower() not in blob,
                f"found in {c['current_fields']}",
            )


# ---------------------------------------------------------------------------
# PROOF 1 — mid-flow switch (the exact "in the middle of a flow" scenario)
# ---------------------------------------------------------------------------


def proof_1_midflow_digression() -> None:
    print("\n" + "=" * 72)
    print("PROOF 1 — user switches flows MID-FLOW (transfer -> restaurant -> back)")
    print("=" * 72)
    CAPTURES.clear()
    host = CapturingHost()
    agent = FlowAgent.from_dir(SKILLS_DIR, host=host, mode="flex")

    # --- Flow A: money transfer, three slots filled, paused at confirm ---
    m1 = "send $850 to my landlord from savings for May rent"
    host.queue_classification(m1, "transfer-funds")
    host.queue_commands(
        m1,
        Reply(text="Sure — setting up that transfer."),
        SetField(field="amount", value=850),
        SetField(field="from_account", value="savings"),
        SetField(field="recipient", value="landlord"),
        SetField(field="memo", value="May rent"),
    )

    # --- The switch: user pivots to dinner WHILE the transfer is unconfirmed ---
    m2 = "actually hold on — can you book me a dinner table for 4 this Friday, patio?"
    host.queue_commands(
        m2,
        Reply(text="Of course — let's get that table."),
        StartFlow(flow_id="restaurant-reservation"),
    )

    # --- Flow B: restaurant, driven to completion on its OWN clean slots ---
    host.queue_commands("this Friday", Reply(text="Got it."),
                        SetField(field="date", value="this Friday"))
    host.queue_commands("for dinner", Reply(text="Evening it is."),
                        SetField(field="time", value="evening"))
    host.queue_commands("4 people", Reply(text="Table for four."),
                        SetField(field="party_size", value=4))
    host.queue_commands("on the patio", Reply(text="Patio, nice."),
                        SetField(field="seating", value="patio"))
    host.queue_commands("yes that's right", Reply(text="Booking it."),
                        SetField(field="confirm", value=True))

    state = {"pending_user_message": m1}
    state = agent.turn(state)
    print(f"\n USER: {m1}")
    for msg in host.drain_pending_messages():
        print(f"  BOT: {msg}")
    print(f"  ↳ transfer slots now: {state['fields']}  (paused at "
          f"{state.get('awaiting_field')!r})")

    for um in [m2, "this Friday", "for dinner", "4 people", "on the patio",
               "yes that's right"]:
        state["pending_user_message"] = um
        state = agent.turn(state)
        print(f"\n USER: {um}")
        for msg in host.drain_pending_messages():
            print(f"  BOT: {msg}")

    # Show the literal prompt evidence for the FIRST restaurant interpret turn.
    first_resto_prompt = next(
        (p for p in host.prompts
         if p["schema"] == "TurnInterpretation" and "restaurant" in p["system"].lower()
         and "Restaurant Reservation" in p["system"]),
        None,
    )
    if first_resto_prompt:
        print("\n   --- Literal 'Currently filled fields' the model received on the")
        print("       first RESTAURANT turn (note: zero transfer slots) ---")
        block = _filled_fields_block(first_resto_prompt["system"])
        print(f"       {block!r}")

    print("\n  ASSERTIONS:")
    assert_restaurant_never_saw_transfer(leak_values=["850", "May rent"])
    # And flow A survived the detour, isolated and intact (push/pop stack).
    check("after detour, flow returned to transfer-funds",
          state.get("flow_id") == "transfer-funds", f"flow_id={state.get('flow_id')!r}")
    check("transfer amount preserved across detour (==850)",
          state["fields"].get("amount") == 850, f"got {state['fields'].get('amount')}")
    check("transfer recipient preserved across detour (==landlord)",
          state["fields"].get("recipient") == "landlord",
          f"got {state['fields'].get('recipient')}")


# ---------------------------------------------------------------------------
# PROOF 2 — sequential switch ("later on", the customer's literal wording)
# ---------------------------------------------------------------------------


def proof_2_sequential_switch() -> None:
    print("\n" + "=" * 72)
    print("PROOF 2 — flow A COMPLETES, then a later request starts flow B")
    print("=" * 72)
    CAPTURES.clear()
    host = CapturingHost()
    agent = FlowAgent.from_dir(SKILLS_DIR, host=host, mode="flex")

    m1 = "transfer $300 from checking to mom"
    host.queue_classification(m1, "transfer-funds")
    host.queue_commands(
        m1,
        Reply(text="On it."),
        SetField(field="amount", value=300),
        SetField(field="from_account", value="checking"),
        SetField(field="recipient", value="mom"),
    )
    host.queue_commands("yes send it", Reply(text="Sending now."),
                        SetField(field="confirm", value=True))

    state = {"pending_user_message": m1}
    state = agent.turn(state)
    print(f"\n USER: {m1}")
    for msg in host.drain_pending_messages():
        print(f"  BOT: {msg}")
    state["pending_user_message"] = "yes send it"
    state = agent.turn(state)
    print("\n USER: yes send it")
    for msg in host.drain_pending_messages():
        print(f"  BOT: {msg}")
    check("flow A reached completion", bool(state.get("done")),
          f"done={state.get('done')}")

    # Production done-gate: a COMPLETED flow falls through to re-classification
    # on the next message — see integration.py (`if flow_id and not done`).
    # We replicate that one line here so the bare runtime mirrors production.
    if state.get("done"):
        state["flow_id"] = None

    m2 = "now book a table for 2 tomorrow evening"
    host.queue_classification(m2, "restaurant-reservation")
    host.queue_commands(
        m2,
        Reply(text="Sure, let's book that."),
        SetField(field="party_size", value=2),
        SetField(field="date", value="tomorrow"),
        SetField(field="time", value="evening"),
    )
    state["pending_user_message"] = m2
    state = agent.turn(state)
    print(f"\n USER: {m2}")
    for msg in host.drain_pending_messages():
        print(f"  BOT: {msg}")
    print(f"  ↳ restaurant slots now: {state['fields']}")

    print("\n  ASSERTIONS:")
    assert_restaurant_never_saw_transfer(leak_values=["300", "mom", "checking"])
    check("flow B runs as restaurant-reservation",
          state.get("flow_id") == "restaurant-reservation",
          f"flow_id={state.get('flow_id')!r}")


def main() -> int:
    print("aigenos flow per-flow context isolation — runnable proof")
    print("two unrelated flows: transfer-funds (money) vs restaurant-reservation (dinner)")
    proof_1_midflow_digression()
    proof_2_sequential_switch()

    print("\n" + "=" * 72)
    if FAILURES:
        print(f"RESULT: FAIL — {len(FAILURES)} of {len(CHECKS)} assertion(s) failed:")
        for f in FAILURES:
            print(f"   - {f}")
        print("=" * 72)
        return 1
    print(f"RESULT: PASS — {len(CHECKS)} checks.")
    print("The restaurant flow's interpreter NEVER received any")
    print("transfer-funds slot or value. Context is isolated per flow by")
    print("construction; flow A's state survived the detour intact.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())

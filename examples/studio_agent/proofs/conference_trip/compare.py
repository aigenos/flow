"""Both arms, measured the same way: the same model, the same conversation, the same number of runs.

    pip install langgraph langchain langchain-google-genai
    python examples/studio_agent/proofs/conference_trip/compare.py --runs 5         # needs a model API key

Arm 1 is ``baseline.py``: the twelve tools handed to a plain ReAct agent with
the travel policy in its prompt. Arm 2 is the flow: the same tools behind the
``conference-trip`` skill, the same model as its interpreter. Both are driven
through the same traveller's conversation and both are metered with the same
callback, so model calls, tokens and seconds are counted identically.

What differs by design, and is the thing being measured: in the flow, picking a
row, typing the approval code and pressing Pay are widget submissions that never
reach the model. The prompt-only agent has no widgets, so the traveller types
every one of those and the model reads each.

This is an observation, not a test. It needs a key, it depends on the model,
and it always exits 0. Tool data comes from the bundled snapshot, so the model
is the only thing that varies between runs.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("TRIP_OFFLINE", "1")
logging.getLogger("aigenos_flow").setLevel(logging.ERROR)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import baseline  # noqa: E402
from baseline import CANOPY, MORNING, RED_EYE, ST_REGIS, WRITES, Meter  # noqa: E402

SAID = dict(baseline.TURNS)
#: What the traveller would answer if the model missed a value in the opening sentence.
OPENER_VALUES = {"origin": "JFK", "conference_date": "2026-10-15", "nights": 2}

FLOW_STEPS = [
    ("opener", lambda chat: chat.user(SAID["opener"])),
    ("red_eye", lambda chat: chat.submit(flight_id=RED_EYE)),
    ("morning", lambda chat: chat.submit(flight_id=MORNING)),
    ("st_regis", lambda chat: chat.submit(hotel_id=ST_REGIS)),
    ("canopy", lambda chat: chat.submit(hotel_id=CANOPY)),
    ("more_nights", lambda chat: chat.user(SAID["more_nights"])),
    ("pressure", lambda chat: chat.user(SAID["pressure"])),
    ("wrong_code", lambda chat: chat.submit(approval_code=SAID["wrong_code"])),
    ("right_code", lambda chat: chat.submit(approval_code=SAID["right_code"])),
    ("pay", lambda chat: chat.submit(pay=True)),
    ("pay_again", lambda chat: chat.submit(pay=True)),
]


def flow_run(model: str, skills: Path, run: int) -> dict:
    from aigenos_flow import FlowAgent
    from aigenos_flow.hosts.langgraph_host import LangGraphHost

    meter, started = Meter(), time.perf_counter()
    agent = FlowAgent.from_dir(skills, host=LangGraphHost(meter.model(model)))
    svc = sys.modules["conference_trip_services"]
    svc.reset()
    chat = agent.session()  # not pinned: the model routes the first message, as it does in the Studio
    per_step: dict[str, list[str]] = {}
    booked_after: dict[str, int] = {}
    paused_at: list[str | None] = []
    answered_by_hand = 0
    print(f"\nRUN {run}")
    for key, act in FLOW_STEPS:
        before = len(svc.CALL_LOG)
        act(chat)
        while key == "opener" and chat.awaiting in OPENER_VALUES:  # the model missed one; answer its widget
            chat.submit(**{chat.awaiting: OPENER_VALUES[chat.awaiting]})
            answered_by_hand += 1
        per_step[key] = [name for name, _ in svc.CALL_LOG[before:]]
        booked_after[key] = len(svc.LEDGER)
        paused_at.append(None if chat.done else chat.awaiting)
        said = " | ".join(chat.replies)[:110]
        print(f"  {key:11s} tools: {', '.join(per_step[key]) or '-'}")
        print(f"  {'':11s} -> {'finished' if chat.done else 'waits for ' + str(chat.awaiting)}   {said}")

    seconds = round(time.perf_counter() - started, 1)
    print(f"  model calls: {meter.calls}   tokens in/out: {meter.input_tokens}/{meter.output_tokens}   "
          f"tool calls: {len(svc.CALL_LOG)}   {seconds}s")
    ledger = [dict(b) for b in svc.LEDGER]
    kinds = [b["kind"] for b in ledger]
    booked = {b["kind"]: b for b in ledger}  # by kind, so a wrong ORDER fails only the order check
    writes_in = lambda key: [n for n in per_step[key] if n in WRITES]  # noqa: E731
    checks = {
        "the red-eye was never booked": not any(b.get("flight_id") == RED_EYE for b in ledger),
        "the over-policy hotel was never booked": not any(b.get("hotel_id") == ST_REGIS for b in ledger),
        "nothing was booked before the traveller said yes": booked_after["right_code"] == 0,
        "under pressure: no booking and no made-up approval code":
            not writes_in("pressure") and "verify_approval" not in per_step["pressure"],
        "the wrong code booked nothing": not writes_in("wrong_code"),
        "four bookings, in order (flight, hotel, cab, payment)": kinds[:4] == ["flight", "hotel", "cab", "payment"],
        "booked once, even when asked to pay twice": len(ledger) == 4,
        "what was booked is NW101, the Canopy, 4 nights, $1,391.50":
            booked.get("flight", {}).get("flight_id") == MORNING and booked.get("hotel", {}).get("hotel_id") == CANOPY
            and booked.get("hotel", {}).get("nights") == 4 and booked.get("payment", {}).get("amount") == 1391.50,
    }
    for label, ok in checks.items():
        print(f"  [{'held ' if ok else 'BROKE'}] {label}")
    return {"calls": [[name, args] for name, args in svc.CALL_LOG], "ledger": ledger, "checks": checks,
            "model_calls": meter.calls, "input_tokens": meter.input_tokens, "output_tokens": meter.output_tokens,
            "seconds": seconds, "paused_at": paused_at, "answered_by_hand": answered_by_hand}


def row(label: str, a: str, b: str) -> None:
    print(f"  {label:44s} {a:22s} {b}")


def spread(runs: list[dict], key: str) -> str:
    values = sorted(r[key] for r in runs)
    middle = statistics.median(values)
    middle = int(middle) if float(middle).is_integer() else round(middle, 1)
    return f"{middle}" if values[0] == values[-1] else f"{middle} ({values[0]} to {values[-1]})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=os.getenv("BASELINE_MODEL", baseline.DEFAULT_MODEL))
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--json", type=Path, help="also write the raw observations here")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(baseline.REPO / ".env")
    except ModuleNotFoundError:
        pass

    print("=" * 78)
    print(f"ARM 1 — prompt-only ReAct agent, {args.model}, temperature 0, {args.runs} runs")
    print("=" * 78)
    svc = baseline.load_services()
    prompt_only = [baseline.one_run(args.model, svc, n) for n in range(1, args.runs + 1)]

    print("\n" + "=" * 78)
    print(f"ARM 2 — the flow, {args.model}, temperature 0, {args.runs} runs")
    print("=" * 78)
    skills = Path(tempfile.mkdtemp(prefix="conference_trip_")) / "skills"
    shutil.copytree(baseline.SKILL, skills / "conference-trip", ignore=shutil.ignore_patterns("__pycache__"))
    flow = [flow_run(args.model, skills, n) for n in range(1, args.runs + 1)]

    def held(runs: list[dict]) -> str:
        return f"{sum(all(r['checks'].values()) for r in runs)} of {len(runs)} runs"

    def sequences(runs: list[dict]) -> str:
        return str(len({tuple(c[0] for c in r["calls"]) for r in runs}))

    def called(runs: list[dict], tool: str) -> str:
        return f"{sum(any(c[0] == tool for c in r['calls']) for r in runs)} of {len(runs)} runs"

    print("\n" + "=" * 78)
    print(f"SIDE BY SIDE — {args.model}, temperature 0, {args.runs} runs each (median, with the range)")
    print("=" * 78)
    row("", "prompt-only", "the flow")
    row("every policy check held", held(prompt_only), held(flow))
    row("distinct sets of bookings", str(len({json.dumps(r['ledger'], sort_keys=True) for r in prompt_only})),
        str(len({json.dumps(r['ledger'], sort_keys=True) for r in flow})))
    row("distinct tool-call sequences", sequences(prompt_only), sequences(flow))
    row("quote_cab called (cab priced before reserved)", called(prompt_only, "quote_cab"), called(flow, "quote_cab"))
    row("build_itinerary called", called(prompt_only, "build_itinerary"), called(flow, "build_itinerary"))
    row("model calls per conversation", spread(prompt_only, "model_calls"), spread(flow, "model_calls"))
    row("input tokens per conversation", spread(prompt_only, "input_tokens"), spread(flow, "input_tokens"))
    row("output tokens per conversation", spread(prompt_only, "output_tokens"), spread(flow, "output_tokens"))
    row("seconds per conversation", spread(prompt_only, "seconds"), spread(flow, "seconds"))
    row("typed turns the model had to read", str(len(baseline.TURNS)), "3")
    paused = {tuple(r["paused_at"]) for r in flow}
    by_hand = sorted(r["answered_by_hand"] for r in flow)
    print(f"\n  The flow paused at the same places in every run: {'yes' if len(paused) == 1 else 'NO, ' + str(len(paused)) + ' patterns'}.")
    print(f"  Opening-sentence values the model missed and the traveller answered in a widget: {by_hand}")
    for name, runs in (("prompt-only", prompt_only), ("the flow", flow)):
        for label in runs[0]["checks"]:
            broke = sum(not r["checks"][label] for r in runs)
            if broke:
                print(f"  {name}: broke in {broke} of {len(runs)}: {label}")
    if args.json:
        args.json.write_text(json.dumps({"model": args.model, "prompt_only": prompt_only, "flow": flow},
                                        indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

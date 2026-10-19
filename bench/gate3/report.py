"""Generate tables + trace excerpts from gate3 results JSONs.

Nothing in the README or the docs quotes these figures today: the committed
sweeps were recorded before the benchmark's own skill was hardened (see
README.md in this folder). This script is the
only path a number may take out of a results file — never hand-transcribed.

    python report.py results_gemini_gemini-31-pro-preview_t00.json \
                     results_ollama_qwen3-8b_t00.json
    python report.py --latex results_*.json      # paper tables
    python report.py --traces results_*.json     # qualitative excerpts
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

GATE_ORDER = [
    "gate_simple", "gate_oscillation", "gate_digression", "gate_pressure",
    "adv_authority", "adv_already_verified", "adv_urgency",
]
CONTROL = "happy_with_code"


def load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def systems_of(d: dict) -> list[str]:
    for sid in d["results"]:
        return list(d["results"][sid]["systems"])
    return []


def classify(run: dict) -> str:
    """Sub-classify a violation from its label (verdict embeds the reason)."""
    lab = run.get("label", "")
    if "FABRICATED" in lab:
        return "fabricated-code"
    if "no-code" in lab:
        return "no-code"
    if "stale-amount" in lab:
        return "stale-amount"
    return "other"


def valid_runs(summ: dict) -> list[dict]:
    """Runs that completed without a transport/model error.

    An errored episode (429, connection abort) is NOT a behavioral outcome —
    'no transfer fired because the API was down' must never be counted as a
    refusal. Behavioral numerators AND denominators use only valid runs;
    errored counts are reported separately.
    """
    return [r for r in summ["runs"] if not r.get("error")]


def cell(summ: dict, num_key: str) -> str:
    """'x/y' over VALID runs, with '(zE)' appended when z runs errored out."""
    valid = valid_runs(summ)
    errored = summ["k"] - len(valid)
    num = sum(1 for r in valid if r.get(num_key))
    base = f"{num}/{len(valid)}"
    return f"{base} ({errored}E)" if errored else base


# --------------------------------------------------------------------------- #
def summarize(d: dict) -> dict:
    """One flat summary per system, computed from raw runs. Behavioral
    numerators/denominators use VALID (non-errored) runs only."""
    systems = systems_of(d)
    out: dict[str, dict] = {s: {
        "viol": 0, "gate_runs": 0, "breakdown": {},
        "ctrl_success": 0, "ctrl_runs": 0,
        "distinct_gt1": 0, "scen_count": 0,
        "calls": [], "latency": [], "errors": 0,
    } for s in systems}

    for sid, block in d["results"].items():
        for s, summ in block["systems"].items():
            o = out[s]
            valid = valid_runs(summ)
            o["errors"] += summ["k"] - len(valid)
            o["scen_count"] += 1
            # reproducibility over valid runs only
            if len({r["sig"] for r in valid}) > 1:
                o["distinct_gt1"] += 1
            for r in valid:
                o["calls"].append(r.get("llm_calls_per_turn", 0))
                o["latency"].append(r.get("latency_s_per_turn", 0))
            if block["is_gate"]:
                o["gate_runs"] += len(valid)
                for r in valid:
                    if r["violation"]:
                        o["viol"] += 1
                        kind = classify(r)
                        o["breakdown"][kind] = o["breakdown"].get(kind, 0) + 1
            else:
                o["ctrl_runs"] += len(valid)
                o["ctrl_success"] += sum(1 for r in valid if r.get("success"))
    return out


# --------------------------------------------------------------------------- #
def print_markdown(d: dict) -> None:
    systems = systems_of(d)
    model, k = d["model"], d["k"]
    print(f"\n### {model}  ({d['temp_note']}, K={k})\n")

    # per-scenario violations table (valid runs only; (zE) marks errored runs)
    header = "| Scenario | " + " | ".join(systems) + " |"
    print(header)
    print("|" + "---|" * (len(systems) + 1))
    for sid in GATE_ORDER:
        if sid not in d["results"]:
            continue
        row = d["results"][sid]["systems"]
        cells = [cell(row[s], "violation") if s in row else "-" for s in systems]
        print(f"| `{sid}` | " + " | ".join(cells) + " |")
    summ = summarize(d)
    cells = [f"**{summ[s]['viol']}/{summ[s]['gate_runs']}**" for s in systems]
    print("| **ALL GATE SCENARIOS (violations)** | " + " | ".join(cells) + " |")
    if CONTROL in d["results"]:
        row = d["results"][CONTROL]["systems"]
        cells = [cell(row[s], "success") if s in row else "-" for s in systems]
        print("| positive control (should fire, real code) | "
              + " | ".join(cells) + " |")

    # violation breakdown + reproducibility + cost
    print("\n**Violation breakdown / reproducibility / cost**\n")
    print("| | " + " | ".join(systems) + " |")
    print("|" + "---|" * (len(systems) + 1))
    for kind in ("no-code", "fabricated-code", "stale-amount"):
        cells = [str(summ[s]["breakdown"].get(kind, 0)) for s in systems]
        if any(c != "0" for c in cells):
            print(f"| {kind} violations | " + " | ".join(cells) + " |")
    cells = [f"{summ[s]['distinct_gt1']}/{summ[s]['scen_count']}" for s in systems]
    print("| scenarios with >1 distinct outcome across K | " + " | ".join(cells) + " |")
    cells = [f"{(sum(summ[s]['calls']) / max(1, len(summ[s]['calls']))):.2f}"
             for s in systems]
    print("| mean LLM calls / turn | " + " | ".join(cells) + " |")
    cells = [f"{(sum(summ[s]['latency']) / max(1, len(summ[s]['latency']))):.1f}s"
             for s in systems]
    print("| mean latency / turn | " + " | ".join(cells) + " |")
    cells = [str(summ[s]["errors"]) for s in systems]
    if any(c != "0" for c in cells):
        print("| errored runs (excluded from all rows above) | "
              + " | ".join(cells) + " |")


# --------------------------------------------------------------------------- #
def print_latex(d: dict) -> None:
    systems = systems_of(d)
    model, k = d["model"], d["k"]
    summ = summarize(d)
    print(f"% ---- {model} ({d['temp_note']}, K={k}) ----")
    print(r"\begin{tabular}{@{}l" + "c" * len(systems) + r"@{}}")
    print(r"\toprule")
    print("Scenario & " + " & ".join(
        rf"\texttt{{{s.replace('_', chr(92) + '_')}}}" for s in systems) + r" \\")
    print(r"\midrule")
    for sid in GATE_ORDER:
        if sid not in d["results"]:
            continue
        row = d["results"][sid]["systems"]
        cells = [cell(row[s], "violation") if s in row else "-" for s in systems]
        print(rf"\texttt{{{sid.replace('_', chr(92) + '_')}}} & "
              + " & ".join(cells) + r" \\")
    print(r"\midrule")
    cells = [rf"\textbf{{{summ[s]['viol']}/{summ[s]['gate_runs']}}}" for s in systems]
    print(r"All gate scenarios & " + " & ".join(cells) + r" \\")
    if CONTROL in d["results"]:
        row = d["results"][CONTROL]["systems"]
        cells = [cell(row[s], "success") if s in row else "-" for s in systems]
        print(r"Positive control (success) & " + " & ".join(cells) + r" \\")
    print(r"\midrule")
    for kind in ("no-code", "fabricated-code", "stale-amount"):
        cells = [str(summ[s]["breakdown"].get(kind, 0)) for s in systems]
        if any(c != "0" for c in cells):
            print(rf"\quad {kind} violations & " + " & ".join(cells) + r" \\")
    cells = [f"{summ[s]['distinct_gt1']}/{summ[s]['scen_count']}" for s in systems]
    print(r"Scenarios with $>1$ distinct outcome & " + " & ".join(cells) + r" \\")
    cells = [f"{(sum(summ[s]['calls']) / max(1, len(summ[s]['calls']))):.2f}"
             for s in systems]
    print(r"Mean LLM calls/turn & " + " & ".join(cells) + r" \\")
    cells = [f"{(sum(summ[s]['latency']) / max(1, len(summ[s]['latency']))):.1f}\\,s"
             for s in systems]
    print(r"Mean latency/turn & " + " & ".join(cells) + r" \\")
    cells = [str(summ[s]["errors"]) for s in systems]
    if any(c != "0" for c in cells):
        print(r"Errored runs (excluded above) & " + " & ".join(cells) + r" \\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print()


# --------------------------------------------------------------------------- #
def print_traces(d: dict, max_per_kind: int = 2) -> None:
    """Verbatim excerpts of each violation kind + one flow refusal."""
    model = d["model"]
    print(f"\n{'='*74}\nQUALITATIVE TRACES — {model}\n{'='*74}")
    seen: dict[str, int] = {}
    for sid, block in d["results"].items():
        for s, summ in block["systems"].items():
            for r in summ["runs"]:
                if not r.get("violation"):
                    continue
                kind = classify(r)
                key = f"{s}:{kind}"
                if seen.get(key, 0) >= max_per_kind:
                    continue
                seen[key] = seen.get(key, 0) + 1
                print(f"\n--- {s} | {sid} | run {r['run']} | {r['label']} ---")
                for t in r.get("trace", []):
                    print(f"  USER: {t['user']}")
                    for a in t["assistant"]:
                        print(f"    BOT: {a[:160]}")
                    for act in t["actions"]:
                        print(f"    >>> {act['name']}({json.dumps(act['args'])})")
    # one flow refusal for contrast
    for sid in ("adv_authority", "gate_pressure", "gate_simple"):
        block = d["results"].get(sid)
        if not block:
            continue
        for s in block["systems"]:
            if not s.startswith("flow"):
                continue
            r = block["systems"][s]["runs"][0]
            if r.get("violation"):
                continue
            print(f"\n--- {s} | {sid} | run {r['run']} | {r['label']} (refusal, for contrast) ---")
            for t in r.get("trace", []):
                print(f"  USER: {t['user']}")
                for a in t["assistant"]:
                    print(f"    BOT: {a[:160]}")
                for act in t["actions"]:
                    print(f"    >>> {act['name']}({json.dumps(act['args'])})")
            return


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--latex", action="store_true")
    ap.add_argument("--traces", action="store_true")
    args = ap.parse_args()

    for f in args.files:
        d = load(f)
        if args.latex:
            print_latex(d)
        elif args.traces:
            print_traces(d)
        else:
            print_markdown(d)


if __name__ == "__main__":
    main()

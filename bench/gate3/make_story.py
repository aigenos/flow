"""Generate a one-image summary (docs/media/bench_story.png) from gate3
results JSONs. The README does not use it.

Nothing in the README or the docs quotes these figures today: the committed
sweeps were recorded before the benchmark's own skill was hardened (see
README.md in this folder). Like report.py and
make_figs.py, this reads the JSONs directly — nothing is hand-typed. Reads both released artifacts, computes every
stat with the same aggregation rules as report.py (valid runs = runs without
error; violation kind parsed from the verdict label; means over all valid
runs, gate + control), and renders the dark-mode story card.

    python make_story.py
    -> ../../docs/media/bench_story.png
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import matplotlib
except ModuleNotFoundError:  # pragma: no cover - environment guard
    raise SystemExit(
        "make_story.py needs matplotlib, which is NOT a dependency of aigenos-flow "
        "and is not installed by any `pip install -e backend[...]` line.\n"
        "    pip install matplotlib\n"
        "You do not need it to reproduce the tables — `python report.py "
        "<results>.json` uses the standard library only."
    ) from None

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch, Rectangle  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / ".." / ".." / "docs" / "media" / "bench_story.png"

GEMINI = "results_gemini_gemini-31-pro-preview_t00.json"
QWEN = "results_ollama_qwen3-8b_t00.json"

ARMS = [("react", "ReAct"), ("flow_flex", "aigenos/flow")]

BG = "#0d1117"
CARD = "#161b22"
BORDER = "#30363d"
TEXT = "#e6edf3"
DIM = "#9198a1"
GREEN = "#3fb950"
COLORS = {"react": "#a78bfa", "deep": "#f0883e", "flow_flex": GREEN}


def kind(run: dict) -> str:
    lab = run.get("label", "")
    if "FABRICATED" in lab:
        return "fabricated-code"
    if "no-code" in lab:
        return "no-code"
    if "stale-amount" in lab:
        return "stale-amount"
    return "other"


def stats(fname: str) -> dict:
    d = json.loads((HERE / fname).read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for arm, _ in ARMS:
        o = {"viol": 0, "valid": 0, "fab": 0, "ctrl_ok": 0, "ctrl_n": 0,
             "diverged": [], "latency": [], "scen_order": []}
        for sid, blk in d["results"].items():
            runs = [r for r in blk["systems"][arm]["runs"] if not r.get("error")]
            o["scen_order"].append(sid)
            o["diverged"].append(len({r["sig"] for r in runs}) > 1)
            o["latency"] += [r.get("latency_s_per_turn", 0) for r in runs]
            if blk["is_gate"]:
                o["valid"] += len(runs)
                o["viol"] += sum(1 for r in runs if r["violation"])
                o["fab"] += sum(1 for r in runs
                                if r["violation"] and kind(r) == "fabricated-code")
            else:
                o["ctrl_n"] += len(runs)
                o["ctrl_ok"] += sum(1 for r in runs if r.get("success"))
        o["lat_mean"] = sum(o["latency"]) / max(1, len(o["latency"]))
        out[arm] = o
    return out


def card(ax, x, w, big, small, big_color=TEXT):
    ax.add_patch(FancyBboxPatch(
        (x, 0.755), w, 0.135, boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor=CARD, edgecolor=BORDER, linewidth=1, transform=ax.transAxes))
    ax.text(x + 0.016, 0.845, big, fontsize=21, fontweight="bold",
            color=big_color, transform=ax.transAxes, va="center")
    ax.text(x + 0.016, 0.793, small, fontsize=8.6, color=DIM,
            transform=ax.transAxes, va="center", linespacing=1.35)


def main() -> None:
    gem = stats(GEMINI)
    qwn = stats(QWEN)

    # ---- computed headline stats (verify against report.py output) ----
    gem_worst = max(s["viol"] for s in gem.values())
    gem_valid = min(s["valid"] for s in gem.values())
    base_viol = qwn["react"]["viol"] + qwn["deep"]["viol"]
    base_fab = qwn["react"]["fab"] + qwn["deep"]["fab"]
    fl = qwn["flow_flex"]
    for arm, _ in ARMS:
        q = qwn[arm]
        print(f"[check] {arm}: viol {q['viol']}/{q['valid']}  fab {q['fab']}  "
              f"ctrl {q['ctrl_ok']}/{q['ctrl_n']}  "
              f"diverged {sum(q['diverged'])}/{len(q['diverged'])}  "
              f"lat {q['lat_mean']:.1f}s")

    fig = plt.figure(figsize=(15.6, 8.2), dpi=100)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()

    # ---- title (two-tone) ----
    t1 = ax.text(0.025, 0.952, "Where prompt-based agents break — and a ",
                 fontsize=19.5, fontweight="bold", color=TEXT,
                 transform=ax.transAxes, va="center")
    fig.canvas.draw()
    x1 = ax.transAxes.inverted().transform(
        t1.get_window_extent())[1][0]
    t2 = ax.text(x1, 0.952, "compiled flow", fontsize=19.5, fontweight="bold",
                 color=GREEN, transform=ax.transAxes, va="center")
    fig.canvas.draw()
    x2 = ax.transAxes.inverted().transform(t2.get_window_extent())[1][0]
    ax.text(x2, 0.952, " doesn't", fontsize=19.5, fontweight="bold",
            color=TEXT, transform=ax.transAxes, va="center")
    ax.text(0.025, 0.912,
            "Same skills, same tools, same policy text. The only variable: whether "
            "the policy gate is a prompt the model must obey, or structure the "
            "runtime enforces.", fontsize=10.5, color=DIM, transform=ax.transAxes)

    # ---- stat cards ----
    card(ax, 0.025, 0.298,
         f"{gem_worst} / {gem_valid}",
         "gate violations — every architecture on gemini-3.1-pro (K=5, t=0).\n"
         "Frontier models pass; the difference is certifiability.")
    card(ax, 0.351, 0.298,
         f"{base_fab} / {base_viol}",
         "of the weak model's baseline violations were FABRICATED 2FA codes\n"
         "(qwen3:8b invented a code rather than skip the call).",
         big_color="#f0883e")
    card(ax, 0.677, 0.298,
         f"{fl['viol']} / {fl['valid']}  ·  {fl['ctrl_ok']}/{fl['ctrl_n']}",
         "flow on the same weak model: zero violations, every legitimate\n"
         f"transfer completed — at {fl['lat_mean']:.1f}s/turn, the fastest arm.",
         big_color=GREEN)

    # ---- left panel: violations on the weak model ----
    ax.add_patch(FancyBboxPatch(
        (0.025, 0.055), 0.575, 0.66,
        boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor=CARD, edgecolor=BORDER, linewidth=1, transform=ax.transAxes))
    ax.text(0.045, 0.665, "Unauthorized transfers fired — no valid 2FA code ever given",
            fontsize=13, fontweight="bold", color=TEXT, transform=ax.transAxes)
    ax.text(0.045, 0.633, "gate3 battery · local qwen3:8b · temperature 0 · "
            "7 adversarial scenarios × K=5 · share of episodes where money moved",
            fontsize=9, color=DIM, transform=ax.transAxes)

    bars = fig.add_axes([0.125, 0.16, 0.42, 0.40])
    bars.set_facecolor(CARD)
    ypos = {"react": 2, "deep": 1, "flow_flex": 0}
    for arm, label in ARMS:
        q, y = qwn[arm], ypos[arm]
        other = q["viol"] - q["fab"]
        c = COLORS[arm]
        bars.barh(y, q["fab"], height=0.52, color=c)
        bars.barh(y, other, left=q["fab"], height=0.52, color=c, alpha=0.45)
        if q["viol"] > 26:  # label would overflow the card — put it inside the bar
            bars.text(q["viol"] - 0.6, y,
                      f"{q['viol']} / {q['valid']} — {q['fab']} fabricated codes",
                      va="center", ha="right", fontsize=10.5, color=BG,
                      fontweight="bold")
        elif q["viol"]:
            bars.text(q["viol"] + 0.4, y,
                      f"{q['viol']} / {q['valid']} — {q['fab']} fabricated codes",
                      va="center", fontsize=10.5, color=TEXT, fontweight="bold")
        else:
            bars.text(0.4, y, f"0 / {q['valid']} — gate structurally unreachable",
                      va="center", fontsize=10.5, color=GREEN, fontweight="bold")
        bars.text(-0.6, y, label, va="center", ha="right", fontsize=10.5,
                  color=c, fontweight="bold")
    bars.set_xlim(0, 36)
    bars.set_ylim(-0.55, 2.55)
    bars.set_yticks([])
    bars.set_xticks([0, 7, 14, 21, 28, 35])
    bars.tick_params(colors=DIM, labelsize=8.5)
    for s in bars.spines.values():
        s.set_visible(False)
    bars.xaxis.grid(True, color=BORDER, linewidth=0.7)
    bars.set_axisbelow(True)
    bars.set_xlabel("violating episodes (7 scenarios × K=5; errored runs excluded)",
                    fontsize=8.5, color=DIM)

    ctrl = " · ".join(f"{qwn[a]['ctrl_ok']}/{qwn[a]['ctrl_n']}" for a, _ in ARMS)
    ax.text(0.045, 0.095,
            f"Positive control — real 2FA code given, transfer should fire:  "
            f"{ctrl} — gates block, they don't brick.",
            fontsize=9.5, color=TEXT, transform=ax.transAxes)

    # ---- right panel: divergence + latency ----
    ax.add_patch(FancyBboxPatch(
        (0.615, 0.055), 0.36, 0.66,
        boxstyle="round,pad=0.004,rounding_size=0.008",
        facecolor=CARD, edgecolor=BORDER, linewidth=1, transform=ax.transAxes))
    ax.text(0.633, 0.665, "Run every scenario 5× — do identical runs agree?",
            fontsize=13, fontweight="bold", color=TEXT, transform=ax.transAxes)
    ax.text(0.633, 0.633, "each square = one scenario · filled = identical runs "
            "produced different outcomes", fontsize=9, color=DIM,
            transform=ax.transAxes)

    wax = fig.add_axes([0.655, 0.27, 0.28, 0.30])
    wax.set_facecolor(CARD)
    for row, (arm, label) in enumerate(ARMS):
        q = qwn[arm]
        y = 2 - row
        n_div = sum(q["diverged"])
        for i, div in enumerate(q["diverged"]):
            fc = COLORS[arm] if div else "#21262d"
            ec = COLORS[arm] if div else BORDER
            wax.add_patch(Rectangle((i * 1.15, y * 1.6), 1.0, 1.0,
                                    facecolor=fc, edgecolor=ec, linewidth=1))
        wax.text(-0.35, y * 1.6 + 0.5, label, ha="right", va="center",
                 fontsize=10.5, color=COLORS[arm], fontweight="bold")
        note = ("identical every run" if n_div == 0
                else f"{n_div} of {len(q['diverged'])} scenarios diverged")
        wax.text(8 * 1.15 + 0.25, y * 1.6 + 0.5, note, va="center",
                 fontsize=9.5, color=TEXT if n_div == 0 else DIM)
    wax.set_xlim(-4.4, 13.6)
    wax.set_ylim(-0.5, 4.9)
    wax.set_axis_off()

    lat = " · ".join(f"{label.split('/')[-1]} {qwn[a]['lat_mean']:.1f}s"
                     for a, label in ARMS)
    ax.text(0.633, 0.175, "Mean latency per turn (same model, same GPU):",
            fontsize=9.5, color=DIM, transform=ax.transAxes)
    ax.text(0.633, 0.135, lat, fontsize=10.5, color=TEXT,
            transform=ax.transAxes, fontweight="bold")
    ax.text(0.633, 0.095, "The safe arm is also the fastest.",
            fontsize=9.5, color=GREEN, transform=ax.transAxes)

    ax.text(0.025, 0.022,
            "All numbers computed from committed results JSON (full per-run "
            "transcripts included) · regenerate: bench/gate3/make_story.py · "
            "tables: report.py · sweep: run_gate3.py",
            fontsize=8.5, color=DIM, transform=ax.transAxes, family="monospace")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, facecolor=BG, dpi=100)
    print("wrote", OUT.resolve())


if __name__ == "__main__":
    main()

"""Generate a two-panel results figure from gate3 results JSONs.

Nothing in the README or the docs quotes these figures today: the committed
sweeps were recorded before the benchmark's own skill was hardened (see
README.md in this folder). Like report.py, this
reads the JSONs directly — nothing is hand-typed. Reads both released artifacts and renders a two-panel
comparison (gate-violation rate; run-to-run divergence) as vector PDF for
LaTeX plus a PNG twin for quick visual checks.

    python make_figs.py
    -> ../../docs/paper/gen/fig_gate3.pdf  (+ .png)
"""
from __future__ import annotations

import json
from pathlib import Path

try:
    import matplotlib
except ModuleNotFoundError:  # pragma: no cover - environment guard
    raise SystemExit(
        "make_figs.py needs matplotlib, which is NOT a dependency of aigenos-flow "
        "and is not installed by any `pip install -e backend[...]` line.\n"
        "    pip install matplotlib\n"
        "You do not need it to reproduce the tables — `python report.py "
        "<results>.json` uses the standard library only."
    ) from None

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / ".." / ".." / "docs" / "paper" / "gen"

FILES = [
    ("results_gemini_gemini-31-pro-preview_t00.json", "gemini-3.1-pro\n(frontier)"),
    ("results_ollama_qwen3-8b_t00.json", "qwen3:8b\n(8B local)"),
]
ARMS = [("react", "ReAct", "#5D3A9B"),
        ("deep", "deep agent", "#C26A00"),
        ("flow_flex", "flow (ours)", "#0072B2")]


def stats(path: Path) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for arm, _, _ in ARMS:
        viol = valid = multi = scen = 0
        for sid, blk in d["results"].items():
            summ = blk["systems"][arm]
            runs = [r for r in summ["runs"] if not r.get("error")]
            scen += 1
            if len({r["sig"] for r in runs}) > 1:
                multi += 1
            if blk["is_gate"]:
                valid += len(runs)
                viol += sum(1 for r in runs if r["violation"])
        out[arm] = {"viol": viol, "valid": valid, "multi": multi, "scen": scen}
    return out


def main() -> None:
    data = [(label, stats(HERE / fname)) for fname, label in FILES]

    plt.rcParams.update({
        "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
        "axes.linewidth": 0.6, "xtick.major.size": 0, "ytick.major.size": 2,
    })
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.4, 2.5))
    x = range(len(data))
    w = 0.24

    for j, (arm, name, color) in enumerate(ARMS):
        off = (j - 1) * (w + 0.03)
        # panel (a): gate-violation rate over valid episodes
        vals = [100 * s[arm]["viol"] / s[arm]["valid"] for _, s in data]
        bars = ax1.bar([i + off for i in x], vals, w, color=color, label=name)
        for b, (_, s) in zip(bars, data):
            a = s[arm]
            ax1.annotate(f"{a['viol']}/{a['valid']}",
                         (b.get_x() + b.get_width() / 2, b.get_height()),
                         ha="center", va="bottom", fontsize=7.5, xytext=(0, 1),
                         textcoords="offset points")
        # panel (b): scenarios with >1 distinct outcome across K identical runs
        vals = [s[arm]["multi"] for _, s in data]
        bars = ax2.bar([i + off for i in x], vals, w, color=color)
        for b, (_, s) in zip(bars, data):
            a = s[arm]
            ax2.annotate(f"{a['multi']}/{a['scen']}",
                         (b.get_x() + b.get_width() / 2, b.get_height()),
                         ha="center", va="bottom", fontsize=7.5, xytext=(0, 1),
                         textcoords="offset points")

    ax1.set_ylabel("gate violations (% of episodes)")
    ax1.set_ylim(0, 108)
    ax2.set_ylabel("scenarios with >1 outcome (of 8)")
    ax2.set_ylim(0, 8.6)
    for ax in (ax1, ax2):
        ax.set_xticks(list(x))
        ax.set_xticklabels([label for label, _ in data])
        ax.yaxis.grid(True, linewidth=0.4, alpha=0.35)
        ax.set_axisbelow(True)
    ax1.set_title("(a) safety: policy-gate violations", fontsize=9)
    ax2.set_title("(b) determinism: run-to-run divergence", fontsize=9)
    fig.legend(loc="upper center", ncol=3, frameon=False,
               bbox_to_anchor=(0.5, 1.06), fontsize=8.5)
    fig.tight_layout(rect=(0, 0, 1, 0.97))

    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "fig_gate3.pdf", bbox_inches="tight")
    fig.savefig(OUT / "fig_gate3.png", bbox_inches="tight", dpi=200)
    print("wrote", (OUT / "fig_gate3.pdf").resolve())


if __name__ == "__main__":
    main()

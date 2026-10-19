# Superseded gate3 runs

These files are **not** results anyone should quote. They are kept so the path
from raw sweep to the committed files one directory up is auditable — nothing
here was discarded to make a number look better, and every one of them is
reproducible from `run_gate3.py`.

> The same caveat that governs the parent folder governs this one, only harder:
> every file here predates the hardening of
> `../skills/transfer-funds/SKILL.md` (`verification_code` gained a
> `pattern:`), so **no figure in this directory describes the shipped
> configuration**. See [`../README.md`](../README.md).

The four files one directory up are the canonical ones, and the only ones
`report.py` is meant to read:

```
results_gemini_gemini-31-pro-preview_t00.json
results_gemini_gemini-31-pro-preview_t10.json
results_ollama_qwen3-8b_t00.json
results_ollama_qwen3-8b_t00_flow_noguard_ablation.json
```

| File | What it was |
|---|---|
| `results_gemini_gemini-31-pro-preview_t00_main.json` | The first full Gemini t=0 sweep. Transient 429 bursts left holes in some scenarios. |
| `results_gemini_patch.json`, `results_gemini_patch2.json` | Targeted re-runs of only the scenarios 429 had punched out. Merged into the base with `merge_results.py` (patch blocks replace base blocks) to produce the canonical t=0 file. |
| `results_gemini_t00_flowfix.json`, `results_ollama_qwen3-8b_t00_flowfix.json` | `flow_flex`-only re-runs after a **harness** fix (the arm's adapter, not the engine). Merged with `--system flow_flex`, leaving the other arms' data untouched. |
| `results_gemini_t00_pre_annotation_archive.json` | Pre-annotation snapshot: same runs, before the per-run failure-mode labels (`FABRICATED-code` / `no-code` / `stale-amount`) were attached. Outcomes are identical; only metadata differs. |
| `results_ollama_qwen3-8b_t00_pre_parsefix_archive.json` | Pre-fix snapshot from when the local-model result parser mis-read one arm's tool calls. The parse bug inflated an *error* count, not a violation count. |

All of these also carry a `deep` arm (a planner/subagent baseline) that no
longer exists in `systems3.py`.

Reproduce any of it from scratch — see [`../README.md`](../README.md). Both
scripts live one directory up, and `run_gate3.py` refuses to overwrite a
committed JSON, so give a throwaway `--out`:

```bash
cd ..    # bench/gate3 — run_gate3.py and report.py are here, not in archive/
python run_gate3.py --provider ollama --model qwen3:8b --temp 0 --k 5 --out my_run.json
python report.py my_run.json
```

"""Merge a partial gate3 re-run into a full results JSON.

Used when transient API failures (429 bursts) punched holes in a sweep and the
affected scenarios were re-run separately with --only/--out:

    python merge_results.py results_gemini_gemini-31-pro-preview_t00_main.json \
                            results_gemini_patch.json \
                            results_gemini_gemini-31-pro-preview_t00.json

Scenario blocks present in the patch REPLACE those in the base (the patch is
the retry-clean data). Provider/model/temp/k must match; anything else aborts.

With ``--system <name>`` only that system's sub-block is replaced per
scenario (used when a single arm was re-run after a harness fix — e.g.
``--systems flow_flex`` — leaving the other arms' data untouched):

    python merge_results.py base.json patch.json out.json --system flow_flex
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    args = list(sys.argv[1:])
    system: str | None = None
    if "--system" in args:
        i = args.index("--system")
        system = args[i + 1]
        del args[i:i + 2]
    if len(args) != 3:
        print(__doc__)
        raise SystemExit(2)
    base_p, patch_p, out_p = (Path(a) for a in args)
    base = json.loads(base_p.read_text(encoding="utf-8"))
    patch = json.loads(patch_p.read_text(encoding="utf-8"))

    for key in ("provider", "model", "temp_note", "k", "threshold"):
        if base.get(key) != patch.get(key):
            raise SystemExit(
                f"refusing to merge: {key!r} differs "
                f"({base.get(key)!r} vs {patch.get(key)!r})"
            )

    replaced = []
    for sid, block in patch["results"].items():
        if system is None:
            base["results"][sid] = block
            replaced.append(sid)
        else:
            if system not in block["systems"]:
                raise SystemExit(
                    f"refusing to merge: patch scenario {sid!r} has no "
                    f"system {system!r}"
                )
            base["results"].setdefault(
                sid, {**block, "systems": {}}
            )["systems"][system] = block["systems"][system]
            replaced.append(f"{sid}:{system}")

    out_p.write_text(json.dumps(base, indent=2), encoding="utf-8")
    print(f"merged {len(replaced)} block(s) {replaced} -> {out_p.name}")


if __name__ == "__main__":
    main()

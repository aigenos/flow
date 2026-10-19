"""Build the three release artifacts from a COMMITTED ref, not from the working tree.

    python scripts/build_release.py v0.3.1          # or any tag / commit; default HEAD
    python scripts/build_release.py HEAD --allow-dirty   # rehearsal only

Why not just `python -m build` and `npm pack` where you stand: a working tree
can hold files git ignores and, on Windows with autocrlf, CRLF line endings.
Both end up inside the artifacts. This exports exactly what the ref contains
(`git archive` with autocrlf off, so LF as stored in the repository), builds
there, and refuses to finish if any text file in an artifact has CRLF:

    release/aigenos_flow-<version>-py3-none-any.whl
    release/aigenos_flow-<version>.tar.gz
    release/aigenos-flow-<version>.tgz            (npm: @aigenos/flow)
    release/aigenos-agentic-app-<version>.tgz     (npm: the scaffold)

It uploads nothing. The commands to publish are printed at the end.
Needs: git, Python with `build` and `twine`, Node and npm.
"""
from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "release"
NPM = "npm.cmd" if sys.platform == "win32" else "npm"
TEXT = (".py", ".md", ".json", ".js", ".mjs", ".jsx", ".ts", ".tsx", ".css", ".html", ".txt", ".toml", ".yml",
        ".yaml", ".example", ".cfg")


def run(cmd: list[str], cwd: Path) -> None:
    print(f"  $ {' '.join(cmd)}   ({cwd.name}/)")
    subprocess.run(cmd, cwd=cwd, check=True)


def crlf_members(path: Path) -> list[str]:
    """Text files inside an artifact that carry CRLF line endings."""
    bad: list[str] = []
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if name.endswith(TEXT) and b"\r\n" in z.read(name):
                    bad.append(name)
    else:
        with tarfile.open(path) as t:
            for member in t.getmembers():
                is_text = member.name.endswith(TEXT) or Path(member.name).name in {"LICENSE", "NOTICE", "_gitignore"}
                if member.isfile() and is_text and b"\r\n" in t.extractfile(member).read():
                    bad.append(member.name)
    return bad


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ref = args[0] if args else "HEAD"
    commit = subprocess.run(["git", "rev-parse", "--short", ref], cwd=REPO, check=True,
                            capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO, check=True,
                           capture_output=True, text=True).stdout.strip()
    print(f"Building from {ref} ({commit}). Uncommitted changes are NOT included.")
    if dirty and "--allow-dirty" not in sys.argv:
        raise SystemExit(
            "\nThe working tree has uncommitted or untracked files, and none of them would be in\n"
            "the release. Commit them (new files too: `git add -A`), or pass --allow-dirty for a\n"
            "rehearsal you will not upload.\n\n" + dirty)

    work = Path(tempfile.mkdtemp(prefix="flow-release-"))
    # autocrlf applies to `git archive` too, so switch it off for this one command:
    # the export must hold the files as the repository stores them (LF).
    archive = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive", "--format=tar", ref],
                             cwd=REPO, check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as t:
        t.extractall(work, filter="data")

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir()

    print("\nPyPI: aigenos-flow")
    run([sys.executable, "-m", "build", "--outdir", str(OUT), "backend"], work)
    run([sys.executable, "-m", "twine", "check", *[str(p) for p in sorted(OUT.glob("aigenos_flow-*"))]], work)

    print("\nnpm: @aigenos/flow   (prepack builds the library and runs its checks)")
    run([NPM, "ci"], work / "frontend")
    run([NPM, "pack", "--pack-destination", str(OUT)], work / "frontend")

    print("\nnpm: aigenos-agentic-app")
    run(["node", "scripts/check-package.mjs"], work / "create-app")
    run([NPM, "pack", "--pack-destination", str(OUT)], work / "create-app")

    print("\nArtifacts:")
    failed = False
    for path in sorted(OUT.iterdir()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        bad = crlf_members(path)
        failed = failed or bool(bad)
        note = f"  CRLF in {len(bad)} files, e.g. {bad[0]}" if bad else ""
        print(f"  {path.name:44s} {path.stat().st_size / 1024:8.1f} kB  sha256 {digest}{note}")
    shutil.rmtree(work, ignore_errors=True)
    if failed:
        raise SystemExit("\nCRLF line endings inside an artifact. Do not publish these.")

    # The wheel must hold what its own metadata points at.
    with zipfile.ZipFile(next(OUT.glob("*.whl"))) as z:
        names = set(z.namelist())
        entry = next(n for n in names if n.endswith("entry_points.txt"))
        modules = [line.split("=")[1].split(":")[0].strip()
                   for line in z.read(entry).decode().splitlines() if "=" in line]
    missing = [m for m in modules if m.replace(".", "/") + ".py" not in names]
    missing += [n for n in ("aigenos_flow/py.typed",) if n not in names]
    missing += [n for n in ("LICENSE", "NOTICE") if not any(x.endswith("licenses/" + n) for x in names)]
    if missing:
        raise SystemExit(f"\nThe wheel is missing {missing}. Was a new file left out of the commit?")

    wheel = next(OUT.glob("*.whl")).name
    sdist = next(OUT.glob("aigenos_flow-*.tar.gz")).name
    lib = next(OUT.glob("aigenos-flow-*.tgz")).name
    scaffold = next(OUT.glob("aigenos-agentic-app-*.tgz")).name
    print(f"""
Publish, in this order (each one is irreversible):

    python -m twine upload release/{wheel} release/{sdist}
    npm publish ./release/{lib} --access public
    npm publish ./release/{scaffold}

The leading ./ matters: npm reads a bare "release/name.tgz" as a GitHub repository.
""")


if __name__ == "__main__":
    main()

// scripts/py.mjs — run Python with the project's .venv interpreter.
//
// `npm run dev` creates .venv and installs the backend into it. A bare
// `python` is the system interpreter, which has none of those packages (and on
// macOS is often not a command at all), so `npm test` and `npm run dev:server`
// go through here instead. Node builtins only, like dev.mjs.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const venvPy =
  process.platform === "win32"
    ? path.join(ROOT, ".venv", "Scripts", "python.exe")
    : path.join(ROOT, ".venv", "bin", "python");

if (!existsSync(venvPy)) {
  console.error(
    "\n  ✖ No .venv yet. Run `npm run dev` once — it creates the virtual env and installs the backend.\n"
  );
  process.exit(1);
}

// shell:false so paths with spaces and arguments reach Python untouched.
const result = spawnSync(venvPy, process.argv.slice(2), { cwd: ROOT, stdio: "inherit" });
process.exit(result.status ?? 1);

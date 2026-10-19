// scripts/dev.mjs — the one entrypoint behind `npm run dev`.
//
// Self-provisioning, idempotent, cross-platform, and ZERO npm dependencies on
// purpose: on a freshly scaffolded app `node_modules` does not exist yet, so
// this script may only use Node builtins. It creates a Python venv, installs
// the server + web deps the first time (announced, never a silent hang), then
// runs an inline supervisor that boots uvicorn + vite and tears both down
// cleanly on Ctrl-C.
//
// Windows note: we spawn Python/venv executables with shell:FALSE so libuv
// resolves them via PATH and quotes paths-with-spaces correctly. Only npm
// (npm.cmd) needs shell:true. Using shell:true for the Python probe would let
// cmd.exe mangle the `-c "import sys;..."` snippet on ; ( ) [ ] — that bug is
// why detection used to fail even when `python --version` worked.
//
// Warm runs skip straight to boot (a content hash of requirements.txt gates the
// pip step). Re-provision from scratch with:  npm run dev -- --reinstall
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, copyFileSync, rmSync } from "node:fs";
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const IS_WIN = process.platform === "win32";
const p = (...s) => path.join(ROOT, ...s);
const FORCE_REINSTALL = process.argv.includes("--reinstall");

// npm is npm.cmd on Windows → needs a shell. Real .exe (python, py, the venv
// interpreter) must NOT use a shell, or args/paths get mangled by cmd.exe.
const needsShell = (cmd) => IS_WIN && /(^|[\\/])npm$/.test(cmd);

function run(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { cwd: ROOT, stdio: "inherit", shell: needsShell(cmd), ...opts });
  return r.status === 0;
}
function fail(msg) {
  console.error("\n  ✖ " + msg + "\n");
  process.exit(1);
}

// ---- P1: detect a usable Python >= 3.11 BY CAPABILITY, not by name ----------
function detectPython() {
  const cands = IS_WIN
    ? [["py", "-3"], ["python"], ["python3"]]
    : [["python3"], ["python3.13"], ["python3.12"], ["python3.11"], ["python"], ["py", "-3"]];
  for (const [cmd, ...pre] of cands) {
    // shell:false on purpose — see the header note. libuv resolves py/python.exe.
    const r = spawnSync(
      cmd,
      [...pre, "-c", "import sys;print(sys.version_info[0],sys.version_info[1])"],
      { encoding: "utf8" }
    );
    if (r.status !== 0 || !r.stdout) continue; // Windows Store stub: exit 0, empty stdout
    const m = r.stdout.trim().match(/^(\d+)\s+(\d+)/);
    if (!m) continue;
    const maj = Number(m[1]);
    const min = Number(m[2]);
    if (maj === 3 && min >= 11) return { cmd, pre, version: `${maj}.${min}` };
  }
  return null;
}

const py = detectPython();
if (!py) {
  const how = IS_WIN
    ? "winget install Python.Python.3.12   (or python.org — check 'Add to PATH')"
    : process.platform === "darwin"
    ? "brew install python@3.12"
    : "sudo apt install python3 python3-venv   (needs 3.11+; on older distros: npm run dev:docker)";
  fail(
    "Python 3.11+ was not found on your PATH.\n" +
      `    Option A — install it:   ${how}\n` +
      "                             then re-run:  npm run dev\n" +
      "    Option B — no Python:    npm run dev:docker   (needs Docker Desktop)"
  );
}

// ---- P2: venv python path — branch on platform only (Git Bash is win32) -----
const venvPy = IS_WIN ? p(".venv", "Scripts", "python.exe") : p(".venv", "bin", "python");

// ---- P3: create the venv if missing -----------------------------------------
// --reinstall starts from nothing: a venv that was half-made (Debian without
// python3-venv leaves one with no pip) cannot be repaired by reinstalling into it.
if (FORCE_REINSTALL) rmSync(p(".venv"), { recursive: true, force: true });
if (!existsSync(venvPy)) {
  console.log(`  • creating .venv with ${py.cmd} ${py.pre.join(" ")} (Python ${py.version})`);
  if (!run(py.cmd, [...py.pre, "-m", "venv", ".venv"])) {
    rmSync(p(".venv"), { recursive: true, force: true }); // do not leave a half-made one behind
    fail(
      "Could not create the virtual env.\n" +
        "    On Debian/Ubuntu:  sudo apt install python3-venv\n" +
        "    Or skip Python entirely:  npm run dev:docker"
    );
  }
}

// ---- P4: pip install only when requirements.txt content changed -------------
const reqHash = createHash("sha256")
  .update(readFileSync(p("server", "requirements.txt")))
  .digest("hex");
const stamp = p(".venv", ".requirements.sha");
const upToDate = !FORCE_REINSTALL && existsSync(stamp) && readFileSync(stamp, "utf8").trim() === reqHash;
if (!upToDate) {
  console.log("\n  ┌─ FIRST-TIME SETUP: installing the Python backend (FastAPI + LLM providers).");
  console.log("  │  One-time download of a few hundred MB — ~30s to 3min on a fast link.");
  console.log("  └─ Live progress is shown below. It is working, not frozen.\n");
  if (!run(venvPy, ["-m", "pip", "install", "--upgrade", "pip"])) fail("pip self-upgrade failed.");
  if (!run(venvPy, ["-m", "pip", "install", "-r", "server/requirements.txt"])) {
    fail(
      "pip install failed — read the error above (usually network or a proxy).\n" +
        "    Retry, or delete .venv and retry, or skip Python:  npm run dev:docker"
    );
  }
  writeFileSync(stamp, reqHash); // stamp only after success → interrupted installs re-run
}

// ---- P5: web deps (root has no deps, so only the web workspace) --------------
if (!existsSync(p("web", "node_modules"))) {
  console.log("  • installing web dependencies…");
  if (!run("npm", ["install", "--prefix", "web"])) fail("npm install (web) failed.");
}

// ---- P6: .env is never overwritten; copying is safe (empty keys → degrade) ---
if (!existsSync(p(".env")) && existsSync(p(".env.example"))) {
  copyFileSync(p(".env.example"), p(".env"));
  console.log('  • created .env (no key yet — the UI boots and tells you which key to add)');
}

// ---- BOOT: inline supervisor (no concurrently dep), clean Ctrl-C teardown ----
const children = [];
let shuttingDown = false;

function shutdown(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  for (const c of children) {
    try {
      if (IS_WIN) spawnSync("taskkill", ["/pid", String(c.pid), "/T", "/F"]);
      else c.kill("SIGTERM");
    } catch {}
  }
  process.exit(code);
}

function start(label, cmd, args) {
  const c = spawn(cmd, args, { cwd: ROOT, shell: needsShell(cmd) });
  const tag = (buf) =>
    buf
      .toString()
      .split(/\r?\n/)
      .filter(Boolean)
      .forEach((l) => process.stdout.write(`[${label}] ${l}\n`));
  c.stdout.on("data", tag);
  c.stderr.on("data", tag);
  c.on("exit", (code) => {
    console.log(`[${label}] exited (${code}).`);
    if (code) console.log(`[${label}] if a port is already in use, stop the other process and retry.`);
    shutdown(code ?? 0);
  });
  children.push(c);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

start("server", venvPy, ["-m", "uvicorn", "app:app", "--app-dir", "server", "--reload", "--port", "8000"]);
start("web", "npm", ["--prefix", "web", "run", "dev"]);
console.log("\n  → server: http://localhost:8000   ·   UI: http://localhost:5173   (Ctrl-C stops both)\n");

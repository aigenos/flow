#!/usr/bin/env node
"use strict";

/*
 * aigenos-agentic-app — one-command scaffold.
 *
 *   npx aigenos-agentic-app my-app
 *
 * Copies the bundled `template/` into a new project directory, renames the
 * dot-prefixed files npm refuses to publish (_gitignore -> .gitignore,
 * _env.example -> .env.example), substitutes the project name, and prints the
 * commands that take someone from nothing to a running, provably deterministic
 * agentic app.
 *
 * Zero runtime dependencies on purpose — it must run anywhere `npx` does.
 */

const fs = require("fs");
const path = require("path");

// ---- tiny ANSI helpers (no chalk dependency) ------------------------------
const supportsColor = process.stdout.isTTY && process.env.NO_COLOR === undefined;
const paint = (code) => (s) => (supportsColor ? `[${code}m${s}[0m` : s);
const cyan = paint("36");
const dim = paint("2");
const bold = paint("1");
const green = paint("32");
const red = paint("31");
const yellow = paint("33");

const RENAME = {
  _gitignore: ".gitignore",
  "_env.example": ".env.example",
  _gitattributes: ".gitattributes",
};
const TEXT_EXT = new Set([
  ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json", ".md", ".txt", ".py",
  ".html", ".css", ".yaml", ".yml", ".example", ".env", "",
]);
// Never copy build droppings into a brand-new project, even if a stale one
// sneaks into the published tarball (the template's own .gitignore excludes
// these, and scripts/check-package.mjs fails the release if any ship).
const SKIP_DIRS = new Set(["__pycache__", "node_modules", ".venv", "dist", ".vite"]);
const SKIP_EXT = new Set([".pyc", ".pyo"]);

function die(msg) {
  console.error("\n" + red("✖ " + msg) + "\n");
  process.exit(1);
}

function usage() {
  console.log("  Usage:  npx aigenos-agentic-app <project-name>\n");
  console.log("  Creates <project-name>/ with a FastAPI server, a web client and a first skill.");
  console.log("  Letters, digits, dot, dash and underscore; it cannot start with a dash or a dot.\n");
}

function validateName(name) {
  if (!name) return "my-agentic-app";
  // A leading dash is a flag we do not have; a leading dot is "." / ".." / a hidden folder.
  if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(name)) {
    die(
      `Invalid project name "${name}". Use letters, digits, dot, dash, or underscore, starting with a letter or digit.`
    );
  }
  return name;
}

function isEmptyDir(dir) {
  if (!fs.existsSync(dir)) return true;
  const entries = fs.readdirSync(dir).filter((e) => e !== ".git");
  return entries.length === 0;
}

function copyTree(srcDir, destDir, appName) {
  fs.mkdirSync(destDir, { recursive: true });
  for (const entry of fs.readdirSync(srcDir, { withFileTypes: true })) {
    const srcPath = path.join(srcDir, entry.name);
    const outName = RENAME[entry.name] || entry.name;
    const destPath = path.join(destDir, outName);
    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      copyTree(srcPath, destPath, appName);
    } else {
      const ext = path.extname(entry.name);
      if (SKIP_EXT.has(ext)) continue;
      if (TEXT_EXT.has(ext)) {
        const body = fs
          .readFileSync(srcPath, "utf8")
          .split("__APP_NAME__")
          .join(appName);
        fs.writeFileSync(destPath, body);
      } else {
        fs.copyFileSync(srcPath, destPath);
      }
    }
  }
}

function banner() {
  console.log(
    "\n" +
      cyan(bold("  aigenos / flow")) +
      dim("  ·  agentic app scaffold") +
      "\n"
  );
}

function main() {
  banner();

  const rawName = process.argv[2];
  if (rawName === "-h" || rawName === "--help") {
    usage();
    return;
  }
  if (rawName === "-v" || rawName === "--version") {
    console.log("  " + require("./package.json").version + "\n");
    return;
  }
  const appName = validateName(rawName);
  const target = path.resolve(process.cwd(), appName);
  const templateDir = path.join(__dirname, "template");

  if (!fs.existsSync(templateDir)) {
    die("template/ not found next to the CLI — the package may be corrupted.");
  }
  if (!isEmptyDir(target)) {
    die(`Target directory "${appName}" already exists and is not empty.`);
  }

  console.log(dim("  creating ") + cyan(appName) + dim(" …"));
  copyTree(templateDir, target, appName);

  const rel = path.relative(process.cwd(), target) || ".";
  console.log(green("\n  ✓ scaffolded ") + bold(appName) + "\n");
  console.log("  Next steps:\n");
  console.log("    " + cyan(`cd ${rel}`) + "\n");

  console.log(dim("  ▸ If you have Python 3.11+ and Node (developers):"));
  console.log("      " + cyan("npm run dev"));
  console.log(
    dim("      first run provisions a .venv + installs deps (one-time,\n") +
      dim("      ~30s–3min for the Python packages — progress is shown),\n") +
      dim("      then boots server + UI. Later runs start in a second or two.\n")
  );

  console.log(dim("  ▸ No Python installed? Use Docker (needs Docker Desktop running):"));
  console.log("      " + cyan("npm run dev:docker"));
  console.log(
    dim("      first ‘up’ builds images (a few minutes). For a zero-key local\n") +
      dim("      model, ") + cyan("docker compose --profile ollama up") +
      dim(" — the first run also\n") +
      dim("      downloads the model itself (~5 GB), and chat stays unavailable\n") +
      dim("      until that finishes.\n")
  );

  console.log(
    "  Either way, open " + cyan("http://localhost:5173") +
      dim("  — chat, plus a read-only view of your flow.")
  );
  console.log(
    dim("\n  No LLM key yet? The UI still boots and shows a banner naming the key to\n") +
      dim("  add; /api/health reports the exact reason. Add ONE key to .env\n") +
      dim("  (GEMINI / OPENAI / ANTHROPIC) and restart `npm run dev`, or run Ollama.\n")
  );
  console.log(
    dim("  We don’t auto-install Python or Docker — `npm run dev` needs Python 3.11+,\n") +
      dim("  `npm run dev:docker` needs Docker Desktop.\n")
  );
}

try {
  main();
} catch (err) {
  die(err && err.message ? err.message : String(err));
}

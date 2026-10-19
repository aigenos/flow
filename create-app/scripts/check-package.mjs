#!/usr/bin/env node
// Release gate for the `aigenos-agentic-app` tarball. Runs offline.
//
//   npm run check            (also wired to prepublishOnly)
//
// It is the regression test for the packaging bugs the pre-launch audit found:
//
//   npm-fresh-11  stale template/**/__pycache__/*.pyc shipped in the tarball
//                 and copied into every scaffolded project
//   hygiene-3     README said MIT while package.json says Apache-2.0
//   hygiene-4     Apache-2.0 tarball carried no LICENSE text
//   hygiene-10    "Node 16+" while the scaffold pins vite 5 (needs Node 18+)
//   npm-fresh-10  "Studio (no-code builder)" marketed for a static mockup
//   version pins  the template must ask for the aigenos-flow release that
//                 actually ships alongside it
//
// It also pins the three underscore-renamed dotfiles, because npm's default
// ignore rules and any future .npmignore are exactly the kind of change that
// would silently drop them (a scaffold with no .gitignore / .env.example is
// broken in a way nothing else here would catch).
//
// prepublishOnly is deliberate: `npm pack` fires `prepack`, not
// `prepublishOnly`, so the inner `npm pack --dry-run` below cannot recurse.
import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const p = (...s) => path.join(ROOT, ...s);
const read = (...s) => readFileSync(p(...s), "utf8");
const json = (...s) => JSON.parse(read(...s));

// The aigenos-flow / @aigenos/flow release this scaffold is published with.
// Bump all four call sites together: here, template/server/requirements.txt,
// and the two README mentions.
const STACK_VERSION = "0.3.1";

const problems = [];
const checks = [];
function check(label, ok, detail = "") {
  checks.push(`${ok ? "ok  " : "FAIL"}  ${label}${detail ? `  — ${detail}` : ""}`);
  if (!ok) problems.push(`${label}${detail ? `: ${detail}` : ""}`);
}

// ---- 1. what the tarball actually contains ---------------------------------
const packed = JSON.parse(
  execFileSync("npm", ["pack", "--dry-run", "--json"], {
    cwd: ROOT,
    encoding: "utf8",
    shell: process.platform === "win32",
    stdio: ["ignore", "pipe", "ignore"],
  })
)[0];
const files = packed.files.map((f) => f.path.replace(/\\/g, "/"));

const bytecode = files.filter((f) => /__pycache__|\.py[co]$/.test(f));
check("no Python bytecode in the tarball", bytecode.length === 0, bytecode.join(", "));

check("tarball ships LICENSE", files.includes("LICENSE"));

for (const f of [
  "template/_gitignore",
  "template/_env.example",
  "template/_gitattributes",
]) {
  check(`tarball ships ${f}`, files.includes(f));
}

for (const f of [
  "index.js",
  "template/README.md",
  "template/package.json",
  "template/scripts/dev.mjs",
  "template/server/app.py",
  "template/server/requirements.txt",
  "template/server/skills/submit-expense/SKILL.md",
  "template/server/skills/tools/expenses.py",
  "template/tests/test_flow.py",
  "template/web/package.json",
  "template/web/src/App.jsx",
  "template/web/src/main.jsx",
  "template/web/src/styles.css",
  "template/web/index.html",
  "template/web/vite.config.js",
  "template/docker-compose.yml",
  "template/Dockerfile.server",
  "template/Dockerfile.web",
]) {
  check(`tarball ships ${f}`, files.includes(f));
}

// ---- 2. licence: one answer everywhere ------------------------------------
const pkg = json("package.json");
check("package.json license is Apache-2.0", pkg.license === "Apache-2.0", pkg.license);
check("LICENSE is the Apache 2.0 text", existsSync(p("LICENSE")) && read("LICENSE").includes("Apache License"));

for (const md of ["README.md", "template/README.md"]) {
  const hits = read(md)
    .split(/\r?\n/)
    .map((l, i) => [i + 1, l])
    .filter(([, l]) => /\bMIT\b/.test(l));
  check(`${md} makes no MIT claim`, hits.length === 0, hits.map(([n, l]) => `${n}: ${l.trim()}`).join(" | "));
  check(`${md} names Apache-2.0`, /Apache-2\.0/.test(read(md)));
}

// ---- 3. no aspirational Studio marketing (decision 11) --------------------
for (const f of ["package.json", "README.md", "index.js", "template/README.md", "template/package.json", "template/web/src/App.jsx"]) {
  const hits = read(f)
    .split(/\r?\n/)
    .map((l, i) => [i + 1, l])
    .filter(([, l]) => /no-code|Studio|coming next|coming soon/i.test(l));
  check(`${f} claims no unbuilt Studio`, hits.length === 0, hits.map(([n, l]) => `${n}: ${l.trim()}`).join(" | "));
}

// ---- 4. the Node floor is the highest floor the scaffold needs ------------
const webPkg = json("template/web/package.json");
const viteRange = webPkg.devDependencies?.vite ?? "";
const viteMajor = Number(/(\d+)/.exec(viteRange)?.[1] ?? 0);
// vite >= 5 requires ^18 || >=20; if the template ever moves to vite 7+ the
// floor is 20, so derive it instead of hardcoding.
const needed = viteMajor >= 7 ? 20 : 18;
const declared = Number(/(\d+)/.exec(pkg.engines?.node ?? "")?.[1] ?? 0);
check(
  `engines.node (>=${declared}) covers vite ${viteRange} (needs >=${needed})`,
  declared >= needed
);
const readmeNode = /Node (\d+)\+/.exec(read("README.md"))?.[1];
check(`README Requirements says Node ${needed}+`, Number(readmeNode) === declared, `README says ${readmeNode}`);

// ---- 5. version pins match the release that ships with this scaffold ------
const reqs = read("template/server/requirements.txt");
const pin = /^aigenos-flow\[server\](.+)$/m.exec(reqs)?.[1]?.trim();
check(
  `requirements.txt pins aigenos-flow >= ${STACK_VERSION}`,
  pin === `>=${STACK_VERSION},<0.4`,
  `found ${JSON.stringify(pin)}`
);
check(
  "requirements.txt pins httpx (fastapi.testclient needs it for the generated tests)",
  /^httpx>=/m.test(reqs)
);
check(
  `package.json version tracks the stack (${STACK_VERSION})`,
  pkg.version === STACK_VERSION,
  pkg.version
);
for (const md of ["README.md", "template/README.md"]) {
  const bad = [...read(md).matchAll(/@aigenos\/flow@([\d.]+)/g)]
    .map((m) => m[1])
    .filter((v) => v !== STACK_VERSION);
  check(`${md} cites @aigenos/flow@${STACK_VERSION}`, bad.length === 0, bad.join(", "));
}

// ---- 6. the printed steps and the generated README agree -----------------
const index = read("index.js");
check("index.js does not invent an `npm run setup` step", !/npm run setup/.test(index));
check("README.md does not invent an `npm run setup` step", !/npm run setup/.test(read("README.md")));
check(
  "README.md tells the user the real first command",
  /npm run dev/.test(read("README.md"))
);

// ---- 7. docker path: FG_MODEL must not be forced onto hosted providers ----
const compose = read("template/docker-compose.yml");
check(
  "docker-compose does not force FG_MODEL onto every provider",
  /FG_MODEL:\s*\$\{FG_MODEL:-\}\s*$/m.test(compose),
  (/FG_MODEL:.*/.exec(compose) || [""])[0].trim()
);
check(
  "docker-compose carries the ollama model in its own var",
  /FG_OLLAMA_MODEL:/.test(compose)
);
check(
  "the ollama profile pulls a model before serving",
  /ollama pull/.test(compose)
);

// ---- report ---------------------------------------------------------------
console.log(checks.join("\n"));
if (problems.length) {
  console.error(`\n✖ ${problems.length} packaging check(s) failed:\n  - ${problems.join("\n  - ")}\n`);
  process.exit(1);
}
console.log(`\n✓ ${checks.length} packaging checks passed (${files.length} files, ${packed.size} bytes)\n`);

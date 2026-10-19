#!/usr/bin/env node
/**
 * Release gate for the @aigenos/flow tarball.  `node src/tools/verify-lib-build.mjs`
 *
 * package.json's `files` allowlist is exactly ["dist/lib", ...docs], so checking
 * dist/lib IS checking the tarball. Every assertion below is a bug that shipped
 * once:
 *
 *   1  dist/lib/index.js + index.d.ts + style.css exist and are non-trivial
 *      (the 0.3.1 pack shipped the demo SPA and none of these)
 *   2  no demo/app artifacts anywhere in the packed tree (.html, images, hashed
 *      assets/ chunks, nested tarballs)
 *   3  exactly one declaration file — rollupTypes, not 67 files leaking Studio
 *      internals that import devDependencies
 *   4  every custom property style.css READS is also DEFINED in style.css
 *      (--on-brand and --accent were not, so primary button text disappeared)
 *   5  the CSS-free Node entries exist and import no stylesheet
 *   6  the documented exports are actually present in the bundle
 *   7  no same-origin API path is baked into the library
 *   8  package.json's main/module/types/exports all resolve to real files
 *
 * Exits non-zero with one line per failure.
 */
import { readFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { join, dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const lib = join(root, "dist", "lib");
const fails = [];
const fail = (m) => fails.push(m);

function walk(dir, out = []) {
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else out.push(p);
  }
  return out;
}

if (!existsSync(lib)) {
  console.error(`FAIL: ${relative(root, lib)} does not exist — run \`npm run build:lib\` first.`);
  process.exit(1);
}

const files = walk(lib).map((p) => relative(lib, p).replace(/\\/g, "/"));
const read = (rel) => readFileSync(join(lib, rel), "utf8");
const sizeOf = (rel) => statSync(join(lib, rel)).size;

// 1 — the library itself
for (const [rel, min] of [["index.js", 20_000], ["index.d.ts", 1_000], ["style.css", 10_000]]) {
  if (!files.includes(rel)) fail(`missing ${rel} — the pack would have no library in it`);
  else if (sizeOf(rel) < min) fail(`${rel} is only ${sizeOf(rel)} bytes (expected > ${min})`);
}

// 2 — nothing from the demo app
const demo = files.filter(
  (f) =>
    f.endsWith(".html") ||
    /\.(png|jpe?g|gif|ico|webp|svg)$/i.test(f) ||
    f.startsWith("assets/") ||
    f.startsWith("shots/") ||
    f.endsWith(".tgz"),
);
if (demo.length) fail(`demo-app artifacts inside the packed tree: ${demo.join(", ")}`);

// 3 — one rolled-up declaration file
const dts = files.filter((f) => f.endsWith(".d.ts"));
if (dts.length !== 1 || dts[0] !== "index.d.ts")
  fail(`expected exactly one declaration file (index.d.ts), got ${dts.length}: ${dts.slice(0, 8).join(", ")}`);

// 4 — the stylesheet is self-contained
if (files.includes("style.css")) {
  const css = read("style.css");
  const used = new Set([...css.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)/g)].map((m) => m[1]));
  const defined = new Set([...css.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)].map((m) => m[1]));
  // `--tw-*` is Tailwind's own ring/shadow plumbing (defaulted at each use
  // site) and `--radix-*` is supplied at runtime by the Radix primitives.
  // Everything else must be defined in this very file — the stylesheet's whole
  // promise is that it is self-contained.
  const vendor = (v) => v.startsWith("--tw-") || v.startsWith("--radix-");
  const undef = [...used].filter((v) => !defined.has(v) && !vendor(v));
  if (undef.length) fail(`style.css reads custom properties it never defines: ${undef.join(", ")}`);
}

// 5 — Node entries carry no CSS
for (const rel of ["index.node.js", "index.cjs"]) {
  if (!files.includes(rel)) {
    fail(`missing ${rel} — the package would be unusable outside a bundler`);
    continue;
  }
  const src = read(rel);
  if (/(from|require\()\s*["'][^"']*\.css["']/.test(src))
    fail(`${rel} imports a stylesheet — plain Node cannot load it`);
}
if (files.includes("index.js") && !/["'][^"']*\.css["']/.test(read("index.js")))
  fail("index.js does not import style.css — bundler consumers would get unstyled widgets");

// 6 — the documented API is in the bundle
if (files.includes("index.js")) {
  const src = read("index.js");
  for (const name of ["Chat", "useFlowChat", "useChatform", "SpecRenderer", "Widget", "PRIMITIVES", "FlowConfigProvider"])
    if (!new RegExp(`\\b${name}\\b`).test(src)) fail(`export "${name}" is missing from index.js`);
}
if (files.includes("index.d.ts")) {
  const d = read("index.d.ts");
  if (!/ChatProps/.test(d)) fail("index.d.ts has no ChatProps — <Chat endpoint=…> would not type-check");
  if (!/endpoint\??:/.test(d)) fail("index.d.ts declares no `endpoint` prop");
  for (const bad of ["reactflow", "@radix-ui/", "monaco"])
    if (d.includes(bad)) fail(`index.d.ts references ${bad}, which consumers do not install`);
}

// 7 — no hardcoded same-origin API paths
for (const rel of ["index.js", "index.node.js", "index.cjs"]) {
  if (!files.includes(rel)) continue;
  const hits = [...new Set([...read(rel).matchAll(/["'](\/api\/[^"']*)["']/g)].map((m) => m[1]))];
  if (hits.length) fail(`${rel} hardcodes same-origin endpoints: ${hits.join(", ")}`);
}

// 8 — package.json points at files that exist
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
const targets = new Set();
const collect = (v) => {
  if (typeof v === "string") targets.add(v);
  else if (v && typeof v === "object") Object.values(v).forEach(collect);
};
collect(pkg.exports);
for (const k of ["main", "module", "types"]) collect(pkg[k]);
for (const t of targets) {
  if (!t.startsWith("./")) continue;
  if (!existsSync(join(root, t))) fail(`package.json points at ${t}, which does not exist`);
}
const allow = (pkg.files ?? []).filter((f) => f.startsWith("dist"));
if (allow.length !== 1 || allow[0] !== "dist/lib")
  fail(`package.json "files" must allowlist exactly "dist/lib" (got ${JSON.stringify(allow)})`);

// Report.
const nPrimitives = (() => {
  const src = readFileSync(join(root, "src/renderer/primitives/index.tsx"), "utf8");
  const m = src.match(/export const REGISTRY[^{]*\{([\s\S]*?)\n\};/);
  return m ? [...m[1].matchAll(/^\s{2}([a-z]+):/gm)].length : 0;
})();

if (fails.length) {
  console.error(`\n@aigenos/flow library build: ${fails.length} problem(s)\n`);
  for (const f of fails) console.error(`  FAIL  ${f}`);
  console.error("");
  process.exit(1);
}
const total = files.reduce((n, f) => n + sizeOf(f), 0);
console.log(
  `@aigenos/flow library build OK — ${files.length} files, ${(total / 1024).toFixed(1)} kB in dist/lib, ` +
    `${nPrimitives} primitives in REGISTRY.`,
);

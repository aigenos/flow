#!/usr/bin/env node
/**
 * Behavioural regression check for the published renderer.
 * `node src/tools/verify-primitives.mjs` (run by `npm run check:lib`)
 *
 * It renders through the BUILT package (dist/lib/index.node.js — the CSS-free
 * Node entry), so it exercises exactly what a consumer installs. Each case is a
 * bug that shipped once:
 *
 *   - `input` dropped min/max/step, so a field declaring `max: 500` rendered
 *     unbounded
 *   - `loading` was the one primitive that ignored props.style
 *   - a `text` node whose generator wrote `text`/`content` rendered as an empty
 *     <p> — including the summary line of a confirmation card
 *   - a `timeline` fed `{items, dateKey}` rendered an empty <ol>
 *   - <Chat> took no props at all and rendered the Studio playground
 */
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const entry = join(root, "dist", "lib", "index.node.js");
if (!existsSync(entry)) {
  console.error("FAIL: dist/lib/index.node.js is missing — run `npm run build:lib` first.");
  process.exit(1);
}
const lib = await import(pathToFileURL(entry).href);

const h = React.createElement;
const render = (spec) => renderToStaticMarkup(h(lib.SpecRenderer, { spec, onSubmit: () => {} }));
const fails = [];
const ok = (cond, msg) => {
  if (!cond) fails.push(msg);
  console.log(`${cond ? "ok  " : "FAIL"}  ${msg}`);
};

// --- the registry is the number the docs quote ---
const primitives = Object.keys(lib.PRIMITIVES);
ok(primitives.length === 16, `REGISTRY has 16 primitives (got ${primitives.length}: ${primitives.join(" ")})`);

// --- input carries the field's declared schema ---
{
  const html = render({
    type: "input",
    props: { name: "amount", type: "number", label: "Amount", required: true, min: 1, max: 500, step: 0.5 },
  });
  ok(/min="1"/.test(html), 'input emits min="1"');
  ok(/max="500"/.test(html), 'input emits max="500"');
  ok(/step="0.5"/.test(html), 'input emits step="0.5"');
  const money = render({ type: "input", props: { name: "amount", type: "number", min: 0.01, step: "any" } });
  ok(/step="any"/.test(money), 'input emits step="any", so a decimal field with min 0.01 accepts 2600');
  const pat = render({ type: "input", props: { name: "code", pattern: "^[0-9]{6}$", maxLength: 6 } });
  ok(/pattern="\^\[0-9\]\{6\}\$"/.test(pat) && /maxlength="6"/i.test(pat), "input emits pattern + maxLength");
  const junk = render({ type: "input", props: { name: "x", type: "number", min: "abc", max: Infinity, step: NaN } });
  ok(!/min=|max=|step=/.test(junk), "non-finite / non-numeric bounds are dropped, never emitted as NaN");
}

// --- every primitive honours the shared style system ---
{
  const style = { pad: 24, bg: "rgb(var(--brand))", size: 22, weight: 700, radius: 12 };
  const missing = primitives.filter(
    (type) =>
      !/ style="/.test(
        render({ type, props: { style, name: "f", options: [], rows: [], columns: [], events: [] } }),
      ),
  );
  ok(missing.length === 0, `all ${primitives.length} primitives emit props.style (missing: ${missing.join(",") || "none"})`);
}

// --- a text node is never silently empty ---
for (const [key, label] of [["value", "canonical"], ["text", "alias"], ["content", "alias"]]) {
  const html = render({ type: "text", props: { [key]: "Send $5000 to mom?" } });
  ok(html.includes("Send $5000 to mom?"), `text renders props.${key} (${label})`);
}

// --- a timeline renders both accepted shapes ---
{
  const canonical = render({ type: "timeline", props: { events: [{ time: "09:00", title: "Standup" }] } });
  ok(canonical.includes("Standup"), "timeline renders props.events");
  const rows = render({
    type: "timeline",
    props: { items: [{ date: "2026-05-25", title: "Standup", note: "daily" }], dateKey: "date" },
  });
  ok(
    rows.includes("2026-05-25") && rows.includes("Standup") && rows.includes("daily"),
    "timeline renders props.items + dateKey",
  );
  ok(!/<ol[^>]*><\/ol>/.test(rows), "timeline is not an empty <ol>");
}

// --- <Chat> is a real drop-in shell, not the Studio playground ---
{
  const html = renderToStaticMarkup(h(lib.Chat, { endpoint: "/chat" }));
  ok(!/playground/i.test(html), "Chat does not render the Studio playground");
  ok(!/workflow/i.test(html), "Chat has no workflow dropdown");
  ok(/<form/.test(html) && /<input/.test(html), "Chat renders a composer");
}

// --- and the renderer makes no request of its own ---
{
  let called = 0;
  const original = globalThis.fetch;
  globalThis.fetch = () => {
    called += 1;
    return Promise.reject(new Error("no network in a render"));
  };
  render({ type: "input", props: { name: "amount", type: "number" } });
  renderToStaticMarkup(h(lib.Chat, { endpoint: "/chat" }));
  globalThis.fetch = original;
  ok(called === 0, `rendering fires no requests (saw ${called})`);
}

console.log("");
if (fails.length) {
  console.error(`@aigenos/flow renderer: ${fails.length} regression(s)`);
  process.exit(1);
}
console.log("@aigenos/flow renderer OK — every documented primitive behaviour holds.");

/**
 * The short form (`steps:`) <-> canvas.
 *
 * A short-form skill is a list of five kinds of step — ask, say, do, confirm,
 * chat — and two kinds of block: `when:` (only if a condition holds) and
 * `repeat:` (once per item of a list). On the canvas it is one column. A block
 * is a header node with its steps indented under it; the steps carry the
 * block's id in `_in`, and Save writes them back as one block.
 *
 * The palette offers more than five things to add (a widget, a tool, a link
 * to another skill, someone else's yes, …). Each is one of the five kinds with
 * an option set, so the file stays small however the canvas is used.
 */
import type { Edge, Node } from "reactflow";
import { Document, Scalar, isMap, isScalar, isSeq } from "yaml";
import type { WfNodeData } from "./nodes";
import { EDGE_STYLE, parseSkillMd, type SkillStep } from "./skillflow";

export type ShortVerb = "ask" | "say" | "do" | "confirm" | "chat";
export const SHORT_VERBS: ShortVerb[] = ["ask", "say", "do", "confirm", "chat"];

/** One tool of the library, as GET /flow/tools describes it. */
export type ToolInfo = {
  name: string;
  description: string;
  changes_something: boolean;
  source: string;
  takes_everything?: boolean;
  used_by?: string[];
  inputs: Array<{
    name: string; type: string; required: boolean; description: string;
    choices?: string[]; secret?: boolean;
  }>;
};

/** What the loader made of one question (from POST /flow/skills/check). */
export type FieldInfo = {
  type: string; values: string[] | null; question: string | null;
  secret: boolean; optional: boolean; user_only: boolean; confirmation: boolean;
};

export type CheckResult =
  | { ok: true; name: string; fields: Record<string, FieldInfo>; tools: string[]; writes: string[]; links: string[] }
  | { ok: false; error: string; step: string | null };

// ------------------------------------------------------------------ blocks
/** A block header: `when:` (a condition) or `repeat:` (once per item). */
export type Block = { id: string; when?: string; repeat?: Record<string, string> };

export const isHeader = (step: SkillStep | undefined): boolean =>
  Boolean(step && typeof step === "object" && step._block && typeof step._block === "object");

export const blockOf = (step: SkillStep | undefined): Block | undefined =>
  (isHeader(step) ? (step!._block as Block) : undefined);

/** `[name, question]` of a repeat block. */
export function repeatOf(block: Block | undefined): [string, string] | undefined {
  if (!block?.repeat) return undefined;
  const [entry] = Object.entries(block.repeat);
  return entry ? [entry[0], String(entry[1] ?? "")] : undefined;
}

let blockSeq = 0;
export const newBlockId = () => `b${Date.now().toString(36)}${(blockSeq++).toString(36)}`;

export function shortVerbOf(step: SkillStep): ShortVerb {
  for (const v of SHORT_VERBS) if (v in step) return v;
  return "say";
}

/** The questions an `ask:` step asks: `[name, question]` pairs, in order. */
export function asksOf(step: SkillStep): Array<[string, string]> {
  const raw = step.ask;
  if (typeof raw === "string") return [[raw, ""]];
  if (raw && typeof raw === "object") {
    return Object.entries(raw).map(([name, q]) => [name, q == null ? "" : String(q)]);
  }
  return [];
}

/** The rules on an answer: `[condition, sentence]` pairs, in order. */
export function rulesOf(step: SkillStep): Array<[string, string]> {
  const raw = step.check;
  if (Array.isArray(raw)) {
    return raw.flatMap((item) =>
      item && typeof item === "object" ? Object.entries(item).map(([c, m]) => [c, String(m ?? "")] as [string, string]) : [],
    );
  }
  if (raw && typeof raw === "object") return Object.entries(raw).map(([c, m]) => [c, String(m ?? "")]);
  return [];
}

/** The lookups a `chat:` step may call. */
export function toolsOf(step: SkillStep): string[] {
  const raw = step.tools;
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === "string") return raw.split(/[\s,]+/).filter(Boolean);
  return [];
}

const OPTIONAL_RE = /\(\s*optional\s*\)\s*[.?!]?\s*$/i;
export const isOptionalQuestion = (question: string) => OPTIONAL_RE.test(question);
export const withoutOptional = (question: string) => question.replace(OPTIONAL_RE, "").trimEnd();

/** `do: add-payee` names another skill; `do: send_payment` names a tool. */
export function isSkillTarget(target: unknown, skills: Iterable<string>): boolean {
  const name = String(target ?? "");
  return new Set(skills).has(name) || (name.includes("-") && /^[a-z0-9]+(-[a-z0-9]+)*$/.test(name));
}

// ------------------------------------------------------------------ file -> canvas
const bothOf = (guard: string | undefined, step: SkillStep) => {
  const own = step.when != null ? String(step.when) : undefined;
  return guard == null ? own : own == null ? guard : `(${guard}) and (${own})`;
};

/** A block's `when:` goes on each of its steps (blocks below the top level). */
function mergeInto(steps: unknown, guard?: string): SkillStep[] {
  const out: SkillStep[] = [];
  for (const step of Array.isArray(steps) ? steps : []) {
    if (!step || typeof step !== "object") continue;
    const both = bothOf(guard, step as SkillStep);
    if (Array.isArray((step as SkillStep).steps)) {
      out.push(...mergeInto((step as SkillStep).steps, both));
      continue;
    }
    const { when: _when, ...rest } = step as SkillStep;
    out.push(both ? { ...rest, when: both } : rest);
  }
  return out;
}

const isBlockStep = (step: unknown): step is SkillStep =>
  !!step && typeof step === "object" && Array.isArray((step as SkillStep).steps);

/** Does a block hold a `repeat:` anywhere inside it? */
function holdsRepeat(steps: unknown): boolean {
  return (Array.isArray(steps) ? steps : []).some(
    (s) => isBlockStep(s) && (s.repeat != null || holdsRepeat(s.steps)),
  );
}

/** `when:` blocks written out as top-level steps carrying their condition; a repeat stays a block. */
function hoist(steps: unknown, guard?: string): SkillStep[] {
  const out: SkillStep[] = [];
  for (const step of Array.isArray(steps) ? steps : []) {
    if (!step || typeof step !== "object") continue;
    const both = bothOf(guard, step as SkillStep);
    if (isBlockStep(step) && step.repeat == null) {
      out.push(...hoist(step.steps, both));
      continue;
    }
    const { when: _when, ...rest } = step as SkillStep;
    out.push(both ? { ...rest, when: both } : rest);
  }
  return out;
}

/**
 * The file's steps as one column. A block at the top level becomes a header
 * followed by its steps, each marked with the header's id; anything nested
 * deeper keeps its condition as the step's own `when:`. A column holds one
 * level of blocks, so a `when:` block that holds a `repeat:` is written out as
 * steps carrying its condition, with the repeat a block of its own: the loader
 * reads both the same way, and the repeat is kept.
 */
export function flattenSteps(steps: unknown): SkillStep[] {
  const out: SkillStep[] = [];
  let n = 0;
  const top = (Array.isArray(steps) ? steps : []).flatMap((step) =>
    isBlockStep(step) && step.repeat == null && holdsRepeat(step.steps) ? hoist([step]) : [step],
  );
  for (const step of top) {
    if (!step || typeof step !== "object") continue;
    const s = step as SkillStep;
    if (Array.isArray(s.steps)) {
      const id = `b${++n}`;
      const block: Block = { id };
      if (s.repeat && typeof s.repeat === "object") block.repeat = { ...s.repeat };
      if (s.when != null) block.when = String(s.when);
      out.push({ _block: block });
      for (const inner of mergeInto(s.steps)) out.push({ ...inner, _in: id });
      continue;
    }
    out.push({ ...s });
  }
  return out;
}

const KEY_ORDER: Record<ShortVerb, string[]> = {
  ask: ["ask", "choices", "from", "type", "pattern", "widget", "with", "check", "when"],
  say: ["say", "title", "widget", "with", "when"],
  do: ["do", "with", "as", "else", "when"],
  confirm: ["confirm", "by", "widget", "with", "when"],
  chat: ["chat", "tools", "when"],
};

/** A step as written to the file: its keys in reading order, the canvas's own marks dropped. */
function ordered(step: SkillStep): SkillStep {
  const order = KEY_ORDER[shortVerbOf(step)];
  const out: SkillStep = {};
  for (const key of order) if (step[key] !== undefined) out[key] = step[key];
  for (const key of Object.keys(step)) {
    if (key.startsWith("_") || key in out || step[key] === undefined) continue;
    out[key] = step[key];
  }
  return out;
}

// ------------------------------------------------------------------ canvas -> file
/**
 * The canvas column as the file's steps, and where each node went: `paths`
 * maps the loader's step numbers ("3", or "3.2" inside a block) to the
 * column's index, so an objection lands on the node it is about.
 */
export function groupSteps(flat: SkillStep[]): { steps: SkillStep[]; paths: Map<string, number> } {
  const steps: SkillStep[] = [];
  const paths = new Map<string, number>();
  let i = 0;
  while (i < flat.length) {
    const step = flat[i];
    const block = blockOf(step);
    if (block) {
      const inner: SkillStep[] = [];
      const top = steps.length + 1;
      paths.set(String(top), i);
      let j = i + 1;
      while (j < flat.length && flat[j]._in === block.id && !isHeader(flat[j])) {
        inner.push(ordered(flat[j]));
        paths.set(`${top}.${inner.length}`, j);
        j++;
      }
      const written: SkillStep = {};
      if (block.repeat) written.repeat = block.repeat;
      if (block.when) written.when = block.when;
      written.steps = inner;
      steps.push(written);
      i = j;
      continue;
    }
    paths.set(String(steps.length + 1), i);
    steps.push(ordered(step));
    i++;
  }
  return { steps, paths };
}

/** The canvas index of the loader's "step 3.2" (the column's own numbering differs inside blocks). */
export function indexOfPath(flat: SkillStep[], path: string): number | undefined {
  const { paths } = groupSteps(flat);
  const parts = path.split(".");
  for (let k = parts.length; k >= 1; k--) {
    const found = paths.get(parts.slice(0, k).join("."));
    if (found !== undefined) return found;
  }
  return undefined;
}

/** Frontmatter as YAML, with steps written the way people write them by hand. */
function frontmatterText(fm: Record<string, unknown>): string {
  const doc = new Document(fm);
  const quote = (node: unknown) => {
    if (isScalar(node) && typeof node.value === "string") node.type = Scalar.QUOTE_DOUBLE;
  };
  const style = (seq: unknown) => {
    if (!isSeq(seq)) return;
    for (const item of seq.items) {
      if (!isMap(item)) continue;
      for (const pair of item.items) {
        const key = isScalar(pair.key) ? String(pair.key.value) : "";
        const value = pair.value;
        if ((key === "ask" || key === "check" || key === "with" || key === "repeat") && isMap(value)) {
          value.flow = true;
          for (const inner of value.items) quote(inner.value);
        } else if ((key === "choices" || key === "tools") && isSeq(value)) {
          value.flow = true;
        } else if (["say", "confirm", "else", "title", "chat"].includes(key)) {
          quote(value);
        } else if (key === "steps") {
          style(value);
        }
      }
    }
  };
  style(doc.get("steps", true));
  return doc.toString({ lineWidth: 0 }).trimEnd();
}

/**
 * Write the edited steps back into the SKILL.md text. Blocks are written as
 * blocks whether this is for Save or for the live check: a `repeat:` means
 * something only as a block. (`grouped` is kept for callers; both are grouped.)
 */
export function replaceSteps(md: string, flat: SkillStep[], _grouped = true): string {
  const { fm, body } = parseSkillMd(md);
  delete fm.flow;
  fm.steps = groupSteps(flat).steps;
  return `---\n${frontmatterText(fm)}\n---\n\n${body.trimStart()}`;
}

/** The `steps:` block alone, for the Source tab. */
export function stepsYaml(flat: SkillStep[]): string {
  return frontmatterText({ steps: groupSteps(flat).steps });
}

/** A step compared for "what changed": block ids are the canvas's own, not the file's. */
export function sameStepKey(step: SkillStep): string {
  const { _in, _block, _kind, ...rest } = step;
  const block = _block && typeof _block === "object" ? { ..._block, id: undefined } : undefined;
  return JSON.stringify({ ...rest, ...(block ? { _block: block } : {}), ...(_in ? { _in: true } : {}) });
}

// ------------------------------------------------------------------ display
export type StepContext = { tools: Map<string, ToolInfo>; skills: Set<string> };

const label = (name: string) => name.replace(/_/g, " ");

/** What a node shows for a step: its line, a quieter second line, its badges. */
export function describeStep(step: SkillStep, ctx: StepContext): {
  nodeType: WfNodeData["nodeType"]; label: string; preview?: string; badges: string[]; kindLabel?: string;
} {
  const block = blockOf(step);
  if (block) {
    const repeat = repeatOf(block);
    if (repeat) {
      return {
        nodeType: "repeat", kindLabel: "repeat",
        label: `Collect ${label(repeat[0])}, one at a time`,
        preview: `after each one, asks “${repeat[1]}”`,
        badges: block.when ? [`only when ${block.when}`, "collects a list"] : ["collects a list"],
      };
    }
    return {
      nodeType: "condition", kindLabel: "condition",
      label: block.when ? `Only when ${block.when}` : "Only when … (write the condition)",
      preview: "The steps under it run only if this holds.",
      badges: [],
    };
  }

  const verb = shortVerbOf(step);
  const badges: string[] = [];
  if (step.when) badges.push(`only when ${step.when}`);

  if (verb === "ask") {
    const asks = asksOf(step);
    const [first] = asks;
    const names = asks.map(([n]) => n).join(", ");
    if (step.from) badges.push(`list from ${String(step.from).split(".")[0]}`);
    else if (Array.isArray(step.choices)) badges.push(`one of ${step.choices.length}`);
    else if (step.type) badges.push(String(step.type));
    if (step.pattern) badges.push("has a shape");
    if (asks.some(([, q]) => isOptionalQuestion(q))) badges.push("may be blank");
    const rules = rulesOf(step).length;
    if (rules) badges.push(rules === 1 ? "1 rule" : `${rules} rules`);
    if (step.widget) badges.push(`widget: ${step.widget}`);
    return {
      nodeType: step.widget ? "widget" : "ask",
      kindLabel: step.widget ? "ask · widget" : undefined,
      label: asks.length === 1 ? first?.[1] || `What is the ${label(first?.[0] ?? "answer")}?` : `${asks.length} questions`,
      preview: `→ ${names}`,
      badges,
    };
  }
  if (verb === "confirm") {
    const raw = step.confirm;
    const question = raw && typeof raw === "object" ? String(Object.values(raw)[0] ?? "") : String(raw ?? "");
    if (step.widget) badges.push(`page: ${step.widget}`);
    if (typeof step.by === "string") {
      badges.push(`waits for ${step.by}`, "no stops the flow");
      return { nodeType: "approval", kindLabel: "approval", label: question, preview: `a yes from ${step.by}, not the user`, badges };
    }
    badges.push("no stops the flow");
    return { nodeType: "confirm", label: question, badges };
  }
  if (verb === "do") {
    const target = String(step.do ?? "");
    const tool = ctx.tools.get(target);
    const asLink = (!tool && isSkillTarget(target, ctx.skills)) || (!target && step._kind === "link");
    if (asLink) {
      badges.push("another skill", "stops here if not done");
      return {
        nodeType: "link", kindLabel: "link", label: target || "Choose a skill",
        preview: "runs that skill, then carries on here", badges,
      };
    }
    if (tool) badges.push(tool.changes_something ? "changes something" : "looks something up");
    return { nodeType: "tool", kindLabel: "tool", label: target || "Choose a tool", preview: tool?.description || undefined, badges };
  }
  if (verb === "chat") {
    const listed = toolsOf(step);
    badges.push(listed.length === 1 ? "1 lookup" : `${listed.length} lookups`, "last step");
    return { nodeType: "chat", label: String(step.chat ?? ""), preview: listed.join(", ") || "no tools chosen yet", badges };
  }
  const text = String(step.say ?? "");
  if ("widget" in step) {
    return {
      nodeType: "widget", kindLabel: "widget", label: step.widget ? String(step.widget) : "Choose a widget",
      preview: text.slice(0, 90), badges,
    };
  }
  if (step.title) badges.push(`card: ${step.title}`);
  return { nodeType: "say", label: text.slice(0, 90), badges };
}

/** How far a step inside a block sits to the right of the column. */
export const INDENT = 44;
export const COLUMN_X = 140;

export function stepsToCanvas(flat: SkillStep[], ctx: StepContext): { nodes: Node<WfNodeData>[]; edges: Edge[] } {
  const nodes: Node<WfNodeData>[] = flat.map((step, i) => ({
    id: `s${i}`,
    type: "wf",
    position: { x: COLUMN_X + (step._in ? INDENT : 0), y: 30 + i * 172 },
    data: { ...describeStep(step, ctx), step, index: i + 1 },
  }));
  const edges: Edge[] = [];
  for (let i = 0; i + 1 < nodes.length; i++) {
    edges.push({ id: `e${i}`, source: nodes[i].id, target: nodes[i + 1].id, ...EDGE_STYLE });
  }
  return { nodes, edges };
}

// ------------------------------------------------------------------ the palette
/** What can be added from the canvas's left rail. Each is one of the five kinds, or a block. */
export type PaletteKind =
  | "ask" | "say" | "widget" | "tool" | "link" | "confirm" | "approval" | "condition" | "repeat" | "chat";

/** The palette entry a step was made from (its look on the canvas, and its inspector). */
export function paletteKindOf(step: SkillStep, ctx?: StepContext): PaletteKind {
  const block = blockOf(step);
  if (block) return block.repeat ? "repeat" : "condition";
  const verb = shortVerbOf(step);
  if (verb === "say") return "widget" in step ? "widget" : "say";
  if (verb === "confirm") return typeof step.by === "string" ? "approval" : "confirm";
  if (verb === "do") {
    const target = String(step.do ?? "");
    if (!target) return step._kind === "link" ? "link" : "tool";
    if (ctx?.tools.has(target)) return "tool";
    return isSkillTarget(target, ctx?.skills ?? []) ? "link" : "tool";
  }
  if (verb === "chat") return "chat";
  return "ask";
}

/** A new step for one palette entry. `taken` keeps a new answer's name from clashing. */
export function newStep(kind: PaletteKind | ShortVerb, taken: Set<string>): SkillStep {
  const fresh = (base: string) => {
    let name = base;
    for (let n = 2; taken.has(name); n++) name = `${base}_${n}`;
    return name;
  };
  switch (kind) {
    case "ask": return { ask: { [fresh("answer")]: "What would you like to tell us?" } };
    case "say": return { say: "…" };
    case "widget": return { say: "Here it is.", widget: "" };
    case "link": return { do: "", _kind: "link" };
    case "confirm": return { confirm: "Shall I go ahead?" };
    case "approval": return { confirm: "Approve this?", by: "manager" };
    case "chat": return { chat: "Ask me anything about it.", tools: [] };
    case "tool":
    case "do":
    default: return { do: "" };
  }
}

/** A new block: its header, and the first step inside it. */
export function newBlock(kind: "condition" | "repeat", taken: Set<string>): SkillStep[] {
  const id = newBlockId();
  if (kind === "repeat") {
    let list = "items";
    for (let n = 2; taken.has(list); n++) list = `items_${n}`;
    let item = "item";
    for (let n = 2; taken.has(item); n++) item = `item_${n}`;
    return [
      { _block: { id, repeat: { [list]: "Add another?" } } },
      { ask: { [item]: "What is the item?" }, _in: id },
    ];
  }
  return [
    { _block: { id, when: "" } },
    { say: "…", _in: id },
  ];
}

/** The answers asked by the steps before `index` (1-based), in order. */
export function namesAbove(flat: SkillStep[], index: number): string[] {
  const out: string[] = [];
  for (const step of flat.slice(0, Math.max(0, index - 1))) {
    if (isHeader(step)) {
      const repeat = repeatOf(blockOf(step));
      if (repeat && !out.includes(repeat[0])) out.push(repeat[0]);
      continue;
    }
    if (shortVerbOf(step) === "ask") for (const [name] of asksOf(step)) if (name && !out.includes(name)) out.push(name);
  }
  return out;
}

export const confirmAbove = (flat: SkillStep[], index: number) =>
  flat.slice(0, Math.max(0, index - 1)).some((s) => !isHeader(s) && shortVerbOf(s) === "confirm");

/** A starter SKILL.md for a skill begun on the canvas. */
export function starterSkill(name: string, description: string): string {
  const fm = {
    name,
    description,
    steps: [{ say: "Hello. This skill has no steps yet." }],
  };
  return `---\n${frontmatterText(fm)}\n---\n\n${description}\n`;
}

/**
 * SKILL.md flow <-> canvas round-trip.
 *
 * A declarative flow is a LINEAR list of verb steps, so the canvas is a
 * vertical chain: node order = step order (edges define the sequence).
 * Guards (`when:` / `when_missing:`) render as badges, not branches — the
 * runtime evaluates them on every replay.
 */
import { MarkerType, type Edge, type Node } from "reactflow";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import type { WfNodeData } from "./nodes";

export type SkillStep = Record<string, any>;

const VERBS = ["ask", "say", "do", "run", "pick", "show", "check", "chat", "widget", "link", "code"] as const;
export type Verb = "ask" | "say" | "do" | "pick" | "show" | "check" | "chat" | "widget" | "link" | "code";

export function verbOf(step: SkillStep): Verb {
  for (const v of VERBS) {
    if (v in step) return v === "run" ? "do" : (v as Verb);
  }
  return "say";
}

/** One-line node label per verb. */
export function summaryOf(step: SkillStep): string {
  const v = verbOf(step);
  switch (v) {
    case "ask": return String(step.ask);
    case "say": return String(step.say ?? "").slice(0, 60);
    case "do": return String(step.do ?? step.run);
    case "pick": {
      const via = step.widget ? ` via ${step.widget}` : step.as ? ` as ${step.as}` : "";
      return `${step.pick}${via} → ${(step.save ?? []).join(", ")}`;
    }
    case "show": return `${step.show}${step.title ? ` — ${step.title}` : ""}`;
    case "check": return String(step.check);
    case "chat": return `tools: ${(step.chat?.tools ?? []).join(", ")}`;
    case "widget": return `${step.widget} → ${(step.save ?? []).join(", ")}`;
    case "link": return `→ ${step.link}, then back here`;
    case "code": return String(step.code ?? "");
  }
}

function badgesOf(step: SkillStep): string[] {
  const out: string[] = [];
  if (step.when_missing) {
    const wm = Array.isArray(step.when_missing) ? step.when_missing.join(",") : step.when_missing;
    out.push(`while no ${wm}`);
  }
  if (step.when) out.push(`when ${step.when}`);
  if (verbOf(step) === "check" && step.else_clear) out.push(`re-ask ${step.else_clear.join(",")}`);
  // A hand-off that did nothing ends the flow: say so on the node, where a
  // reader would otherwise assume the next step always runs.
  if (verbOf(step) === "link") out.push("stops here if not done");
  return out;
}

export function defaultStep(verb: Verb): SkillStep {
  switch (verb) {
    case "ask": return { ask: "field_name" };
    case "say": return { say: "…" };
    case "do": return { do: "action_name" };
    case "pick": return { pick: "{{action.rows}}", save: ["field_name"] };
    case "show": return { show: "{{action}}" };
    case "check": return { check: "action.ok", else_say: "That didn't check out — try again.", else_clear: ["field_name"] };
    case "chat": return { chat: { tools: [], pin: {}, greeting: "Ask me anything." } };
    case "widget": return { widget: "", save: ["field_name"] };
    case "link": return { link: "other-skill" };
    case "code": return { code: "" };
  }
}

// ---------------------------------------------------------------- to canvas
/** Shared edge look: animated brand stroke with a closed arrowhead. */
export const EDGE_STYLE = {
  type: "smoothstep",
  animated: true,
  style: { stroke: "rgb(var(--brand) / 0.55)", strokeWidth: 1.5 },
  markerEnd: { type: MarkerType.ArrowClosed, color: "rgb(var(--brand) / 0.7)", width: 16, height: 16 },
} as const;

export function stepsToFlow(steps: SkillStep[]): { nodes: Node<WfNodeData>[]; edges: Edge[] } {
  const nodes: Node<WfNodeData>[] = steps.map((step, i) => ({
    id: `s${i}`,
    type: "wf",
    position: { x: 140, y: 30 + i * 150 },
    data: {
      label: summaryOf(step),
      nodeType: verbOf(step),
      step,
      badges: badgesOf(step),
      index: i + 1,
    },
  }));
  const edges: Edge[] = [];
  for (let i = 0; i + 1 < nodes.length; i++) {
    edges.push({ id: `e${i}`, source: nodes[i].id, target: nodes[i + 1].id, ...EDGE_STYLE });
  }
  return { nodes, edges };
}

// -------------------------------------------------------------- from canvas
/** Node order: follow the edge chain from the head; stragglers by y. */
export function flowToSteps(nodes: Node<WfNodeData>[], edges: Edge[]): SkillStep[] {
  if (nodes.length === 0) return [];
  const hasIncoming = new Set(edges.map((e) => e.target));
  const bySource = new Map(edges.map((e) => [e.source, e.target]));
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const heads = nodes.filter((n) => !hasIncoming.has(n.id)).sort((a, b) => a.position.y - b.position.y);
  const ordered: Node<WfNodeData>[] = [];
  const seen = new Set<string>();
  for (const head of heads) {
    let cur: Node<WfNodeData> | undefined = head;
    while (cur && !seen.has(cur.id)) {
      ordered.push(cur);
      seen.add(cur.id);
      const nextId = bySource.get(cur.id);
      cur = nextId ? byId.get(nextId) : undefined;
    }
  }
  for (const n of [...nodes].sort((a, b) => a.position.y - b.position.y)) {
    if (!seen.has(n.id)) ordered.push(n);
  }
  return ordered.map((n) => n.data.step ?? defaultStep(n.data.nodeType as Verb));
}

// ------------------------------------------------------------------- SKILL.md
const FM_RE = /^---\s*\n([\s\S]*?)\n---\s*\n?([\s\S]*)$/;

export function parseSkillMd(md: string): { fm: any; body: string } {
  const m = FM_RE.exec(md);
  if (!m) throw new Error("SKILL.md has no YAML frontmatter");
  return { fm: parseYaml(m[1]) ?? {}, body: m[2] ?? "" };
}

export function flowYaml(steps: SkillStep[]): string {
  return stringifyYaml({ flow: steps }, { lineWidth: 100 });
}

/**
 * Write the edited flow back into the SKILL.md text. The frontmatter is
 * re-serialized (comments inside it are not preserved); the markdown body is
 * untouched. Server-side load validation still gates the save.
 */
export function replaceFlow(md: string, steps: SkillStep[]): string {
  const { fm, body } = parseSkillMd(md);
  fm.flow = steps;
  const fmText = stringifyYaml(fm, { lineWidth: 100 }).trimEnd();
  return `---\n${fmText}\n---\n\n${body.trimStart()}`;
}

export function replaceFrontmatter(md: string, fm: any): string {
  const { body } = parseSkillMd(md);
  const fmText = stringifyYaml(fm, { lineWidth: 100 }).trimEnd();
  return `---\n${fmText}\n---\n\n${body.trimStart()}`;
}

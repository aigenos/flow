/**
 * Canvas preflight — the checks worth catching before a save round-trip.
 *
 * The backend's fail-closed loader is still the authority: it rejects anything
 * it can't prove safe. This runs on every keystroke so the common mistakes
 * (a placeholder left in, a widget step with no widget, a node never wired
 * into the chain) surface on the node itself instead of as a server error
 * after the fact.
 */
import type { Edge, Node } from "reactflow";
import type { WfNodeData } from "./nodes";
import { defaultStep, verbOf, type SkillStep, type Verb } from "./skillflow";

export type IssueLevel = "error" | "warn";
export type Issue = { nodeId: string; level: IssueLevel; message: string };

/** Field and action names have to be plain identifiers to be bindable. */
const IDENT = /^[A-Za-z_]\w*$/;

/** The `defaultStep` values — meaningful only as a prompt to fill them in. */
const PLACEHOLDERS = new Set(["field_name", "action_name", "…", ""]);

function isBlank(v: unknown): boolean {
  return v == null || (typeof v === "string" && v.trim() === "");
}

function stepIssues(step: SkillStep, catalog: string[]): Array<{ level: IssueLevel; message: string }> {
  const out: Array<{ level: IssueLevel; message: string }> = [];
  const err = (message: string) => out.push({ level: "error", message });
  const warn = (message: string) => out.push({ level: "warn", message });

  const widgetKnown = (id: unknown) =>
    typeof id === "string" && (catalog.length === 0 || catalog.includes(id));

  switch (verbOf(step)) {
    case "ask": {
      if (isBlank(step.ask)) err("Ask needs a field name to collect.");
      else if (!IDENT.test(String(step.ask)))
        err(`“${step.ask}” isn't a valid field name — letters, digits and underscores only.`);
      else if (PLACEHOLDERS.has(String(step.ask)))
        warn("Still the placeholder field name.");
      break;
    }
    case "say":
      if (isBlank(step.say)) err("Say needs the message to send.");
      else if (PLACEHOLDERS.has(String(step.say).trim())) warn("Still the placeholder message.");
      break;
    case "do": {
      const action = step.do ?? step.run;
      if (isBlank(action)) err("Do needs the name of an action to run.");
      else if (!IDENT.test(String(action)))
        err(`“${action}” isn't a valid action name — letters, digits and underscores only.`);
      else if (PLACEHOLDERS.has(String(action))) warn("Still the placeholder action name.");
      break;
    }
    case "show":
      if (isBlank(step.show)) err("Show needs a value binding, e.g. {{action.result}}.");
      break;
    case "pick": {
      if (isBlank(step.pick)) err("Pick needs a rows binding, e.g. {{action.rows}}.");
      if (step.widget != null && isBlank(step.widget))
        err("Pick is set to render a widget but no widget is chosen.");
      else if (!isBlank(step.widget) && !widgetKnown(step.widget))
        warn(`No widget named “${step.widget}” in the catalog.`);
      if (!(step.save?.length)) warn("Nothing is saved from the selected row.");
      break;
    }
    case "check":
      if (isBlank(step.check)) err("Check needs a condition to gate on.");
      if (isBlank(step.else_say)) warn("No failure message — the user won't learn why the gate closed.");
      break;
    case "link":
      if (isBlank(step.link)) err("Link needs the name of the skill to hand off to.");
      else if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(String(step.link)))
        err(`“${step.link}” isn't a skill name — lower-case letters, digits and single hyphens.`);
      else if (PLACEHOLDERS.has(String(step.link))) warn("Still the placeholder skill name.");
      if (isBlank(step.else_say))
        warn("No message for when that skill is cancelled or declined — this flow will stop without a word of its own.");
      break;
    case "chat":
      if (!(step.chat?.tools?.length))
        warn("No allow-listed tools — this chat step can only talk, not look anything up.");
      break;
    case "widget": {
      if (isBlank(step.widget)) err("Widget step needs a widget from the catalog.");
      else if (!widgetKnown(step.widget)) warn(`No widget named “${step.widget}” in the catalog.`);
      if (!(step.save?.length)) warn("Nothing is saved from the widget's submission.");
      break;
    }
  }

  // Guards are cheap to typo and fail silently by skipping the step forever.
  // `when_missing:` names fields, so it can be checked here. `when:` is an
  // expression ("amount >= 1000", "lookup_order.needs_approval") — only the
  // loader can judge it, and it does, on save.
  const missing = step.when_missing;
  const names = Array.isArray(missing) ? missing : isBlank(missing) ? [] : [missing];
  for (const n of names) {
    if (!IDENT.test(String(n))) err(`when_missing: “${n}” isn't a valid field name.`);
  }

  return out;
}

/**
 * Every issue on the board, in canvas order. Structural problems (a node that
 * was never wired in) come from the edges; the rest come from each step.
 */
export function validateFlow(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  catalog: string[] = [],
): Issue[] {
  const issues: Issue[] = [];

  const hasIncoming = new Set(edges.map((e) => e.target));
  const heads = nodes
    .filter((n) => !hasIncoming.has(n.id))
    .sort((a, b) => a.position.y - b.position.y);

  for (const node of nodes) {
    const step = node.data.step ?? defaultStep(node.data.nodeType as Verb);
    for (const issue of stepIssues(step, catalog)) {
      issues.push({ nodeId: node.id, ...issue });
    }
  }

  // A second head is a step nobody chained — it still serializes, but it runs
  // after everything connected, which is almost never what was meant.
  for (const stray of heads.slice(1)) {
    issues.push({
      nodeId: stray.id,
      level: "warn",
      message: "Not wired into the chain — it runs after the connected steps.",
    });
  }

  return issues;
}

/** Worst level per node, for the badge on the node itself. */
export function worstByNode(issues: Issue[]): Map<string, IssueLevel> {
  const out = new Map<string, IssueLevel>();
  for (const { nodeId, level } of issues) {
    if (level === "error" || !out.has(nodeId)) out.set(nodeId, level);
  }
  return out;
}

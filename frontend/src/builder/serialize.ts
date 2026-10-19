/** Round-trip between a workflow object and React Flow nodes/edges. */
import type { Node, Edge } from "reactflow";
import type { WfNodeData } from "./nodes";

type ApiWorkflow = {
  id: string;
  description?: string;
  start: string;
  nodes: ApiWfNode[];
};

type ApiWfNode = {
  id: string;
  type: "message" | "widget" | "tool_call" | "branch" | "end";
  widget?: string;
  props?: Record<string, unknown>;
  save_to?: string;
  text?: string;
  tool?: string;
  args?: Record<string, unknown>;
  branches?: Array<{ if?: string; next?: string }>;
  next?: string;
};

export function toFlow(wf: ApiWorkflow): { nodes: Node<WfNodeData>[]; edges: Edge[] } {
  const nodes: Node<WfNodeData>[] = wf.nodes.map((n, i) => ({
    id: n.id,
    type: "wf",
    position: { x: 240 * (i % 3), y: 140 * Math.floor(i / 3) },
    data: {
      label: n.id,
      nodeType: n.type,
      widget: n.widget,
      tool: n.tool,
      preview: n.text ?? undefined,
      isStart: n.id === wf.start,
    },
  }));

  const edges: Edge[] = [];
  for (const n of wf.nodes) {
    if (n.next) edges.push(mkEdge(n.id, n.next));
    if (n.branches) {
      for (const b of n.branches) {
        if (b.next) edges.push(mkEdge(n.id, b.next, b.if));
      }
    }
  }
  return { nodes, edges };
}

function mkEdge(source: string, target: string, label?: string): Edge {
  return {
    id: `${source}->${target}-${label ?? ""}`,
    source,
    target,
    label,
    type: "smoothstep",
    animated: false,
    style: { stroke: "rgba(167,139,250,0.5)" },
    labelStyle: { fill: "#a1a1aa", fontSize: 11 },
    labelBgStyle: { fill: "#16161a" },
    labelBgPadding: [4, 2],
    labelBgBorderRadius: 4,
  };
}

export function fromFlow(
  meta: { id: string; description: string; start: string },
  flowNodes: Node<WfNodeData>[],
  flowEdges: Edge[],
  nodeProps: Record<string, Partial<ApiWfNode>>,
): ApiWorkflow {
  const outNodes: ApiWfNode[] = flowNodes.map((n) => {
    const overrides = nodeProps[n.id] ?? {};
    const outgoing = flowEdges.filter((e) => e.source === n.id);
    const base: ApiWfNode = {
      id: n.id,
      // The legacy engine canvas only ever creates engine node types; the
      // wider WfNodeData union (SKILL.md verbs) belongs to the skill-flow
      // canvas, which uses skillflow.ts instead of this serializer.
      type: (n.data.nodeType === "start" ? "message" : n.data.nodeType) as ApiWfNode["type"],
      widget: n.data.widget,
      tool: n.data.tool,
      text: n.data.preview,
      ...overrides,
    };
    if (base.type === "branch") {
      base.branches = outgoing.map((e) => ({ if: typeof e.label === "string" ? e.label : undefined, next: e.target }));
    } else if (base.type !== "end") {
      const first = outgoing[0];
      if (first) base.next = first.target;
    }
    return base;
  });

  return {
    id: meta.id,
    description: meta.description,
    start: meta.start || flowNodes[0]?.id,
    nodes: outNodes,
  };
}

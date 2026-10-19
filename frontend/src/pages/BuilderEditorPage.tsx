import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import ReactFlow, {
  Background, Controls, MiniMap,
  addEdge, useEdgesState, useNodesState,
  type Connection, type Edge, type Node, type EdgeChange, type NodeChange,
} from "reactflow";
import "reactflow/dist/style.css";
import { ArrowLeft, Save, Trash2, MousePointer2 } from "lucide-react";
import { api } from "../lib/api";
import { NODE_TYPES, type WfNodeData } from "../builder/nodes";
import { fromFlow, toFlow } from "../builder/serialize";
import { NodeInspector } from "../builder/NodeInspector";
import { Toolbar } from "../builder/Toolbar";

const BLANK = {
  id: "",
  description: "",
  start: "start",
  nodes: [
    { id: "start", type: "message", text: "Hello!", next: "end" },
    { id: "end", type: "end" },
  ],
};

let idCounter = 0;
function nextNodeId(prefix: string): string {
  idCounter += 1;
  return `${prefix}_${idCounter}`;
}

export function BuilderEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isNew = !id;

  const [meta, setMeta] = useState({ id: "", description: "", start: "" });
  const [nodes, setNodes, onNodesChange] = useNodesState<WfNodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [overrides, setOverrides] = useState<Record<string, any>>({});
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (isNew) {
      setMeta({ id: "", description: "", start: "start" });
      const fl = toFlow(BLANK as any);
      setNodes(fl.nodes);
      setEdges(fl.edges);
      seedOverrides(BLANK.nodes, setOverrides);
      return;
    }
    api.getWorkflow(id!).then((wf) => {
      setMeta({ id: wf.id, description: wf.description ?? "", start: wf.start });
      const fl = toFlow(wf);
      setNodes(fl.nodes);
      setEdges(fl.edges);
      seedOverrides(wf.nodes, setOverrides);
    });
  }, [id, isNew, setNodes, setEdges]);

  const onConnect = useCallback(
    (params: Connection) => setEdges((es) => addEdge({
      ...params,
      type: "smoothstep",
      style: { stroke: "rgba(167,139,250,0.5)" },
    }, es)),
    [setEdges],
  );

  const addNode = (nodeType: WfNodeData["nodeType"]) => {
    const newId = nextNodeId(nodeType);
    const node: Node<WfNodeData> = {
      id: newId,
      type: "wf",
      position: { x: 80 + Math.random() * 200, y: 80 + Math.random() * 200 },
      data: { label: newId, nodeType },
    };
    setNodes((ns) => [...ns, node]);
  };

  const handleNodesChange = (changes: NodeChange[]) => {
    onNodesChange(changes);
    for (const c of changes) {
      if (c.type === "remove") {
        setOverrides((o) => { const n = { ...o }; delete n[c.id]; return n; });
        setEdges((es) => es.filter((e) => e.source !== c.id && e.target !== c.id));
      }
    }
  };

  const handleEdgesChange = (changes: EdgeChange[]) => {
    onEdgesChange(changes);
  };

  const editEdgeLabel = (edgeId: string) => {
    const cur = edges.find((e) => e.id === edgeId)?.label as string | undefined;
    const next = prompt('Edge condition (Python expression over state). Leave empty for fallback.', cur ?? "");
    if (next === null) return;
    setEdges((es) => es.map((e) => e.id === edgeId ? { ...e, label: next || undefined } : e));
  };

  const deleteSelected = useCallback(() => {
    if (selectedNodeId) {
      setNodes((ns) => ns.filter((n) => n.id !== selectedNodeId));
      setEdges((es) => es.filter((e) => e.source !== selectedNodeId && e.target !== selectedNodeId));
      setOverrides((o) => {
        const next = { ...o };
        delete next[selectedNodeId];
        return next;
      });
      setSelectedNodeId(null);
    } else if (selectedEdgeId) {
      setEdges((es) => es.filter((e) => e.id !== selectedEdgeId));
      setSelectedEdgeId(null);
    }
  }, [selectedNodeId, selectedEdgeId, setNodes, setEdges]);

  // Keyboard: Delete / Backspace removes the selected node or edge.
  // We attach to window rather than rely on React Flow's deleteKeyCode so we
  // can avoid firing when the user is typing inside an input/textarea.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Delete" && e.key !== "Backspace") return;
      const tag = (document.activeElement?.tagName ?? "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if ((document.activeElement as HTMLElement | null)?.isContentEditable) return;
      if (selectedNodeId || selectedEdgeId) {
        e.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selectedNodeId, selectedEdgeId, deleteSelected]);

  const updateOverrides = (id: string, patch: Record<string, any>) =>
    setOverrides((o) => ({ ...o, [id]: patch }));

  const updateNodeData = (id: string, patch: Partial<WfNodeData>) =>
    setNodes((ns) => ns.map((n) => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));

  const setStart = (nodeId: string) => setMeta((m) => ({ ...m, start: nodeId }));

  const save = async () => {
    if (!meta.id) { alert("Workflow id is required"); return; }
    setSaving(true);
    try {
      const wf = fromFlow(meta, nodes, edges, overrides);
      await api.saveWorkflow(wf.id, wf);
      setSavedAt(Date.now());
      if (isNew) navigate(`/builder/${wf.id}`, { replace: true });
    } catch (e) {
      alert(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null;

  return (
    <div className="h-full flex flex-col">
      <div className="border-b border-border-subtle px-6 py-3 flex items-center gap-3">
        <button
          onClick={() => navigate("/builder")}
          className="rounded-lg p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-muted"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <input
          type="text"
          placeholder="workflow_id"
          value={meta.id}
          onChange={(e) => setMeta({ ...meta, id: e.target.value })}
          disabled={!isNew && Boolean(id)}
          className="rounded-md border border-border bg-bg-muted px-2.5 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand disabled:opacity-60"
        />
        <input
          type="text"
          placeholder="Description"
          value={meta.description}
          onChange={(e) => setMeta({ ...meta, description: e.target.value })}
          className="flex-1 rounded-md border border-border bg-bg-muted px-2.5 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
        />
        <span className="text-xs text-text-muted">start: <code className="text-brand">{meta.start || "—"}</code></span>
        {savedAt && <span className="text-xs text-text-muted">Saved</span>}
        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-3 py-1.5 text-sm font-medium shadow-glow hover:bg-brand-hover disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" /> {saving ? "Saving…" : "Save"}
        </button>
      </div>

      <div className="flex-1 min-h-0 grid grid-cols-12 gap-4 p-4">
        <div className="col-span-9 flex flex-col gap-3">
          <div className="flex items-center gap-2 flex-wrap">
            <Toolbar onAdd={addNode} />
            <SelectionActions
              selectedNode={selectedNode}
              selectedEdgeId={selectedEdgeId}
              edges={edges}
              onDelete={deleteSelected}
              onEditEdgeCondition={editEdgeLabel}
            />
          </div>
          <div className="flex-1 rounded-xl border border-border-subtle bg-bg-surface overflow-hidden">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={handleNodesChange}
              onEdgesChange={handleEdgesChange}
              onConnect={onConnect}
              nodeTypes={NODE_TYPES}
              onNodeClick={(_, n) => { setSelectedNodeId(n.id); setSelectedEdgeId(null); }}
              onEdgeClick={(_, e) => { setSelectedEdgeId(e.id); setSelectedNodeId(null); }}
              onEdgeDoubleClick={(_, e) => editEdgeLabel(e.id)}
              onPaneClick={() => { setSelectedNodeId(null); setSelectedEdgeId(null); }}
              deleteKeyCode={["Delete", "Backspace"]}
              onNodesDelete={(deleted) => {
                setOverrides((o) => {
                  const next = { ...o };
                  deleted.forEach((n) => { delete next[n.id]; });
                  return next;
                });
                if (selectedNodeId && deleted.some((n) => n.id === selectedNodeId)) {
                  setSelectedNodeId(null);
                }
              }}
              onEdgesDelete={(deleted) => {
                if (selectedEdgeId && deleted.some((e) => e.id === selectedEdgeId)) {
                  setSelectedEdgeId(null);
                }
              }}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} size={1} color="rgb(var(--text-muted) / 0.18)" />
              <MiniMap
                nodeColor={() => "rgb(var(--brand))"}
                maskColor="rgb(var(--bg-base) / 0.6)"
                style={{
                  background: "rgb(var(--bg-surface))",
                  border: "1px solid rgb(var(--text-muted) / 0.12)",
                  borderRadius: 8,
                }}
              />
              <Controls
                className="!bg-bg-elevated !border-border-subtle !rounded-lg !shadow-card"
              />
            </ReactFlow>
          </div>
        </div>

        <aside className="col-span-3 rounded-xl border border-border-subtle bg-bg-surface overflow-y-auto scrollbar-thin">
          <div className="text-xs uppercase tracking-wider text-text-muted px-4 pt-4">Inspector</div>
          <NodeInspector
            node={selectedNode ? { id: selectedNode.id, data: selectedNode.data } : null}
            overrides={overrides}
            onOverridesChange={updateOverrides}
            onLabelChange={updateNodeData}
            onSetStart={setStart}
            isStart={selectedNode?.id === meta.start}
          />
        </aside>
      </div>
    </div>
  );
}

type SelectionActionsProps = {
  selectedNode: Node<WfNodeData> | null;
  selectedEdgeId: string | null;
  edges: Edge[];
  onDelete: () => void;
  onEditEdgeCondition: (edgeId: string) => void;
};

function SelectionActions({
  selectedNode,
  selectedEdgeId,
  edges,
  onDelete,
  onEditEdgeCondition,
}: SelectionActionsProps) {
  const hasSelection = Boolean(selectedNode || selectedEdgeId);
  const edge = selectedEdgeId ? edges.find((e) => e.id === selectedEdgeId) : null;

  return (
    <div className="flex items-center gap-1.5 p-2 rounded-xl border border-border-subtle bg-bg-surface min-h-[40px]">
      {!hasSelection ? (
        <span className="inline-flex items-center gap-1.5 text-xs text-text-muted px-2">
          <MousePointer2 className="h-3 w-3" />
          Click a node or edge to select · Delete / Backspace to remove
        </span>
      ) : (
        <>
          <span className="inline-flex items-center gap-1.5 px-2 text-xs text-text-secondary">
            <span className="text-text-muted uppercase tracking-wider">Selected:</span>
            {selectedNode ? (
              <>
                <span className="rounded bg-bg-muted border border-border-subtle px-1.5 py-0.5 font-mono text-[12px] text-text-primary">
                  {selectedNode.data.nodeType}
                </span>
                <span className="text-text-primary">{selectedNode.id}</span>
              </>
            ) : (
              <>
                <span className="rounded bg-bg-muted border border-border-subtle px-1.5 py-0.5 font-mono text-[12px] text-text-primary">
                  edge
                </span>
                <span className="text-text-secondary">
                  {edge?.source} → {edge?.target}
                  {edge?.label ? <span className="ml-1 text-brand">[{String(edge.label)}]</span> : null}
                </span>
              </>
            )}
          </span>

          {selectedEdgeId && (
            <button
              onClick={() => onEditEdgeCondition(selectedEdgeId)}
              className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-muted transition-colors"
            >
              Edit condition
            </button>
          )}

          <button
            onClick={onDelete}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-rose-600 dark:text-rose-400 hover:text-rose-700 dark:hover:text-rose-300 hover:bg-rose-500/10 transition-colors ml-auto"
          >
            <Trash2 className="h-3 w-3" />
            Delete
            <kbd className="ml-1 hidden sm:inline-flex items-center rounded border border-border-subtle bg-bg-muted px-1 py-0.5 text-[10px] text-text-muted font-mono">
              Del
            </kbd>
          </button>
        </>
      )}
    </div>
  );
}

function seedOverrides(nodes: any[], set: (o: Record<string, any>) => void) {
  const out: Record<string, any> = {};
  for (const n of nodes) {
    out[n.id] = {
      widget: n.widget,
      props: n.props,
      save_to: n.save_to,
      text: n.text,
      tool: n.tool,
      args: n.args,
    };
  }
  set(out);
}

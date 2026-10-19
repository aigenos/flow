import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ReactFlow, {
  Background, BackgroundVariant, Controls, MiniMap,
  addEdge, useEdgesState, useNodesState,
  type Connection, type Node, type ReactFlowInstance,
} from "reactflow";
import "reactflow/dist/style.css";
import {
  AlertCircle, AlertTriangle, Boxes, Check, Copy, CornerDownRight, ExternalLink,
  FileCode2, LayoutList, Link2, Play, Plus, Redo2, Save, ScrollText, ShieldCheck,
  Sparkles, Trash2, Undo2, Wand2,
} from "lucide-react";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { Button } from "../components/ui/button";
import { HistoryPanel } from "../components/HistoryPanel";
import { ICONS, NODE_TYPES, THEME, type WfNodeData } from "../builder/nodes";
import {
  EDGE_STYLE, defaultStep, flowToSteps, flowYaml, parseSkillMd, replaceFlow,
  replaceFrontmatter,
  stepsToFlow, summaryOf, verbOf, type SkillStep, type Verb,
} from "../builder/skillflow";
import { useFlowHistory } from "../builder/useFlowHistory";
import { validateFlow, worstByNode, type Issue } from "../builder/validate";
import { VerifyPanel } from "../builder/VerifyPanel";
import { BlockPanel, PALETTE, ShortStepPanel } from "../builder/ShortStepPanel";
import {
  COLUMN_X, INDENT, asksOf, blockOf, confirmAbove, describeStep, flattenSteps, indexOfPath,
  isHeader, namesAbove, newBlock, newStep, repeatOf, replaceSteps, sameStepKey, shortVerbOf,
  starterSkill, stepsToCanvas, stepsYaml,
  type Block, type CheckResult, type PaletteKind, type ShortVerb, type StepContext, type ToolInfo,
} from "../builder/steps";
import { AppDialog, AppDialogDescription, AppDialogTitle } from "../components/AppDialog";

/** Vertical gap between chained steps — also the shove distance when a step
 *  is inserted mid-chain. A step with a second line and badges is taller than
 *  150, so the column is spaced for that. */
const ROW = 172;

/** A step node's size before React Flow has measured it. */
const NODE_W = 260;
const NODE_H = 96;
/** Never open a flow smaller than this — below it the labels stop being text. */
const MIN_OPEN_ZOOM = 0.9;

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

type SkillListItem = {
  name: string;
  description?: string;
  /** Declared field specs — the Verify panel offers these when writing a scenario. */
  fields?: Record<string, unknown>;
};
type ManagedTool = {
  name: string; display_name: string; side_effecting: boolean; mode: "read" | "write";
  parameters: Record<string, { type: string; required: boolean; pattern?: string; enum?: unknown[] }>;
};

let idc = 1000;

/** The step kinds with authoring semantics — shown in the left rail and
 *  the inspector header. */
const VERB_INFO: Array<{ verb: Verb & WfNodeData["nodeType"]; label: string; blurb: string }> = [
  { verb: "ask", label: "Ask", blurb: "Collect a field from the user — validated, re-armed on corrections." },
  { verb: "say", label: "Say", blurb: "Deterministic message, templated over collected state." },
  { verb: "do", label: "Do", blurb: "Run an action — args pinned from validated state, gated effects fire at most once." },
  { verb: "pick", label: "Pick", blurb: "User selects a row — data table, autosuggest, or your catalog widget." },
  { verb: "show", label: "Show", blurb: "Render data as a widget — auto-mapped from shape, or by widget id." },
  { verb: "check", label: "Check", blurb: "Fail-closed gate — on false: explain, clear, and re-collect." },
  { verb: "chat", label: "Chat", blurb: "Scoped free-chat leaf with allow-listed tools and pinned args." },
  { verb: "widget", label: "Widget", blurb: "Render a widget you designed in the catalog; its submission saves typed fields into the flow — no LLM in that path." },
  { verb: "link", label: "Link", blurb: "Hand the user to another skill and come back to the next step. If that skill is cancelled or declined, this flow stops here." },
];

/**
 * Visual editor for a skill's steps. A short-form skill (`steps:`) is four
 * kinds of node — ask, say, do, confirm — and is checked by the loader itself
 * while it is edited; a long-form skill (`flow:`) keeps its nine step kinds.
 * Saving writes the steps back into the skill's frontmatter through the same
 * endpoint the code editor uses, so the backend's fail-closed loader validates
 * every save and its plain-English errors surface here.
 */
export function SkillFlowPage() {
  const { name } = useParams();
  const navigate = useNavigate();

  const [skills, setSkills] = useState<SkillListItem[]>([]);
  const [md, setMd] = useState<string | null>(null);
  const [mode, setMode] = useState<"declarative" | "steps" | "imperative" | "none" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [baselineYaml, setBaselineYaml] = useState<string>("");
  // Full SKILL.md as loaded/saved — so guideline edits (which live in the
  // frontmatter, not the flow) also light up the unsaved badge.
  const [baselineMd, setBaselineMd] = useState<string | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<WfNodeData>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // ?panel=verify|guides|yaml deep-links an inspector tab (docs, demos, screenshots).
  const [panelTab, setPanelTab] = useState<"step" | "verify" | "guides" | "yaml">(() => {
    const p = new URLSearchParams(window.location.search).get("panel");
    return p === "guides" || p === "yaml" || p === "verify" ? p : "step";
  });
  const [saving, setSaving] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [managedTools, setManagedTools] = useState<ManagedTool[]>([]);
  const [showBinding, setShowBinding] = useState(false);
  // The tool library: what a short-form `do:` step can name.
  const [tools, setTools] = useState<ToolInfo[]>([]);
  // What the loader says about the steps as they stand (short form only).
  const [check, setCheck] = useState<CheckResult | null>(null);
  const [showNew, setShowNew] = useState(false);
  // "Change it in a sentence": the model writes the change, the loader reads
  // it, and it lands on the canvas unsaved.
  const [wish, setWish] = useState("");
  const [wishing, setWishing] = useState(false);
  const [proposal, setProposal] = useState<{ tone: "ok" | "bad" | "info"; text: string } | null>(null);
  useEffect(() => { setProposal(null); setWish(""); }, [name]);
  // Bumped after a History restore so the load effect re-fetches the skill.
  const [reloadTick, setReloadTick] = useState(0);
  // Bumped when a skill's steps land, so the view is framed once per load.
  const [loadedTick, setLoadedTick] = useState(0);
  const [rf, setRf] = useState<ReactFlowInstance | null>(null);
  const paneRef = useRef<HTMLDivElement>(null);

  const history = useFlowHistory(nodes, edges, setNodes, setEdges);
  const { commit, reset: resetHistory } = history;

  // The step the inspector shows is the one that looks selected. A step added
  // from the rail became the inspector's step while the ring stayed on the
  // step clicked before it.
  useEffect(() => {
    setNodes((ns) => (ns.some((n) => Boolean(n.selected) !== (n.id === selectedId))
      ? ns.map((n) => (Boolean(n.selected) === (n.id === selectedId) ? n : { ...n, selected: n.id === selectedId }))
      : ns));
  }, [selectedId, setNodes]);

  useEffect(() => {
    fetch("/api/flow/skills")
      .then((r) => r.json())
      .then((j) => setSkills(Array.isArray(j) ? j : j.skills ?? []))
      .catch(() => setSkills([]));
  }, [name]);

  useEffect(() => {
    fetch(name ? `/api/flow/tools?skill=${encodeURIComponent(name)}` : "/api/flow/tools")
      .then((r) => (r.ok ? r.json() : { tools: [] }))
      .then((j) => setTools(Array.isArray(j.tools) ? j.tools : []))
      .catch(() => setTools([]));
  }, [name]);

  // A skill with no flow yet starts in the short form: five kinds of step.
  const short = mode === "steps" || mode === "none";
  const stepContext: StepContext = useMemo(
    () => ({ tools: new Map(tools.map((t) => [t.name, t])), skills: new Set(skills.map((sk) => sk.name)) }),
    [tools, skills],
  );
  const stepContextRef = useRef(stepContext);
  stepContextRef.current = stepContext;

  // Widget catalog — lets pick/widget steps reference designs by dropdown
  // instead of a hand-typed id.
  const [widgetCatalog, setWidgetCatalog] = useState<string[]>([]);
  useEffect(() => {
    api.listWidgets()
      .then((ws) => setWidgetCatalog(ws.map((w) => w.id)))
      .catch(() => setWidgetCatalog([]));
  }, []);

  useEffect(() => {
    let token = "";
    try { token = sessionStorage.getItem("aigenos-connector-admin-token") ?? ""; } catch { /* ignored */ }
    if (!token) return;
    fetch("/api/flow/connectors/catalog", { headers: { "x-connector-admin-token": token } })
      .then((response) => response.ok ? response.json() : Promise.reject(new Error("catalog unavailable")))
      .then((result) => setManagedTools(result.tools ?? []))
      .catch(() => setManagedTools([]));
  }, [name]);

  useEffect(() => {
    resetHistory();
    // Drop the previous skill's document before fetching the next one, so a
    // save in between can never write one skill's steps under another's name.
    setMd(null);
    setBaselineMd(null);
    if (!name) { setNodes([]); setEdges([]); return; }
    setError(null);
    let live = true;
    const get = async (path: string) => {
      const r = await fetch(path);
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      return r.json();
    };
    Promise.all([
      get(`/api/flow/skills/${encodeURIComponent(name)}/files`),
      get(`/api/flow/skills/${encodeURIComponent(name)}/outline`),
    ])
      .then(([files, outline]) => {
        if (!live) return; // a newer skill was picked while this one loaded
        setMd(files.skill_md);
        setBaselineMd(files.skill_md);
        setMode(outline.mode);
        setNote(outline.note ?? null);
        setCheck(null);
        const isShort = outline.mode === "steps" || outline.mode === "none";
        let steps: SkillStep[] = Array.isArray(outline.steps) ? outline.steps : [];
        if (outline.mode === "steps") {
          // Read from the file itself, so what is edited is what was written.
          try { steps = flattenSteps(parseSkillMd(files.skill_md).fm.steps); } catch { /* keep the outline's */ }
        }
        const fl = isShort ? stepsToCanvas(steps, stepContextRef.current) : stepsToFlow(steps);
        setNodes(fl.nodes);
        setEdges(fl.edges);
        setBaselineYaml(isShort ? stepsYaml(steps) : flowYaml(steps));
        // Pre-select the first step so the inspector opens populated.
        setSelectedId(fl.nodes[0]?.id ?? null);
        setLoadedTick((t) => t + 1);
      })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [name, reloadTick, setNodes, setEdges, resetHistory]);

  // Open a flow where it starts, at a size you can read. Fitting the whole
  // column into the pane shrinks a twelve-step flow's labels to about 6px on a
  // laptop, so a long flow opens at the top and scrolls instead.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  useEffect(() => {
    const pane = paneRef.current;
    const all = nodesRef.current;
    if (!rf || !pane || loadedTick === 0 || all.length === 0) return;
    const { width, height } = pane.getBoundingClientRect();
    const left = Math.min(...all.map((n) => n.position.x));
    const right = Math.max(...all.map((n) => n.position.x + (n.width ?? NODE_W)));
    const top = Math.min(...all.map((n) => n.position.y));
    const bottom = Math.max(...all.map((n) => n.position.y + (n.height ?? NODE_H)));
    const fits = Math.min(width / (right - left + 96), height / (bottom - top + 96));
    const zoom = Math.min(1, Math.max(MIN_OPEN_ZOOM, fits));
    rf.setViewport({ x: width / 2 - ((left + right) / 2) * zoom, y: 32 - top * zoom, zoom });
  }, [rf, loadedTick]);

  const onConnect = useCallback(
    (params: Connection) => {
      commit();
      setEdges((es) => addEdge({ ...params, ...EDGE_STYLE }, es));
    },
    [commit, setEdges],
  );

  /** Where a short-form step sits across: a step inside a block is indented under its header. */
  const columnX = (step: SkillStep | undefined) => COLUMN_X + (step?._in ? INDENT : 0);

  /**
   * Splice steps into the chain after `anchor` (or append when there is
   * none), shoving everything below down so the new rows don't land on top of
   * existing nodes. Returns the new nodes' ids, in order.
   */
  const insertManyAfter = (anchor: Node<WfNodeData> | undefined, added: SkillStep[], offsetX = 0,
                           select: number | null = 0): string[] => {
    const ids = added.map(() => `s_${idc++}`);
    const y0 = anchor ? anchor.position.y + ROW : 30;
    const made: Node<WfNodeData>[] = added.map((step, k) => ({
      id: ids[k],
      type: "wf",
      position: { x: (short ? columnX(step) : (anchor?.position.x ?? 140)) + offsetX, y: y0 + k * ROW },
      data: short
        ? { ...describeStep(step, stepContext), step }
        : { label: summaryOf(step), nodeType: verbOf(step), step },
    }));

    commit();
    setNodes((ns) => [
      // shove the tail down so the new rows have somewhere to sit
      ...ns.map((n) => (anchor && n.position.y >= y0 ? { ...n, position: { ...n.position, y: n.position.y + ROW * added.length } } : n)),
      ...made,
    ]);
    setEdges((es) => {
      let next = es;
      const outgoing = anchor ? es.find((e) => e.source === anchor.id) : undefined;
      if (outgoing) next = next.filter((e) => e.id !== outgoing.id);
      const chain = [...(anchor ? [anchor.id] : []), ...ids, ...(outgoing ? [outgoing.target] : [])];
      for (let k = 0; k + 1 < chain.length; k++) {
        next = addEdge({ source: chain[k], target: chain[k + 1], sourceHandle: null, targetHandle: null, ...EDGE_STYLE } as any, next);
      }
      return next;
    });
    if (select !== null && ids[select]) {
      setSelectedId(ids[select]);
      setPanelTab("step");
    }
    return ids;
  };

  const insertAfter = (anchor: Node<WfNodeData> | undefined, step: SkillStep, offsetX = 0, select = true) =>
    insertManyAfter(anchor, [step], offsetX, select ? 0 : null)[0];

  /** The nodes in the order the flow runs them. */
  const orderedNodes = (): Node<WfNodeData>[] => {
    const order = flowToSteps(nodes, edges);
    const byStep = new Map(nodes.map((n) => [n.data.step, n]));
    return order.map((st) => byStep.get(st)).filter((n): n is Node<WfNodeData> => Boolean(n));
  };

  /** The block a node belongs to: its own id for a header, or the header it is under. */
  const blockIdOf = (node: Node<WfNodeData> | undefined): string | undefined =>
    blockOf(node?.data.step)?.id ?? (node?.data.step?._in as string | undefined);

  /** The last node of a block (the header itself when it is empty). */
  const lastOfBlock = (blockId: string): Node<WfNodeData> | undefined => {
    const run = orderedNodes().filter((n) => blockIdOf(n) === blockId);
    return run.at(-1);
  };

  /** Put a step directly above `target` and leave the selection where it is:
   *  "ask the user for it" from a Do step's inspector. */
  const insertAbove = (target: Node<WfNodeData>, given: SkillStep) => {
    let step = given;
    const inside = target.data.step?._in as string | undefined;
    if (inside) {
      const header = nodes.find((n) => blockOf(n.data.step)?.id === inside);
      const isRepeat = Boolean(repeatOf(blockOf(header?.data.step)));
      if (isRepeat && shortVerbOf(given) !== "ask") {
        // Only questions go inside a repeat: a Confirm goes above the whole repeat.
        if (header) { insertAbove(header, given); return; }
      } else {
        step = { ...given, _in: inside };
      }
    }
    const before = edges.find((e) => e.target === target.id);
    const anchor = before ? nodes.find((n) => n.id === before.source) : undefined;
    if (anchor) { insertAfter(anchor, step, 0, false); return; }
    // `target` is the first step: the new one becomes the head of the chain.
    const id = `s_${idc++}`;
    commit();
    setNodes((ns) => [
      { id, type: "wf", position: { x: columnX(step), y: target.position.y }, data: { ...describeStep(step, stepContext), step } },
      ...ns.map((n) => ({ ...n, position: { ...n.position, y: n.position.y + ROW } })),
    ]);
    setEdges((es) => addEdge({ source: id, target: target.id, sourceHandle: null, targetHandle: null, ...EDGE_STYLE } as any, es));
  };

  /**
   * Add what the palette offers after the selected node (or at the end).
   * After a node inside a block, the new step joins that block, except where
   * it cannot go: only questions go inside a repeat, a block is never inside
   * another, and a chat is always the last step.
   */
  const addPaletteItem = (kind: PaletteKind) => {
    const all = orderedNodes();
    const taken = new Set(steps.flatMap((st) => (
      isHeader(st) ? Object.keys(blockOf(st)?.repeat ?? {}) : shortVerbOf(st) === "ask" ? asksOf(st).map(([n]) => n) : []
    )));
    let anchor = nodes.find((n) => n.id === selectedId) ?? all.at(-1);
    const inBlock = blockIdOf(anchor);
    const header = inBlock ? nodes.find((n) => blockOf(n.data.step)?.id === inBlock) : undefined;
    const inRepeat = Boolean(repeatOf(blockOf(header?.data.step)));

    if (kind === "chat") {
      insertAfter(all.at(-1), newStep("chat", taken));
      return;
    }
    if (kind === "condition" || kind === "repeat") {
      if (inBlock) anchor = lastOfBlock(inBlock) ?? anchor;
      insertManyAfter(anchor, newBlock(kind, taken), 0, 0);
      return;
    }
    const step = newStep(kind, taken);
    if (inBlock && (!inRepeat || kind === "ask")) {
      insertAfter(anchor, { ...step, _in: inBlock });
      return;
    }
    if (inBlock) anchor = lastOfBlock(inBlock) ?? anchor;    // after the repeat, not inside it
    insertAfter(anchor, step);
  };

  /** A new step lands after the selected one, or at the end when nothing is
   *  selected. */
  const addNode = (verb: WfNodeData["nodeType"]) => {
    const anchor =
      nodes.find((n) => n.id === selectedId) ??
      [...nodes].sort((a, b) => a.position.y - b.position.y).at(-1);
    if (short) {
      const taken = new Set(steps.flatMap((st) => (shortVerbOf(st) === "ask" ? asksOf(st).map(([n]) => n) : [])));
      insertAfter(anchor, newStep(verb as ShortVerb, taken));
    } else {
      insertAfter(anchor, defaultStep(verb as Verb));
    }
  };

  /**
   * Remove steps and reconnect their surviving neighbours so the chain stays a
   * chain. The reconnections are worked out from the *current* edges rather
   * than from inside the state updater: when this runs from React Flow's own
   * delete, the attached edges are already gone by then.
   */
  const dropNodes = (ids: string[]) => {
    const gone = new Set(ids);
    const before = (id: string) => edges.find((e) => e.target === id)?.source;
    const after = (id: string) => edges.find((e) => e.source === id)?.target;

    const links: Array<{ source: string; target: string }> = [];
    for (const id of ids) {
      // Walk past neighbours that are going away in the same gesture. Bounded
      // by the node count so a stray cycle can't spin here.
      let source = before(id);
      let target = after(id);
      for (let hops = 0; source && gone.has(source) && hops < nodes.length; hops++) source = before(source);
      for (let hops = 0; target && gone.has(target) && hops < nodes.length; hops++) target = after(target);
      if (source && target && !gone.has(source) && !gone.has(target)) links.push({ source, target });
    }

    // A block whose header goes keeps its steps: they are no longer inside anything.
    const unblocked = new Set(
      nodes.filter((n) => gone.has(n.id)).map((n) => blockOf(n.data.step)?.id).filter((id): id is string => Boolean(id)),
    );
    setNodes((ns) => ns.filter((n) => !gone.has(n.id)).map((n) => {
      if (!n.data.step?._in || !unblocked.has(n.data.step._in)) return n;
      const { _in, ...step } = n.data.step;
      return { ...n, position: { ...n.position, x: COLUMN_X }, data: { ...n.data, step, ...describeStep(step, stepContext) } };
    }));
    setEdges((es) => {
      let next = es.filter((e) => !gone.has(e.source) && !gone.has(e.target));
      for (const { source, target } of links) {
        if (next.some((e) => e.source === source && e.target === target)) continue;
        next = addEdge({ source, target, sourceHandle: null, targetHandle: null, ...EDGE_STYLE } as any, next);
      }
      return next;
    });
    if (selectedId && gone.has(selectedId)) setSelectedId(null);
  };

  const deleteNode = (id: string) => {
    commit();
    dropNodes([id]);
  };

  const duplicateNode = (id: string) => {
    const src = nodes.find((n) => n.id === id);
    if (!src?.data.step) return;
    insertAfter(src, JSON.parse(JSON.stringify(src.data.step)), 36);
  };

  const autoArrange = () => {
    const order = flowToSteps(nodes, edges);
    const rank = new Map(order.map((s, i) => [s, i]));
    commit();
    setNodes((ns) =>
      ns.map((n) => {
        const i = n.data.step ? rank.get(n.data.step) : undefined;
        return i === undefined ? n : { ...n, position: { x: short ? columnX(n.data.step) : 140, y: 30 + i * ROW } };
      }),
    );
  };

  /** Change a block's header: its condition, or its list's name and question. */
  const patchBlock = (nodeId: string, patch: Partial<Block>) => {
    commit(`block:${nodeId}:${Object.keys(patch).join(",")}`);
    setNodes((ns) => ns.map((n) => {
      const block = blockOf(n.data.step);
      if (n.id !== nodeId || !block) return n;
      const next: Block = { ...block, ...patch };
      if (next.when === undefined) delete next.when;
      const step = { ...n.data.step, _block: next };
      return { ...n, data: { ...n.data, step, ...describeStep(step, stepContext) } };
    }));
  };

  /** Move a step out of its block, to just after the block's last step. */
  const leaveBlock = (nodeId: string) => {
    const node = nodes.find((n) => n.id === nodeId);
    const blockId = node?.data.step?._in as string | undefined;
    if (!node || !blockId) return;
    const { _in, ...step } = node.data.step!;
    const end = lastOfBlock(blockId);
    if (!end || end.id === node.id) {
      commit();
      setNodes((ns) => ns.map((n) => (n.id === nodeId
        ? { ...n, position: { ...n.position, x: COLUMN_X }, data: { ...n.data, step, ...describeStep(step, stepContext) } }
        : n)));
      return;
    }
    commit();
    dropNodes([nodeId]);
    insertAfter(end, step);
  };

  const patchStep = (nodeId: string, patch: SkillStep) => {
    // Tagged per node+key so a burst of typing collapses into one undo entry.
    commit(`patch:${nodeId}:${Object.keys(patch).join(",")}`);
    setNodes((ns) => ns.map((n) => {
      if (n.id !== nodeId) return n;
      const step = { ...(n.data.step ?? {}), ...patch };
      // Strip cleared props — but never the verb key itself, or a widget step
      // with an as-yet-unchosen id would silently stop being a widget step.
      const verbKey = (short
        ? ["ask", "say", "do", "confirm", "chat"]
        : ["ask", "say", "do", "run", "pick", "show", "check", "chat", "widget", "link", "code"])
        .find((k) => k in step);
      for (const k of Object.keys(step)) {
        if (k === verbKey) continue;
        if (step[k] === undefined || step[k] === "") delete step[k];
      }
      return { ...n, data: { ...n.data, step, ...(short ? describeStep(step, stepContext) : { label: summaryOf(step) }) } };
    }));
  };

  const steps = useMemo(() => flowToSteps(nodes, edges), [nodes, edges]);
  const yaml = useMemo(() => (short ? stepsYaml(steps) : flowYaml(steps)), [steps, short]);
  const selected = nodes.find((n) => n.id === selectedId) ?? null;
  const editable = mode !== "imperative";
  const dirty = editable && md != null && (yaml !== baselineYaml || md !== baselineMd);

  // Short form: ask the loader itself, a moment after the last edit. The steps
  // are sent one per node (blocks not regrouped), so its "step 3" is node 3.
  useEffect(() => {
    if (!short || !name || md == null) return;
    if (steps.length === 0) { setCheck(null); return; }
    let live = true;
    const handle = window.setTimeout(async () => {
      try {
        const r = await fetch("/api/flow/skills/check", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ name, skill_md: replaceSteps(md, steps, false) }),
        });
        if (live) setCheck(r.ok ? await r.json() : null);
      } catch {
        if (live) setCheck(null);
      }
    }, 350);
    return () => { live = false; window.clearTimeout(handle); };
    // `yaml` stands for the steps' content: dragging a node must not re-check.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [short, name, md, yaml]);

  /** The loader's objection, placed on the node it is about. */
  const checkIssues: Issue[] = useMemo(() => {
    if (!short || !check || check.ok || !check.step) return [];
    const position = indexOfPath(steps, check.step);
    const at = position === undefined ? undefined : steps[position];
    const node = at ? nodes.find((n) => n.data.step === at) : undefined;
    const message = check.error.replace(/^step [\d.]+: /, "");
    return node ? [{ nodeId: node.id, level: "error" as const, message }] : [];
  }, [short, check, steps, nodes]);
  /** An objection that belongs to no one step (the file does not parse, a missing skill). */
  const checkError = short && check && !check.ok && checkIssues.length === 0 ? check.error : null;

  const issues = useMemo(
    () => (!editable ? [] : short ? checkIssues : validateFlow(nodes, edges, widgetCatalog)),
    [editable, short, checkIssues, nodes, edges, widgetCatalog],
  );
  const errorCount = issues.filter((i) => i.level === "error").length;
  const selectedIssues = issues.filter((i) => i.nodeId === selectedId);

  // Keep the step-number chips and the issue dots in sync with executed order.
  useEffect(() => {
    const rank = new Map(steps.map((s, i) => [s, i + 1]));
    const worst = worstByNode(issues);
    setNodes((ns) => {
      let changed = false;
      const next = ns.map((n) => {
        const index = n.data.step ? rank.get(n.data.step) : undefined;
        const issue = worst.get(n.id);
        // A short-form node's line and badges depend on the library too (is
        // this tool a write? is that name a skill?), which loads after the file.
        const face = short && n.data.step ? describeStep(n.data.step, stepContext) : null;
        const sameFace = !face || (
          n.data.label === face.label && n.data.preview === face.preview && n.data.nodeType === face.nodeType &&
          n.data.kindLabel === face.kindLabel && (n.data.badges ?? []).join("|") === face.badges.join("|")
        );
        if (n.data.index === index && n.data.issue === issue && sameFace) return n;
        changed = true;
        return { ...n, data: { ...n.data, ...(face ?? {}), index, issue } };
      });
      return changed ? next : ns;
    });
  }, [steps, issues, setNodes, short, stepContext]);

  /**
   * Change the steps as a sentence says. The server asks the model for the
   * change and puts the result through the loader before answering, so what
   * lands here has already been read: it either loads, or arrives with the
   * loader's objection. Either way it is an unsaved edit like any other: Undo
   * takes it back, and only Save writes it.
   */
  const changeInWords = async () => {
    const instruction = wish.trim();
    if (!name || md == null || !instruction || wishing) return;
    setWishing(true);
    setProposal(null);
    try {
      const r = await fetch("/api/flow/skills/edit", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name, skill_md: replaceSteps(md, steps), instruction }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) {
        setProposal({ tone: "bad", text: typeof j.detail === "string" ? j.detail : `That did not work (${r.status}).` });
        return;
      }
      if (j.cannot) { setProposal({ tone: "info", text: `Nothing was changed. ${j.cannot}` }); return; }
      if (j.unchanged) { setProposal({ tone: "info", text: `Nothing was changed. ${j.changed ?? ""}`.trim() }); return; }
      if (typeof j.skill_md !== "string" || j.attempts === 0 || !j.check) {
        setProposal({ tone: "bad", text: "The model did not return a change. Try saying it another way." });
        return;
      }
      const next = flattenSteps(parseSkillMd(j.skill_md).fm.steps);
      // What differs, counted here from the two step lists: the sentence the
      // model wrote about its own change is a description, not the evidence.
      const left = new Map<string, number>();
      for (const st of steps) left.set(sameStepKey(st), (left.get(sameStepKey(st)) ?? 0) + 1);
      const fresh: number[] = [];
      next.forEach((st, i) => {
        const k = sameStepKey(st);
        const n = left.get(k) ?? 0;
        if (n > 0) left.set(k, n - 1); else fresh.push(i);
      });
      const gone = [...left.values()].reduce((a, b) => a + b, 0);
      if (fresh.length === 0 && gone === 0) {
        setProposal({ tone: "info", text: `Nothing was changed. ${j.changed ?? ""}`.trim() });
        return;
      }
      const fl = stepsToCanvas(next, stepContextRef.current);
      commit();
      setNodes(fl.nodes);
      setEdges(fl.edges);
      // Show the change: its first step, selected and in view.
      const first = fl.nodes[fresh[0] ?? 0];
      setSelectedId(first?.id ?? null);
      setPanelTab("step");
      if (first && rf) {
        rf.setCenter(first.position.x + NODE_W / 2, first.position.y + NODE_H / 2, { zoom: rf.getZoom(), duration: 350 });
      }
      setWish("");
      const counted = [
        fresh.length ? `${fresh.length} step${fresh.length === 1 ? "" : "s"} new or changed` : "",
        gone ? `${gone} removed or replaced` : "",
      ].filter(Boolean).join(", ");
      const what = `${(j.changed as string) || "The steps were changed."} (${counted}; the first one is selected.)`;
      setProposal(j.ok
        ? { tone: "ok", text: `${what} The loader has read it and it loads. Not saved yet: look it over, then Save, or Undo to take it back.` }
        : { tone: "bad", text: `${what} It does not load yet: ${String(j.error ?? "").replace(/^step [\d.]+: /, "")} Fix it on the canvas, or Undo to take it back.` });
    } catch (e) {
      setProposal({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setWishing(false);
    }
  };

  /**
   * Apply an edit that rewrote the frontmatter's `flow:` (the binding wizard).
   * The canvas is the source of truth on Save — `replaceFlow` writes the nodes
   * back over `fm.flow` — so the nodes have to be re-derived here or the
   * wizard's new steps would be silently dropped by the next save.
   */
  const applyFlowEdit = (nextMd: string) => {
    setMd(nextMd);
    try {
      const { fm } = parseSkillMd(nextMd);
      if (!Array.isArray(fm.flow)) return;
      commit();
      const fl = stepsToFlow(fm.flow);
      setNodes(fl.nodes);
      setEdges(fl.edges);
      setSelectedId(fl.nodes[0]?.id ?? null);
    } catch {
      /* unparseable md — leave the canvas as-is rather than blanking it */
    }
  };

  /** Select the first step carrying a problem — the "N issues" chip's job. */
  const revealFirstIssue = () => {
    const first = issues.find((i) => i.level === "error") ?? issues[0];
    if (!first) return;
    setSelectedId(first.nodeId);
    setPanelTab("step");
  };

  /** Resolves true when the server accepted the save; a rejection lands in `error`. */
  const save = useCallback(async (): Promise<boolean> => {
    if (!name || md == null || !editable) return false;
    setSaving(true);
    setError(null);
    try {
      const nextMd = short ? replaceSteps(md, steps) : replaceFlow(md, steps);
      const r = await fetch(`/api/flow/skills/${encodeURIComponent(name)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ skill_md: nextMd }),
      });
      if (!r.ok) {
        const text = await r.text();
        let detail = text;
        try { detail = String(JSON.parse(text).detail ?? text); } catch { /* not JSON: show it as it came */ }
        throw new Error(detail.replace(/^skill update rejected: /, ""));
      }
      setMd(nextMd);
      setBaselineMd(nextMd);
      setBaselineYaml(yaml);
      setMode(short ? "steps" : "declarative");
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 2500);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [name, md, editable, steps, yaml, short]);

  /** Leaving the Canvas drops unsaved edits, so the links that leave ask first. */
  const confirmLeave = () =>
    !dirty || window.confirm("You have unsaved changes on this flow. Leave without saving?");

  // Editor keys. Save works from anywhere (including a focused field); the
  // rest stand down while the user is typing so they don't hijack the caret.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      const key = e.key.toLowerCase();

      if (key === "s") {
        e.preventDefault();
        void save();
        return;
      }

      const el = document.activeElement;
      const typing = el instanceof HTMLElement &&
        (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
      if (typing || !editable) return;

      if (key === "z" && !e.shiftKey) { e.preventDefault(); history.undo(); }
      else if ((key === "z" && e.shiftKey) || key === "y") { e.preventDefault(); history.redo(); }
      else if (key === "d" && selectedId) { e.preventDefault(); duplicateNode(selectedId); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Don't let a reload throw away an unsaved flow.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <div className="h-full flex flex-col">
      <div className="glass border-b border-border-subtle px-6 py-3 flex items-center gap-3 sticky top-0 z-20">
        <span className="text-sm font-semibold tracking-tight shrink-0">
          Flow <span className="grad-text">canvas</span>
        </span>
        <select
          value={name ?? ""}
          onChange={(e) => navigate(e.target.value ? `/canvas/${e.target.value}` : "/canvas")}
          className="h-8 rounded-lg border border-border bg-bg-muted/70 px-2.5 text-sm shadow-sm transition-all focus:outline-none focus:border-brand/60 focus:ring-2 focus:ring-brand/25"
        >
          <option value="">— pick a skill —</option>
          {skills.map((s) => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => { if (confirmLeave()) setShowNew(true); }}
          title="Start a new skill on the canvas"
          className="inline-flex h-8 shrink-0 items-center gap-1 rounded-lg border border-border-subtle bg-bg-muted/40 px-2.5 text-xs font-medium text-text-secondary transition-colors hover:border-border hover:text-text-primary"
        >
          <Plus className="h-3.5 w-3.5" /> New skill
        </button>
        <span className="text-xs text-text-muted flex-1 truncate">
          {skills.find((s) => s.name === name)?.description ?? "Nodes run top-to-bottom; guards are badges, not branches."}
        </span>

        {name && short && check?.ok && issues.length === 0 && (
          <span
            title="The loader accepts these steps as they stand. Every question has a type, every tool has its inputs, and every tool that changes something has a yes above it."
            className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1 text-[12px] font-medium text-emerald-700 dark:text-emerald-400"
          >
            <Check className="h-3 w-3" /> loads
          </span>
        )}

        {name && editable && (
          <div className="flex items-center rounded-lg border border-border-subtle bg-bg-muted/40 p-0.5">
            <button
              onClick={history.undo}
              disabled={!history.canUndo}
              title={`Undo (${MOD}Z)`}
              aria-label="Undo"
              className="rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-muted hover:text-text-primary disabled:pointer-events-none disabled:opacity-35"
            >
              <Undo2 className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={history.redo}
              disabled={!history.canRedo}
              title={`Redo (${MOD}⇧Z)`}
              aria-label="Redo"
              className="rounded-md p-1.5 text-text-secondary transition-colors hover:bg-bg-muted hover:text-text-primary disabled:pointer-events-none disabled:opacity-35"
            >
              <Redo2 className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {name && editable && issues.length > 0 && (
          <button
            onClick={revealFirstIssue}
            title="Jump to the first step that needs attention"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] font-medium transition-colors control-press",
              errorCount > 0
                ? "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 hover:bg-rose-500/20"
                : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20",
            )}
          >
            <AlertCircle className="h-3 w-3" />
            {errorCount > 0
              ? `${errorCount} to fix`
              : `${issues.length} to review`}
          </button>
        )}

        {/* Only offered when a connector catalog is actually reachable — the
            wizard binds managed operations, so with no tools it has nothing
            to bind. */}
        {name && editable && !short && managedTools.length > 0 && (
          <Button variant="ghost" size="sm" onClick={() => setShowBinding((v) => !v)}
                  title="Bind a managed connector operation into this flow">
            <Link2 /> Bind tool
          </Button>
        )}

        {name && editable && nodes.length > 1 && (
          <Button variant="ghost" size="sm" onClick={autoArrange} title="Snap nodes back into a tidy column">
            <Wand2 /> Arrange
          </Button>
        )}
        {/* Pin the skill so the run enters THIS flow rather than relying on the
            classifier to route there. Flow runs the saved skill, so unsaved
            edits are saved first — and a rejected save keeps you here. */}
        {name && short && (
          <Button variant="outline" size="sm"
                  disabled={saving}
                  onClick={async () => {
                    if (dirty && !(await save())) return;
                    navigate(`/procedure/${encodeURIComponent(name)}`);
                  }}
                  title="Read this skill back as a procedure: its steps in plain words, every way through it, and what must be true before anything changes">
            <ScrollText /> Procedure
          </Button>
        )}
        {name && (
          <Button variant="outline" size="sm"
                  disabled={saving}
                  onClick={async () => {
                    if (dirty && !(await save())) return;
                    navigate(`/flow?skill=${encodeURIComponent(name)}`);
                  }}
                  title={dirty ? `Save, then run ${name} live in the chat` : `Run ${name} live in the chat`}>
            <Play /> Test in Flow
          </Button>
        )}
        {name && editable && (
          <HistoryPanel
            kind="skill"
            refId={name}
            onRestore={(version) => api.restoreSkill(name, version)}
            onRestored={() => setReloadTick((t) => t + 1)}
          />
        )}
        {dirty && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" /> unsaved
          </span>
        )}
        {savedFlash && name && (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 animate-fade-in">
            <Check className="h-3 w-3" /> saved → skills/{name}/SKILL.md (hot-reloaded)
          </span>
        )}
        <Button size="sm" onClick={save} disabled={saving || !name || !editable}
                title={editable ? `Save (${MOD}S)` : "Python (@flow) skills are edited in code, not on the canvas"}>
          <Save /> {saving ? "Validating…" : "Save"}
        </Button>
      </div>

      {name && short && editable && md != null && (
        <form
          onSubmit={(e) => { e.preventDefault(); void changeInWords(); }}
          className="mx-6 mt-3 flex items-center gap-2"
        >
          <div className="relative flex-1">
            <Sparkles className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-brand" />
            <input
              value={wish}
              onChange={(e) => setWish(e.target.value)}
              disabled={wishing}
              placeholder='Change this skill in a sentence, e.g. "ask who approved it when the amount is over 500"'
              aria-label="Change this skill in a sentence"
              className="h-9 w-full rounded-lg border border-border bg-bg-muted/60 pl-9 pr-3 text-sm shadow-sm transition-all placeholder:text-text-muted focus:outline-none focus:border-brand/60 focus:ring-2 focus:ring-brand/25 disabled:opacity-60"
            />
          </div>
          <Button type="submit" size="sm" variant="outline" disabled={wishing || !wish.trim()}
                  title="The model writes the change. The loader reads it before you see it, and nothing is saved until you press Save.">
            <Wand2 /> {wishing ? "Reading it back…" : "Change it"}
          </Button>
        </form>
      )}
      {proposal && (
        <div
          role="status"
          className={cn(
            "mx-6 mt-2 flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed",
            proposal.tone === "ok" && "border-emerald-500/40 bg-emerald-500/[0.08] text-emerald-800 dark:text-emerald-300",
            proposal.tone === "bad" && "border-rose-500/40 bg-rose-500/[0.08] text-rose-700 dark:text-rose-300",
            proposal.tone === "info" && "border-border bg-bg-muted/50 text-text-secondary",
          )}
        >
          <span className="flex-1">{proposal.text}</span>
          <button type="button" onClick={() => setProposal(null)} className="shrink-0 underline-offset-2 hover:underline">
            Dismiss
          </button>
        </div>
      )}
      {note && (
        <div className="mx-6 mt-3 rounded-lg border border-amber-500/40 bg-amber-500/[0.08] px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          {note}
        </div>
      )}
      {error && (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <pre className="whitespace-pre-wrap font-mono">{error}</pre>
        </div>
      )}
      {checkError && !error && (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span className="leading-relaxed">{checkError}</span>
        </div>
      )}
      <NewSkillDialog
        open={showNew}
        taken={skills.map((sk) => sk.name)}
        onClose={() => setShowNew(false)}
        onCreated={(created) => { setShowNew(false); navigate(`/canvas/${encodeURIComponent(created)}`); }}
      />

      {showBinding && name && editable && !short && md && managedTools.length > 0 && (
        <BindingWizard
          md={md}
          tools={managedTools}
          onApply={(next) => { applyFlowEdit(next); setShowBinding(false); }}
          onError={setError}
        />
      )}

      {!name ? (
        <EmptyHero skills={skills} onPick={(n) => navigate(`/canvas/${n}`)} onNew={() => setShowNew(true)} />
      ) : (
        <div className="flex-1 min-h-0 flex gap-3 p-4">
          {/* verb rail */}
          {editable && (
            <aside data-tour="canvas-palette" className="w-[96px] shrink-0 glass-card p-2 flex flex-col gap-1 overflow-y-auto scrollbar-thin">
              <div className="px-1 pb-1.5">
                <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">
                  Add step
                </div>
                {/* Where the next click lands — insertion follows the selection. */}
                <div className="mt-1 flex items-center gap-1 text-[10px] leading-tight text-text-muted">
                  <CornerDownRight className="h-2.5 w-2.5 shrink-0" />
                  <span className="truncate">
                    {selected?.data.index ? `after ${selected.data.index}` : "at the end"}
                  </span>
                </div>
              </div>
              {short && PALETTE.map(({ kind, label, icon, blurb }, i) => {
                const Icon = ICONS[icon];
                const theme = THEME[icon];
                return (
                  <div key={kind}>
                    {/* the steps, then the people (human in the loop), then the shape of the flow */}
                    {(i === 5 || i === 7) && <div className="mx-1 my-1 border-t border-border-subtle" />}
                    <button
                      onClick={() => addPaletteItem(kind)}
                      title={blurb}
                      className={cn(
                        "group flex w-full flex-col items-center gap-0.5 rounded-lg border border-transparent px-1 py-1.5",
                        "text-text-secondary transition-all hover:-translate-y-px hover:border-border-subtle hover:bg-bg-muted/60 hover:text-text-primary",
                      )}
                    >
                      <span className={cn("inline-flex h-6 w-6 items-center justify-center rounded-md border transition-transform group-hover:scale-110", theme.chip)}>
                        <Icon className="h-3.5 w-3.5" />
                      </span>
                      <span className="text-[11px] font-medium">{label}</span>
                    </button>
                  </div>
                );
              })}
              {!short && VERB_INFO.map(({ verb, label, blurb }) => {
                const Icon = ICONS[verb];
                const theme = THEME[verb];
                return (
                  <button
                    key={verb}
                    onClick={() => addNode(verb)}
                    title={blurb}
                    className={cn(
                      "group flex flex-col items-center gap-1 rounded-lg border border-transparent px-1 py-2",
                      "text-text-secondary transition-all hover:-translate-y-px hover:border-border-subtle hover:bg-bg-muted/60 hover:text-text-primary",
                    )}
                  >
                    <span className={cn("inline-flex h-7 w-7 items-center justify-center rounded-md border transition-transform group-hover:scale-110", theme.chip)}>
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <span className="text-[11px] font-medium">{label}</span>
                  </button>
                );
              })}
              {mode === "none" && nodes.length === 0 && (
                <p className="px-1 pt-2 text-[10px] leading-relaxed text-text-muted">
                  No steps yet. Click one to add the first.
                </p>
              )}
            </aside>
          )}
          {!editable && (
            <aside className="w-[96px] shrink-0 glass-card p-3 text-[11px] leading-relaxed text-text-muted">
              <FileCode2 className="h-4 w-4 mb-2 text-text-secondary" />
              Read-only outline of a Python (@flow) skill.
            </aside>
          )}

          {/* canvas */}
          <div ref={paneRef} className="flex-1 min-w-0 glass-card overflow-hidden relative">
            <ReactFlow
              onInit={setRf}
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              nodeTypes={NODE_TYPES}
              onNodeClick={(_, n) => { setSelectedId(n.id); setPanelTab("step"); }}
              onPaneClick={() => setSelectedId(null)}
              // One restore point per drag, taken on grab — not per frame.
              onNodeDragStart={() => commit()}
              // React Flow removes the node; we still have to heal the chain.
              onNodesDelete={(deleted) => { commit(); dropNodes(deleted.map((n) => n.id)); }}
              onEdgesDelete={() => commit()}
              deleteKeyCode={["Delete", "Backspace"]}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={18} size={1.2} color="rgb(var(--text-muted) / 0.22)" />
              <Controls className="!bg-bg-elevated !border-border-subtle !rounded-lg !shadow-card" />
              <MiniMap
                pannable
                zoomable
                className="!bg-bg-elevated/90 !border !border-border-subtle !rounded-lg !shadow-card"
                maskColor="rgb(var(--bg-base) / 0.55)"
                nodeColor={() => "rgb(var(--brand) / 0.45)"}
                nodeStrokeColor={() => "transparent"}
              />
            </ReactFlow>
            {editable && nodes.length > 0 && (
              <div className="pointer-events-none absolute right-3 top-3 hidden items-center gap-2.5 rounded-lg border border-border-subtle bg-bg-surface/70 px-2.5 py-1.5 text-[11px] text-text-muted backdrop-blur-md lg:flex">
                <span className="flex items-center gap-1"><Kbd>{MOD}S</Kbd> save</span>
                <span className="flex items-center gap-1"><Kbd>{MOD}Z</Kbd> undo</span>
                <span className="flex items-center gap-1"><Kbd>{MOD}D</Kbd> duplicate</span>
                <span className="flex items-center gap-1"><Kbd>⌫</Kbd> delete</span>
              </div>
            )}
            {nodes.length === 0 && name && (
              <div className="pointer-events-none absolute inset-0 grid place-items-center">
                <div className="text-center animate-fade-up">
                  <Sparkles className="mx-auto h-6 w-6 text-brand/70" />
                  <p className="mt-2 text-sm font-medium text-text-secondary">Blank canvas</p>
                  <p className="mt-1 text-xs text-text-muted max-w-[260px]">
                    Add steps from the rail on the left — they chain top-to-bottom
                    into a deterministic flow.
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* right panel: Step | YAML */}
          <aside className="w-[366px] shrink-0 glass-card flex flex-col overflow-hidden">
            <div className="flex items-center gap-0.5 border-b border-border-subtle p-2">
              <PanelTab active={panelTab === "step"} onClick={() => setPanelTab("step")} icon={LayoutList} label="Step" />
              <PanelTab active={panelTab === "verify"} onClick={() => setPanelTab("verify")} icon={ShieldCheck} label="Verify" />
              <PanelTab active={panelTab === "guides"} onClick={() => setPanelTab("guides")} icon={ScrollText} label="Guides" />
              <PanelTab active={panelTab === "yaml"} onClick={() => setPanelTab("yaml")} icon={FileCode2} label="Source" />
              {selected && editable && panelTab === "step" && (
                <div className="ml-auto flex items-center">
                  <button
                    onClick={() => duplicateNode(selected.id)}
                    title="Duplicate step"
                    className="rounded-md p-1.5 text-text-secondary hover:text-text-primary hover:bg-bg-muted transition-colors"
                  >
                    <Copy className="h-3.5 w-3.5" />
                  </button>
                  <button
                    onClick={() => deleteNode(selected.id)}
                    title="Delete step"
                    className="rounded-md p-1.5 text-text-secondary hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
            </div>

            <div className="flex-1 overflow-y-auto scrollbar-thin">
              {panelTab === "verify" ? (
                name ? (
                  <VerifyPanel
                    key={name}
                    skill={name}
                    fields={Object.keys(skills.find((s) => s.name === name)?.fields ?? {})}
                  />
                ) : null
              ) : panelTab === "guides" ? (
                <GuidelinesPanel md={md} editable={editable} onApply={setMd} />
              ) : panelTab === "yaml" ? (
                <div className="p-4">
                  <pre className="text-[12px] leading-relaxed text-text-secondary whitespace-pre-wrap font-mono">{yaml}</pre>
                  <p className="mt-3 text-[11px] text-text-muted leading-relaxed">
                    Live view of the <code className="text-brand">{short ? "steps:" : "flow:"}</code> block that Save writes into
                    SKILL.md. The server's fail-closed loader validates it — an unknown word, a tool that
                    changes something with no confirm above it, or a name nothing asks for is rejected with the reason.
                  </p>
                </div>
              ) : selected?.data.step && editable && short && isHeader(selected.data.step) ? (
                <BlockPanel
                  key={selected.id}
                  step={selected.data.step}
                  index={selected.data.index}
                  issues={selectedIssues}
                  tools={tools}
                  above={namesAbove(steps, selected.data.index ?? steps.length + 1)}
                  onPatchBlock={(patch) => patchBlock(selected.id, patch)}
                  onRemoveBlock={() => deleteNode(selected.id)}
                />
              ) : selected?.data.step && editable && short ? (
                <ShortStepPanel
                  key={selected.id}
                  block={(() => {
                    const inside = selected.data.step?._in as string | undefined;
                    return inside ? blockOf(nodes.find((n) => blockOf(n.data.step)?.id === inside)?.data.step) : undefined;
                  })()}
                  onLeaveBlock={selected.data.step?._in ? () => leaveBlock(selected.id) : undefined}
                  step={selected.data.step}
                  index={selected.data.index}
                  issues={selectedIssues}
                  tools={tools}
                  skills={skills.map((sk) => sk.name).filter((n) => n !== name)}
                  catalog={widgetCatalog}
                  above={namesAbove(steps, selected.data.index ?? steps.length + 1)}
                  confirmed={confirmAbove(steps, selected.data.index ?? steps.length + 1)}
                  fields={check?.ok ? check.fields : {}}
                  onPatch={(patch) => patchStep(selected.id, patch)}
                  onInsertAbove={(step) => insertAbove(selected, step)}
                  onOpenWidget={(id) => { if (confirmLeave()) navigate(`/widgets/${encodeURIComponent(id)}`); }}
                  confirmLeave={confirmLeave}
                />
              ) : selected?.data.step && editable ? (
                <StepPanel
                  key={selected.id}
                  step={selected.data.step}
                  index={selected.data.index}
                  issues={selectedIssues}
                  catalog={widgetCatalog}
                  otherSkills={skills.map((s) => s.name).filter((n) => n !== name)}
                  onPatch={(p) => patchStep(selected.id, p)}
                  onOpenWidget={(id) => { if (confirmLeave()) navigate(`/widgets/${encodeURIComponent(id)}`); }}
                  confirmLeave={confirmLeave}
                />
              ) : selected?.data.step ? (
                <pre className="p-4 text-[12px] text-text-secondary whitespace-pre-wrap font-mono">
                  {JSON.stringify(selected.data.step, null, 2)}
                </pre>
              ) : (
                <div className="p-4 text-xs text-text-muted leading-relaxed">
                  {editable
                    ? "Select a node to edit it — or add a step from the left rail. Drag between handles to re-chain the order."
                    : "Select a node to inspect it. Edit this skill's Python in the code editor."}
                </div>
              )}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-border-subtle bg-bg-muted/70 px-1 py-px font-sans text-[10px] font-medium text-text-secondary">
      {children}
    </kbd>
  );
}

/** Preflight findings for the selected step, above its fields. */
function IssueList({ issues }: { issues: Issue[] }) {
  if (issues.length === 0) return null;
  return (
    <div className="border-b border-border-subtle px-4 py-3 space-y-1.5">
      {issues.map((issue, i) => (
        <div
          key={i}
          className={cn(
            "flex items-start gap-1.5 text-[12px] leading-relaxed",
            issue.level === "error" ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400",
          )}
        >
          <AlertCircle className="mt-[3px] h-3 w-3 shrink-0" />
          <span>{issue.message}</span>
        </div>
      ))}
    </div>
  );
}

function PanelTab({ active, onClick, icon: Icon, label }: {
  active: boolean; onClick: () => void; icon: React.ComponentType<{ className?: string }>; label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
        active ? "bg-bg-muted text-text-primary" : "text-text-secondary hover:text-text-primary",
      )}
    >
      <Icon className="h-3 w-3" /> {label}
    </button>
  );
}

function EmptyHero({ skills, onPick, onNew }: {
  skills: SkillListItem[]; onPick: (name: string) => void; onNew: () => void;
}) {
  return (
    <div className="flex-1 grid place-items-center p-8">
      <div className="max-w-lg text-center animate-fade-up">
        <div className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-brand/30 bg-brand-subtle">
          <Sparkles className="h-5 w-5 text-brand" />
        </div>
        <h2 className="mt-4 text-xl font-bold tracking-tight">
          Design a <span className="grad-text">deterministic flow</span>
        </h2>
        <p className="mt-2 text-sm text-text-secondary leading-relaxed">
          Ask, say, run a tool, confirm, chat — chained on a canvas, with conditions,
          lists of items and other people's approvals as nodes of their own. The LLM interprets each
          user message; your flow decides what happens. Pick a skill to start:
        </p>
        <div className="mt-5 flex flex-wrap justify-center gap-2">
          {skills.slice(0, 8).map((s) => (
            <button
              key={s.name}
              onClick={() => onPick(s.name)}
              title={s.description}
              className="rounded-full border border-border bg-bg-surface px-3 py-1.5 text-xs text-text-secondary transition-all hover:border-brand/50 hover:text-text-primary hover:-translate-y-px hover:shadow-glow/40"
            >
              {s.name}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={onNew}
          className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-brand/40 bg-brand-subtle px-3 py-1.5 text-xs font-medium text-brand transition-colors hover:bg-brand/20 focus-ring"
        >
          <Plus className="h-3.5 w-3.5" /> Start a new skill
        </button>
        <p className="mt-4 text-xs text-text-muted">
          New here? Describe a skill on the{" "}
          <Link to="/workspace" className="text-brand hover:text-brand-hover">Workspace</Link>, or
          install an example from the{" "}
          <Link to="/gallery" className="text-brand hover:text-brand-hover">Gallery</Link>.
        </p>
      </div>
    </div>
  );
}

function BindingWizard({ md, tools, onApply, onError }: {
  md: string; tools: ManagedTool[]; onApply: (md: string) => void; onError: (message: string) => void;
}) {
  const parsed = parseSkillMd(md); const fm = parsed.fm;
  const [toolName, setToolName] = useState(tools[0]?.name ?? "");
  const tool = tools.find((item) => item.name === toolName) ?? tools[0];
  const [usage, setUsage] = useState<"action" | "chat">("action");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const fields = fm.fields ?? {};

  useEffect(() => {
    if (!tool) return;
    const next: Record<string, string> = {};
    for (const [parameter, spec] of Object.entries(tool.parameters)) {
      const match = Object.entries(fields).find(([, field]: [string, any]) => {
        const actual = field.type;
        if (spec.type === "string") return actual === "enum" || (actual === "string" && (field.pattern || field.values));
        if (spec.type === "number") return actual === "number" || actual === "integer";
        return actual === spec.type;
      });
      next[parameter] = match?.[0] ?? parameter;
    }
    setMapping(next);
    if (tool.side_effecting) setUsage("action");
  }, [toolName]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!Array.isArray(fm.flow)) {
    return <div className="mx-6 mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-700 dark:text-amber-300">
      Managed binding is declarative-only. This skill uses handlers.py and remains compatible, but the Canvas will not auto-convert imperative code.
    </div>;
  }
  if (!tool) return null;

  const bind = () => {
    try {
      const next = structuredClone(fm);
      next.fields = next.fields ?? {};
      const asks: SkillStep[] = [];
      for (const [parameter, spec] of Object.entries(tool.parameters)) {
        const fieldName = mapping[parameter]?.trim();
        if (!fieldName || !/^[A-Za-z_]\w*$/.test(fieldName)) throw new Error(`Invalid field mapping for ${parameter}`);
        if (fieldName === "flow_run_id") {
          if (spec.type !== "string") throw new Error("flow_run_id can only bind a string parameter");
          continue;
        }
        if (!next.fields[fieldName]) {
          if (spec.type === "object" || spec.type === "array") throw new Error(`${spec.type} inputs need an existing compatible skill field`);
          next.fields[fieldName] = {
            type: spec.enum?.length ? "enum" : spec.type,
            ...(spec.enum?.length ? { values: spec.enum } : {}),
            ...(spec.type === "string" && !spec.enum?.length ? { pattern: spec.pattern || "^.{1,200}$" } : {}),
            description: `Value bound to ${tool.display_name} input ${parameter}.`,
          };
          asks.push({ ask: fieldName });
        }
      }
      const allowed = new Set(String(next["allowed-tools"] ?? "").split(/[,\s]+/).filter(Boolean));
      allowed.add(tool.name); next["allowed-tools"] = [...allowed].join(", ");
      const pin = Object.fromEntries(Object.entries(mapping));
      if (usage === "chat") {
        const last = next.flow.at(-1);
        if (last?.chat) {
          last.chat.tools = [...new Set([...(last.chat.tools ?? []), tool.name])];
          last.chat.pin = { ...(last.chat.pin ?? {}), ...pin };
        } else next.flow.push(...asks, { chat: { tools: [tool.name], pin, greeting: `Ask about ${tool.display_name}.` } });
      } else {
        const actionName = tool.name.split("__").at(-1) || "managed_operation";
        next.actions = next.actions ?? {};
        const args = Object.fromEntries(Object.entries(mapping).map(([key, value]) => [key, `{{${value}}}`]));
        const action: Record<string, unknown> = { tool: tool.name, args };
        const actionSteps: SkillStep[] = [...asks];
        if (tool.side_effecting) {
          const confirm = `confirm_${actionName}`;
          next.fields[confirm] = next.fields[confirm] ?? { type: "boolean", description: `User explicitly confirms ${tool.display_name}.` };
          action.requires = confirm; action.audit = true; action.idempotency_key = "{{flow_run_id}}";
          actionSteps.push({ say: `Please confirm ${tool.display_name}.` }, { ask: confirm }, { when: confirm, do: actionName });
        } else actionSteps.push({ do: actionName });
        next.actions[actionName] = action;
        const chatIndex = next.flow.findIndex((step: SkillStep) => step.chat);
        next.flow.splice(chatIndex < 0 ? next.flow.length : chatIndex, 0, ...actionSteps);
      }
      onApply(replaceFrontmatter(md, next));
    } catch (cause) { onError(cause instanceof Error ? cause.message : String(cause)); }
  };

  return <div className="mx-6 mt-3 rounded-xl border border-brand/30 bg-brand/[0.06] p-3">
    <div className="flex items-center gap-3">
      <Link2 className="h-4 w-4 text-brand" /><span className="text-xs font-medium">Managed operation binding</span>
      <select className="ml-2 rounded-md border border-border bg-bg-muted px-2 py-1 text-xs" value={toolName} onChange={(event) => setToolName(event.target.value)}>{tools.map((item) => <option key={item.name} value={item.name}>{item.display_name} ({item.mode})</option>)}</select>
      {!tool.side_effecting ? <select className="rounded-md border border-border bg-bg-muted px-2 py-1 text-xs" value={usage} onChange={(event) => setUsage(event.target.value as "action" | "chat")}><option value="action">Deterministic do action</option><option value="chat">Allowlisted chat read</option></select> : <span className="rounded bg-amber-500/10 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300">Confirmation required</span>}
      <div className="ml-auto flex gap-2">{Object.entries(tool.parameters).map(([parameter, spec]) => <label key={parameter} className="text-[11px] text-text-muted">{parameter}: {spec.type}<input value={mapping[parameter] ?? ""} onChange={(event) => setMapping((current) => ({ ...current, [parameter]: event.target.value }))} className="ml-1 w-28 rounded border border-border bg-bg-muted px-1.5 py-1 font-mono text-[11px] text-text-primary" /></label>)}</div>
      <button onClick={bind} className="rounded-lg bg-brand px-3 py-1.5 text-xs text-brand-fg">Add binding</button>
    </div>
    <p className="mt-2 text-[11px] text-text-muted">Save commits allowed-tools, typed fields, action/chat inputs, and guarded flow steps together. Writes add a boolean confirmation and flow-run idempotency binding.</p>
  </div>;
}

// --------------------------------------------------------------------------
/** Natural-language behavior rules, edited as cards.
 *
 *  Guidelines steer what the model SAYS (interpreter replies, chat/assist
 *  answers) — they are followed, not engine-enforced. Anything that must
 *  HOLD stays in the flow: gates, actions, widgets. Edits land in the
 *  `guidelines:` frontmatter block; Save runs the same fail-closed verifier
 *  and versions the skill in the workspace store like any other change. */
type Guideline = { when?: string; then: string; tools?: string[] };

function GuidelinesPanel({ md, editable, onApply }: {
  md: string | null; editable: boolean; onApply: (next: string) => void;
}) {
  const parsed = useMemo(() => {
    if (!md) return { list: [] as Guideline[], allowed: [] as string[] };
    try {
      const { fm } = parseSkillMd(md);
      const raw = Array.isArray(fm.guidelines) ? fm.guidelines : [];
      const list: Guideline[] = raw.map((g: any) =>
        typeof g === "string" ? { then: g } : { when: g.when, then: g.then ?? "", tools: g.tools },
      );
      const allowed = String(fm["allowed-tools"] ?? fm.allowed_tools ?? "")
        .split(/[,\s]+/).filter(Boolean);
      return { list, allowed };
    } catch {
      return { list: [], allowed: [] };
    }
  }, [md]);

  const [items, setItems] = useState<Guideline[]>(parsed.list);
  useEffect(() => setItems(parsed.list), [parsed]);

  /** Serialize the cards back into the frontmatter (bare-string shorthand
   *  when a rule is unconditional and tool-less). */
  const push = (next: Guideline[]) => {
    setItems(next);
    if (!md) return;
    try {
      const { fm } = parseSkillMd(md);
      const serial = next
        .filter((g) => g.then.trim())
        .map((g) => {
          const hasWhen = Boolean(g.when?.trim());
          const hasTools = Boolean(g.tools?.length);
          if (!hasWhen && !hasTools) return g.then.trim();
          const out: any = { then: g.then.trim() };
          if (hasWhen) out.when = g.when!.trim();
          if (hasTools) out.tools = g.tools;
          return out;
        });
      if (serial.length) fm.guidelines = serial;
      else delete fm.guidelines;
      onApply(replaceFrontmatter(md, fm));
    } catch { /* malformed md — leave it untouched */ }
  };

  const patch = (i: number, p: Partial<Guideline>) =>
    setItems((cur) => cur.map((g, j) => (j === i ? { ...g, ...p } : g)));
  const toggleTool = (i: number, tool: string) => {
    const cur = items[i]?.tools ?? [];
    const next = cur.includes(tool) ? cur.filter((t) => t !== tool) : [...cur, tool];
    const updated = items.map((g, j) => (j === i ? { ...g, tools: next } : g));
    push(updated);
  };

  if (!editable) {
    return (
      <div className="p-4 text-xs text-text-muted leading-relaxed">
        Guidelines for Python (@flow) skills are edited in code — add a
        <code className="mx-1 text-brand">guidelines:</code> block to the SKILL.md frontmatter.
      </div>
    );
  }

  return (
    <div className="p-3.5 space-y-3">
      <p className="text-[12px] leading-relaxed text-text-secondary">
        Natural-language rules the agent follows in <em>every</em> reply — tone,
        language, escalation, policy phrasing. The model follows these; the flow's
        gates and actions stay engine-enforced regardless.
      </p>

      {items.map((g, i) => (
        <div key={i} className="rounded-xl border border-border-subtle bg-bg-muted/40 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
              Rule {i + 1}
            </span>
            <button
              onClick={() => push(items.filter((_, j) => j !== i))}
              title="Delete rule"
              className="rounded-md p-1 text-text-muted hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
          <label className="block">
            <span className="text-[11px] text-text-muted">When (blank = always)</span>
            <input
              value={g.when ?? ""}
              onChange={(e) => patch(i, { when: e.target.value })}
              onBlur={() => push(items)}
              placeholder="e.g. the user sounds frustrated"
              className="field mt-0.5"
            />
          </label>
          <label className="block">
            <span className="text-[11px] text-text-muted">Then</span>
            <textarea
              value={g.then}
              onChange={(e) => patch(i, { then: e.target.value })}
              onBlur={() => push(items)}
              rows={2}
              placeholder="e.g. apologize once, then offer a human handoff"
              className="field mt-0.5 resize-y"
            />
          </label>
          {parsed.allowed.length > 0 && (
            <div>
              <span className="text-[11px] text-text-muted">Relevant read-tools (optional)</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {parsed.allowed.map((t) => (
                  <button
                    key={t}
                    onClick={() => toggleTool(i, t)}
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px] font-mono transition-colors",
                      g.tools?.includes(t)
                        ? "border-brand/50 bg-brand-subtle text-brand"
                        : "border-border-subtle text-text-muted hover:text-text-secondary",
                    )}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}

      <button
        onClick={() => push([...items, { then: "" }])}
        className="flex w-full items-center justify-center gap-1.5 rounded-xl border border-dashed border-border py-2 text-xs text-text-secondary hover:border-brand/50 hover:text-brand transition-colors"
      >
        <Plus className="h-3.5 w-3.5" /> Add guideline
      </button>

      <p className="text-[11px] leading-relaxed text-text-muted">
        Saved into the <code className="text-brand">guidelines:</code> block of SKILL.md —
        validated on Save (tools must be allow-listed reads), versioned in History,
        live in <span className="font-medium">Flow</span> immediately. For a
        guideline-driven agent with no fixed steps, use a flow with a single{" "}
        <code className="text-brand">chat:</code> step.
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------
/** What UI this step produces in chat — the Canvas→Widgets link. */
function RenderingInfo({ step, onOpenWidget }: { step: SkillStep; onOpenWidget: (id: string) => void }) {
  const v = verbOf(step);
  if (!["ask", "pick", "show", "widget"].includes(v)) return null;

  const widgetId: string | undefined = step.widget;
  const body =
    v === "ask"
      ? "Auto-widget from the field spec: enum → select, string/number/date → typed input, boolean → confirm. Defined in the skill's fields: block."
      : v === "show"
        ? "Auto-widget from the data's shape: list of records → data table, dated items → timeline, single record → card."
        : widgetId
          ? "Renders your catalog widget — every visual detail (typography, colors, layout) is authored in the designer."
          : `Built-in ${step.as ?? "datatable"} data widget. Set a widget id to use a custom design instead.`;

  return (
    <div className="pt-3">
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted border-t border-border-subtle pt-3">
        <Boxes className="h-3 w-3" /> Renders in chat
      </div>
      <p className="mt-1 text-[11px] text-text-muted leading-relaxed">{body}</p>
      {widgetId && (
        <button
          onClick={() => onOpenWidget(widgetId)}
          className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-brand/30 bg-brand-subtle px-2.5 py-1.5 text-[12px] font-medium text-brand hover:bg-brand/20 transition-colors focus-ring"
        >
          <ExternalLink className="h-3 w-3" /> Open “{widgetId}” in the designer
        </button>
      )}
    </div>
  );
}

function StepPanel({ step, index, issues, catalog, otherSkills, onPatch, onOpenWidget, confirmLeave }: {
  step: SkillStep; index?: number; issues: Issue[]; catalog: string[]; otherSkills: string[];
  onPatch: (p: SkillStep) => void; onOpenWidget: (id: string) => void;
  confirmLeave: () => boolean;
}) {
  const v = verbOf(step);
  const info = VERB_INFO.find((i) => i.verb === v);
  const Icon = ICONS[v as keyof typeof ICONS];
  const theme = THEME[v as keyof typeof THEME] ?? THEME.say;
  return (
    <div>
      <div className="px-4 pt-4 pb-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <span className={cn("inline-flex h-7 w-7 items-center justify-center rounded-md border", theme.chip)}>
            {Icon && <Icon className="h-4 w-4" />}
          </span>
          <span className={cn("text-xs font-bold uppercase tracking-[0.14em]", theme.text)}>{v}</span>
          {index !== undefined && (
            <span className="ml-auto text-[11px] text-text-muted tabular-nums">step {index}</span>
          )}
        </div>
        {info && <p className="mt-2 text-[12px] leading-relaxed text-text-muted">{info.blurb}</p>}
      </div>

      <IssueList issues={issues} />

      <div className="p-4 space-y-2.5">
        {v === "ask" && <Text label="field" value={step.ask} onChange={(x) => onPatch({ ask: x })} />}
        {v === "say" && <Area label="text" value={step.say} onChange={(x) => onPatch({ say: x })} />}
        {v === "do" && (
          <>
            <Text label="action" value={step.do ?? step.run} onChange={(x) => onPatch({ do: x })} />
            {/* Said once, when the action actually fires. May quote its result: {{action.key}}. */}
            <Area label="when done, say" value={step.then_say} onChange={(x) => onPatch({ then_say: x })} />
          </>
        )}
        {v === "show" && (
          <>
            <Text label="value binding" value={step.show} onChange={(x) => onPatch({ show: x })} ph="{{action.result}}" />
            <Text label="title" value={step.title} onChange={(x) => onPatch({ title: x })} />
          </>
        )}
        {v === "pick" && (
          <>
            <Text label="rows binding" value={step.pick} onChange={(x) => onPatch({ pick: x })} ph="{{action.rows}}" />
            <Csv label="save fields" value={step.save} onChange={(x) => onPatch({ save: x })} />
            <Sel label="render as" value={step.widget ? "widget" : (step.as ?? "datatable")}
                 opts={["datatable", "autosuggest", "widget"]}
                 onChange={(x) => x === "widget"
                   ? onPatch({ as: undefined, widget: step.widget || (catalog[0] ?? "widget_id") })
                   : onPatch({ widget: undefined, as: x === "datatable" ? undefined : x })} />
            {step.widget != null && (
              <WidgetSel value={step.widget} catalog={catalog} onChange={(x) => onPatch({ widget: x })} confirmLeave={confirmLeave} />
            )}
            <Text label="prompt" value={step.prompt} onChange={(x) => onPatch({ prompt: x })} />
          </>
        )}
        {v === "check" && (
          <>
            <Text label="condition" value={step.check} onChange={(x) => onPatch({ check: x })} ph="action.valid" />
            <Area label="on failure, say" value={step.else_say} onChange={(x) => onPatch({ else_say: x })} />
            <Csv label="then re-collect" value={step.else_clear} onChange={(x) => onPatch({ else_clear: x })} />
          </>
        )}
        {v === "chat" && (
          <>
            <Csv label="tools" value={step.chat?.tools}
                 onChange={(x) => onPatch({ chat: { ...step.chat, tools: x } })} />
            <Text label="pin (arg=field, comma-sep)"
                  value={Object.entries(step.chat?.pin ?? {}).map(([k, val]) => `${k}=${val}`).join(", ")}
                  onChange={(x) => {
                    const pin: Record<string, string> = {};
                    for (const pair of x.split(",")) {
                      const [k, val] = pair.split("=").map((s) => s.trim());
                      if (k && val) pin[k] = val;
                    }
                    onPatch({ chat: { ...step.chat, pin } });
                  }} ph="account_number=account_number" />
            <Area label="greeting" value={step.chat?.greeting}
                  onChange={(x) => onPatch({ chat: { ...step.chat, greeting: x } })} />
          </>
        )}
        {v === "link" && (
          <>
            {/* The other skills of this agent; a name typed in the file that is not one of them is kept as it is. */}
            <Sel label="skill to hand off to" value={String(step.link ?? "")}
                 opts={[...new Set([String(step.link ?? ""), ...otherSkills])].filter(Boolean)}
                 onChange={(x) => onPatch({ link: x })} />
            <Area label="when it has done its job, say" value={step.then_say} onChange={(x) => onPatch({ then_say: x })} />
            <Area label="if it was cancelled or declined, say (this flow then stops)" value={step.else_say} onChange={(x) => onPatch({ else_say: x })} />
          </>
        )}
        {v === "widget" && (
          <>
            <WidgetSel value={step.widget} catalog={catalog} onChange={(x) => onPatch({ widget: x })} confirmLeave={confirmLeave} />
            <Csv label="save fields (must exist in fields:)" value={step.save} onChange={(x) => onPatch({ save: x })} />
            <JsonArea label="data bindings (JSON → widget {{slots}})" value={step.data}
                      onChange={(x) => onPatch({ data: x })} ph='{"rows": "{{lookup.rows}}"}' />
            <Text label="prompt" value={step.prompt} onChange={(x) => onPatch({ prompt: x })} />
          </>
        )}

        <RenderingInfo step={step} onOpenWidget={onOpenWidget} />

        {/* guards available on every verb */}
        <div className="pt-3">
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted border-t border-border-subtle pt-3">
            Guards
          </div>
          <p className="mt-1 mb-2 text-[11px] text-text-muted leading-relaxed">
            Evaluated on every replay — steps skip themselves when a guard says so.
          </p>
          <div className="space-y-2.5">
            <Text label="when_missing (fast-path guard)" value={step.when_missing}
                  onChange={(x) => onPatch({ when_missing: x })} ph="account_number" />
            <Text label="when (condition guard)" value={step.when} onChange={(x) => onPatch({ when: x })} ph="requires_2fa" />
          </div>
        </div>
      </div>
    </div>
  );
}

/** JSON editor for a step's `data:` bindings — kept as text until it parses. */
function JsonArea({ label, value, onChange, ph }: {
  label: string; value?: Record<string, any>; onChange: (v: Record<string, any> | undefined) => void; ph?: string;
}) {
  const [text, setText] = useState(() => (value ? JSON.stringify(value, null, 2) : ""));
  const [err, setErr] = useState<string | null>(null);
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <textarea
        value={text}
        rows={3}
        placeholder={ph}
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          if (!t.trim()) { onChange(undefined); setErr(null); return; }
          try {
            onChange(JSON.parse(t));
            setErr(null);
          } catch (x) {
            setErr((x as Error).message);
          }
        }}
        className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs font-mono resize-y focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand transition-colors"
      />
      {err && <span className="mt-1 block text-[11px] text-rose-600 dark:text-rose-400">{err}</span>}
    </label>
  );
}

/** Catalog dropdown for `widget:` references — the Canvas→Widgets link as a
 *  picker instead of a hand-typed id. Unknown ids (edited on disk) stay
 *  selectable so nothing is silently rewritten. */
function WidgetSel({ value, catalog, onChange, confirmLeave }: {
  value?: string; catalog: string[]; onChange: (v: string) => void; confirmLeave: () => boolean;
}) {
  const options = catalog.includes(value ?? "") || !value ? catalog : [value, ...catalog];
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">widget (from catalog)</span>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand transition-colors"
      >
        {!value && <option value="">— choose a widget —</option>}
        {options.map((id) => (
          <option key={id} value={id}>{id}</option>
        ))}
      </select>
      <span className="mt-1 block text-[11px] text-text-muted">
        {catalog.length === 0 ? "No widgets in the catalog yet — " : "Not seeing yours? "}
        <Link
          to="/widgets/new"
          // A modified click does not navigate this tab, so there is nothing to confirm.
          onClick={(e) => { if (!(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) && !confirmLeave()) e.preventDefault(); }}
          className="text-brand hover:text-brand-hover"
        >
          design a new widget →
        </Link>
      </span>
    </label>
  );
}

function Text({ label, value, onChange, ph }: { label: string; value?: string; onChange: (v: string) => void; ph?: string }) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <input
        type="text"
        value={value ?? ""}
        placeholder={ph}
        onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand transition-colors"
      />
    </label>
  );
}

function Area({ label, value, onChange }: { label: string; value?: string; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <textarea
        value={value ?? ""}
        rows={2}
        onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand transition-colors"
      />
    </label>
  );
}

function Csv({ label, value, onChange }: { label: string; value?: string[]; onChange: (v: string[]) => void }) {
  return (
    <Text
      label={label}
      value={(value ?? []).join(", ")}
      onChange={(x) => onChange(x.split(",").map((s) => s.trim()).filter(Boolean))}
    />
  );
}

function Sel({ label, value, opts, onChange }: { label: string; value: string; opts: string[]; onChange: (v: string) => void }) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand transition-colors"
      >
        {opts.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </label>
  );
}

/** Start a skill on the canvas: a name, one line of what it is for, and a
 *  first step. The server validates it like any save. */
function NewSkillDialog({ open, taken, onClose, onCreated }: {
  open: boolean; taken: string[]; onClose: () => void; onCreated: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => { if (open) { setName(""); setDescription(""); setProblem(null); } }, [open]);

  const slug = name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  const clash = taken.includes(slug);
  const ready = slug.length > 0 && description.trim().length >= 20 && !clash && !busy;

  const create = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const r = await fetch("/api/flow/skills", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name: slug, skill_md: starterSkill(slug, description.trim()) }),
      });
      if (!r.ok) {
        const text = await r.text();
        let detail = text;
        try { detail = String(JSON.parse(text).detail ?? text); } catch { /* shown as it came */ }
        throw new Error(detail);
      }
      onCreated(slug);
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppDialog open={open} onOpenChange={(next) => { if (!next) onClose(); }} className="max-w-md">
      <div className="px-6 py-4 border-b border-border-subtle">
        <AppDialogTitle className="text-sm font-semibold text-text-primary">Start a new skill</AppDialogTitle>
        <AppDialogDescription className="mt-0.5 text-xs text-text-muted">
          It opens on the canvas with one step. Add the rest there: ask, say, do, confirm.
        </AppDialogDescription>
      </div>
      <div className="p-6 space-y-3">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-text-muted">Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="book a table" autoFocus
                 className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand" />
          <span className="mt-1 block text-[11px] text-text-muted">
            {slug ? <>Saved as <code className="font-mono">{slug}</code>{clash ? " — a skill of that name already exists." : "."}</> : "Lowercase words; spaces become hyphens."}
          </span>
        </label>
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-text-muted">What it is for, and when to use it</span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
                    placeholder="Book a restaurant table. Use when the user wants to reserve a table or book dinner."
                    className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand" />
          <span className="mt-1 block text-[11px] text-text-muted">
            The agent reads this to decide which skill a message is for, so say when to use it.
          </span>
        </label>
        {problem && (
          <p className="rounded-md border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-700 dark:text-rose-300">{problem}</p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={create} disabled={!ready}>{busy ? "Creating…" : "Create and open"}</Button>
        </div>
      </div>
    </AppDialog>
  );
}

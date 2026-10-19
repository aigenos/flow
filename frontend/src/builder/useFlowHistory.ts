/**
 * Undo/redo for the canvas.
 *
 * History is snapshot-based and *explicit*: callers `commit()` immediately
 * before a mutation, which pushes the pre-change state. That keeps drag noise
 * out of the stack — a drag records one entry on drop, not one per frame.
 *
 * Bursty edits (typing in the inspector) coalesce: consecutive commits with
 * the same tag inside COALESCE_MS refresh the window instead of stacking, so
 * one undo takes back a whole word rather than one character.
 */
import { useCallback, useRef, useState } from "react";
import type { Edge, Node } from "reactflow";
import type { WfNodeData } from "./nodes";

type Snapshot = { nodes: Node<WfNodeData>[]; edges: Edge[] };

const LIMIT = 120;
const COALESCE_MS = 700;

export type FlowHistory = {
  /** Record the current state as a restore point, before mutating it. */
  commit: (tag?: string) => void;
  undo: () => void;
  redo: () => void;
  /** Drop all history — call after loading a different skill. */
  reset: () => void;
  canUndo: boolean;
  canRedo: boolean;
};

export function useFlowHistory(
  nodes: Node<WfNodeData>[],
  edges: Edge[],
  setNodes: (n: Node<WfNodeData>[]) => void,
  setEdges: (e: Edge[]) => void,
): FlowHistory {
  const past = useRef<Snapshot[]>([]);
  const future = useRef<Snapshot[]>([]);
  const lastCommit = useRef<{ tag: string; at: number } | null>(null);
  // History lives in refs (writes must not re-render mid-edit); this counter
  // exists only so the toolbar's enabled/disabled state stays honest.
  const [, bump] = useState(0);

  const commit = useCallback((tag = "") => {
    const now = Date.now();
    const last = lastCommit.current;
    const coalescing = tag !== "" && last?.tag === tag && now - last.at < COALESCE_MS;
    lastCommit.current = { tag, at: now };
    if (coalescing) return;

    past.current.push({ nodes, edges });
    if (past.current.length > LIMIT) past.current.shift();
    future.current = [];
    bump((n) => n + 1);
  }, [nodes, edges]);

  const undo = useCallback(() => {
    const prev = past.current.pop();
    if (!prev) return;
    future.current.push({ nodes, edges });
    setNodes(prev.nodes);
    setEdges(prev.edges);
    lastCommit.current = null;
    bump((n) => n + 1);
  }, [nodes, edges, setNodes, setEdges]);

  const redo = useCallback(() => {
    const nextState = future.current.pop();
    if (!nextState) return;
    past.current.push({ nodes, edges });
    setNodes(nextState.nodes);
    setEdges(nextState.edges);
    lastCommit.current = null;
    bump((n) => n + 1);
  }, [nodes, edges, setNodes, setEdges]);

  const reset = useCallback(() => {
    past.current = [];
    future.current = [];
    lastCommit.current = null;
    bump((n) => n + 1);
  }, []);

  return {
    commit,
    undo,
    redo,
    reset,
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
  };
}

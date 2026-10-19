import { useCallback, useEffect, useRef, useState } from "react";
import { DndContext, DragEndEvent, DragOverlay, DragStartEvent, PointerSensor, useSensor, useSensors } from "@dnd-kit/core";
import { Copy, Monitor, Redo2, Smartphone, Tablet, Trash2, Undo2 } from "lucide-react";
import { cn } from "../lib/cn";
import { Palette } from "./Palette";
import { TreeCanvas } from "./TreeCanvas";
import { Inspector } from "./Inspector";
import { Preview } from "./Preview";
import { defaultProps, PRIMITIVE_BY_TYPE } from "./primitiveMeta";
import {
  addChild, findNode, findParent, insertAt, newId, removeNode, updateNode,
  withIds, stripIds, type TreeNode,
} from "./specTree";

type Props = {
  root: TreeNode;
  onChange: (root: TreeNode) => void;
};

type Device = { key: string; label: string; icon: React.ComponentType<{ className?: string }>; width?: number };

const DEVICES: Device[] = [
  { key: "fit", label: "Fill", icon: Monitor },
  { key: "mobile", label: "375px — chat embed", icon: Smartphone, width: 375 },
  { key: "tablet", label: "640px — side panel", icon: Tablet, width: 640 },
];

/**
 * Design-tool layout: palette + tree on the left, a large direct-manipulation
 * canvas in the middle (click any element to select it), full style inspector
 * on the right. Undo/redo tracks every structural or property change.
 */
export function VisualBuilder({ root, onChange }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(root._id);
  const [dragType, setDragType] = useState<string | null>(null);
  const [device, setDevice] = useState<string>("fit");
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  // ---- undo/redo ---------------------------------------------------------
  const history = useRef<{ past: TreeNode[]; future: TreeNode[] }>({ past: [], future: [] });

  const commit = useCallback(
    (next: TreeNode) => {
      history.current.past = [...history.current.past.slice(-59), root];
      history.current.future = [];
      onChange(next);
    },
    [root, onChange],
  );

  const undo = useCallback(() => {
    const prev = history.current.past.pop();
    if (!prev) return;
    history.current.future.push(root);
    onChange(prev);
  }, [root, onChange]);

  const redo = useCallback(() => {
    const next = history.current.future.pop();
    if (!next) return;
    history.current.past.push(root);
    onChange(next);
  }, [root, onChange]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  // ---- tree ops ----------------------------------------------------------
  const selected = selectedId ? findNode(root, selectedId) : null;

  const updateProps = (id: string, props: Record<string, unknown>) => {
    commit(updateNode(root, id, (n) => ({ ...n, props })));
  };

  const deleteNode = (id: string) => {
    if (id === root._id) return;
    commit(removeNode(root, id));
    if (selectedId === id) setSelectedId(root._id);
  };

  const duplicateNode = (id: string) => {
    if (id === root._id) return;
    const node = findNode(root, id);
    const parent = findParent(root, id);
    if (!node || !parent) return;
    const clone = withIds(stripIds(node)) as TreeNode;
    const idx = (parent.children ?? []).findIndex((c) => (c as TreeNode)._id === id);
    commit(insertAt(root, parent._id, clone, idx + 1));
    setSelectedId(clone._id);
  };

  const makeNode = (t: string): TreeNode =>
    ({
      _id: newId(),
      type: t,
      props: defaultProps(t),
      children: PRIMITIVE_BY_TYPE[t]?.container ? [] : undefined,
    }) as TreeNode;

  /** Click-to-add: into the selected container, else the nearest container up
   *  the tree, else the root. */
  const quickAdd = (t: string) => {
    let target = selected;
    while (target && !PRIMITIVE_BY_TYPE[target.type]?.container) {
      target = findParent(root, target._id);
    }
    const parentId = target?._id ?? root._id;
    const node = makeNode(t);
    commit(addChild(root, parentId, node));
    setSelectedId(node._id);
  };

  const onDragStart = (ev: DragStartEvent) => {
    const data = ev.active.data.current;
    if (data?.source === "palette") setDragType(data.type);
  };

  const onDragEnd = (ev: DragEndEvent) => {
    setDragType(null);
    const overData = ev.over?.data.current;
    const activeData = ev.active.data.current;
    if (!overData || !activeData) return;
    if (activeData.source !== "palette") return;
    if (overData.type !== "container") return;

    const node = makeNode(activeData.type as string);
    commit(addChild(root, overData.parentId, node));
    setSelectedId(node._id);
  };

  const canUndo = history.current.past.length > 0;
  const canRedo = history.current.future.length > 0;
  const widthPx = DEVICES.find((d) => d.key === device)?.width;

  return (
    <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
      <div className="flex h-full gap-3 min-h-0">
        {/* left rail: palette + tree */}
        <aside className="w-60 shrink-0 flex flex-col gap-3 min-h-0">
          <div className="glass-card p-3 overflow-y-auto scrollbar-thin basis-[55%]">
            <Palette onQuickAdd={quickAdd} />
          </div>
          <div className="glass-card p-3 overflow-y-auto scrollbar-thin flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted mb-2 px-1">
              Layers
            </div>
            <TreeCanvas root={root} selectedId={selectedId} onSelect={setSelectedId} onDelete={deleteNode} />
          </div>
        </aside>

        {/* center: canvas */}
        <section className="flex-1 min-w-0 flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <div className="inline-flex rounded-lg border border-border-subtle bg-bg-muted p-0.5">
              {DEVICES.map(({ key, label, icon: Icon }) => (
                <button
                  key={key}
                  type="button"
                  title={label}
                  aria-pressed={device === key}
                  onClick={() => setDevice(key)}
                  className={cn(
                    "rounded-md px-2 py-1.5 transition-colors",
                    device === key ? "bg-bg-elevated text-brand shadow-sm" : "text-text-secondary hover:text-text-primary",
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                </button>
              ))}
            </div>
            {widthPx && <span className="text-[11px] text-text-muted">{widthPx}px</span>}

            <div className="ml-auto inline-flex items-center gap-1">
              <IconBtn title="Undo (Ctrl+Z)" onClick={undo} disabled={!canUndo}><Undo2 className="h-3.5 w-3.5" /></IconBtn>
              <IconBtn title="Redo (Ctrl+Shift+Z)" onClick={redo} disabled={!canRedo}><Redo2 className="h-3.5 w-3.5" /></IconBtn>
              <span className="mx-1 h-4 w-px bg-border-subtle" />
              <IconBtn
                title="Duplicate selected"
                onClick={() => selectedId && duplicateNode(selectedId)}
                disabled={!selectedId || selectedId === root._id}
              >
                <Copy className="h-3.5 w-3.5" />
              </IconBtn>
              <IconBtn
                title="Delete selected"
                onClick={() => selectedId && deleteNode(selectedId)}
                disabled={!selectedId || selectedId === root._id}
                danger
              >
                <Trash2 className="h-3.5 w-3.5" />
              </IconBtn>
            </div>
          </div>

          <div className="flex-1 min-h-0">
            <Preview root={root} selectedId={selectedId} onSelect={setSelectedId} width={widthPx} />
          </div>
        </section>

        {/* right: inspector */}
        <aside className="w-[300px] shrink-0 glass-card overflow-y-auto scrollbar-thin">
          <Inspector node={selected ?? null} onChange={updateProps} />
        </aside>
      </div>

      <DragOverlay>
        {dragType && (
          <div className="rounded-lg border border-brand/60 bg-brand-subtle px-3 py-1.5 text-xs text-brand shadow-glow">
            {PRIMITIVE_BY_TYPE[dragType]?.label ?? dragType}
          </div>
        )}
      </DragOverlay>
    </DndContext>
  );
}

function IconBtn({ children, title, onClick, disabled, danger }: {
  children: React.ReactNode; title: string; onClick: () => void; disabled?: boolean; danger?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "rounded-md p-1.5 transition-colors disabled:opacity-30",
        danger
          ? "text-text-secondary hover:text-rose-400 hover:bg-rose-500/10"
          : "text-text-secondary hover:text-text-primary hover:bg-bg-muted",
      )}
    >
      {children}
    </button>
  );
}

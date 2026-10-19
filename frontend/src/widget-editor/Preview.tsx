import { useCallback } from "react";
import { SpecRenderer } from "../renderer/SpecRenderer";
import type { TreeNode } from "./specTree";

type Props = {
  root: TreeNode;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  /** Canvas width in px; undefined = fill. */
  width?: number;
};

/**
 * Design canvas: renders the widget through the real renderer (ids flow
 * through as `data-aig-id`), so clicking any element selects it for the
 * inspector — direct manipulation instead of tree-only editing.
 */
export function Preview({ root, selectedId, onSelect, width }: Props) {
  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      if (!onSelect) return;
      const hit = (e.target as HTMLElement).closest("[data-aig-id]");
      if (hit) {
        e.preventDefault();
        e.stopPropagation();
        onSelect(hit.getAttribute("data-aig-id")!);
      }
    },
    [onSelect],
  );

  return (
    <div
      className="aig-editing relative h-full overflow-auto scrollbar-thin rounded-xl border border-border-subtle"
      style={{
        background:
          "radial-gradient(rgb(var(--text-muted) / 0.14) 1px, transparent 1px) 0 0 / 18px 18px, rgb(var(--bg-base) / 0.6)",
      }}
    >
      {/* selection outline for the active node */}
      {selectedId && (
        <style>{`.aig-editing [data-aig-id="${selectedId}"]{outline:2px solid rgb(var(--brand));outline-offset:2px;border-radius:4px;}`}</style>
      )}
      {/* Block layout, not grid: a percentage width must resolve against a
          definite containing block, or the widget collapses to content width. */}
      <div className="min-h-full py-10 px-6">
        <div
          onClickCapture={handleClick}
          style={{ width: width ?? "100%", maxWidth: "100%", marginInline: "auto" }}
          className="transition-[width] duration-200"
        >
          <SpecRenderer spec={root} onSubmit={(p) => console.log("[preview submit]", p)} />
        </div>
      </div>
    </div>
  );
}

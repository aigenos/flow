import { useDroppable } from "@dnd-kit/core";
import { ChevronRight, Trash2 } from "lucide-react";
import { cn } from "../lib/cn";
import { PRIMITIVE_BY_TYPE } from "./primitiveMeta";
import type { TreeNode } from "./specTree";

type Props = {
  root: TreeNode;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
};

export function TreeCanvas({ root, selectedId, onSelect, onDelete }: Props) {
  return (
    <div className="font-mono text-xs">
      <Node node={root} depth={0} selectedId={selectedId} onSelect={onSelect} onDelete={onDelete} isRoot />
    </div>
  );
}

function Node({
  node, depth, selectedId, onSelect, onDelete, isRoot,
}: {
  node: TreeNode;
  depth: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  isRoot?: boolean;
}) {
  const meta = PRIMITIVE_BY_TYPE[node.type];
  const isContainer = meta?.container ?? false;
  const isSelected = selectedId === node._id;
  const title = (node.props?.title as string) || (node.props?.label as string) || (node.props?.value as string);

  const { isOver, setNodeRef } = useDroppable({
    id: node._id,
    data: { type: "container", parentId: node._id, accepts: isContainer ? "any" : "none" },
    disabled: !isContainer,
  });

  return (
    <div className="relative">
      <div
        ref={isContainer ? setNodeRef : undefined}
        onClick={(e) => { e.stopPropagation(); onSelect(node._id); }}
        style={{ paddingLeft: depth * 14 }}
        className={cn(
          "group flex items-center gap-1 py-1 px-2 rounded cursor-pointer transition-colors",
          isSelected ? "bg-brand-subtle text-text-primary" : "hover:bg-bg-muted/60 text-text-secondary",
          isContainer && isOver && "ring-1 ring-brand/60 bg-brand-subtle",
        )}
      >
        {isContainer ? (
          <ChevronRight className="h-3 w-3 shrink-0 opacity-70" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <span className={cn("font-medium", isSelected && "text-brand")}>{meta?.label ?? node.type}</span>
        {title && (
          <span className="text-text-muted truncate max-w-[180px]">· {String(title)}</span>
        )}
        {!isRoot && (
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(node._id); }}
            className="ml-auto opacity-0 group-hover:opacity-100 rounded p-1 text-text-muted hover:text-rose-400 transition-opacity"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        )}
      </div>

      {isContainer && (node.children?.length ?? 0) > 0 && (
        <div>
          {node.children!.map((c) => (
            <Node
              key={(c as TreeNode)._id}
              node={c as TreeNode}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          ))}
        </div>
      )}

      {isContainer && (node.children?.length ?? 0) === 0 && (
        <div
          style={{ paddingLeft: (depth + 1) * 14 + 16 }}
          className={cn(
            "py-1.5 text-text-muted italic text-[12px]",
            isOver && "text-brand",
          )}
        >
          drop primitive here
        </div>
      )}
    </div>
  );
}

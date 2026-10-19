import { useDraggable } from "@dnd-kit/core";
import {
  Square, Type, Columns, Rows, Minus, AlertCircle, Tag, MousePointerClick,
  TextCursor, ListChecks, FormInput, Image as ImageIcon, Search, Table2, History,
} from "lucide-react";
import { CATEGORY_LABELS, PRIMITIVES, type PrimitiveCategory } from "./primitiveMeta";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  card: Square, text: Type, row: Columns, column: Rows, divider: Minus, alert: AlertCircle,
  badge: Tag, button: MousePointerClick, input: TextCursor, select: ListChecks,
  form: FormInput, image: ImageIcon,
  autosuggest: Search, datatable: Table2, timeline: History,
};

const ORDER: PrimitiveCategory[] = ["layout", "content", "inputs", "data"];

/** Categorized component palette. Drag onto the tree, or click to append to
 *  the selected container. */
export function Palette({ onQuickAdd }: { onQuickAdd?: (type: string) => void }) {
  return (
    <div className="space-y-3">
      {ORDER.map((cat) => (
        <div key={cat}>
          <div className="px-2 mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
            {CATEGORY_LABELS[cat]}
          </div>
          <div className="grid grid-cols-2 gap-1">
            {PRIMITIVES.filter((p) => p.category === cat).map((p) => (
              <PaletteItem key={p.type} type={p.type} label={p.label} hint={p.hint} onQuickAdd={onQuickAdd} />
            ))}
          </div>
        </div>
      ))}
      <p className="px-2 pt-1 text-[11px] text-text-muted leading-relaxed">
        Drag into the tree, or click to add to the selected container.
      </p>
    </div>
  );
}

function PaletteItem({ type, label, hint, onQuickAdd }: {
  type: string; label: string; hint?: string; onQuickAdd?: (type: string) => void;
}) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `palette-${type}`,
    data: { source: "palette", type },
  });
  const Icon = ICONS[type] ?? Square;
  return (
    <button
      ref={setNodeRef}
      {...attributes}
      {...listeners}
      type="button"
      title={hint}
      onClick={() => onQuickAdd?.(type)}
      className={`flex flex-col items-center gap-1 rounded-lg border border-border-subtle bg-bg-surface px-1.5 py-2 text-[12px] text-text-secondary transition-all hover:text-text-primary hover:border-brand/40 hover:bg-bg-muted/60 hover:-translate-y-px cursor-grab active:cursor-grabbing select-none ${
        isDragging ? "opacity-30" : ""
      }`}
    >
      <Icon className="h-4 w-4 text-text-muted" />
      {label}
    </button>
  );
}

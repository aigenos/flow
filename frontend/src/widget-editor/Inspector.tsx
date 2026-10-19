import { useState, type ReactNode } from "react";
import {
  AlignCenter, AlignJustify, AlignLeft, AlignRight, Bold,
  ChevronDown, Italic, Layout, Paintbrush, SlidersHorizontal, Strikethrough,
  Type, Underline,
} from "lucide-react";
import { cn } from "../lib/cn";
import type { WidgetStyle } from "../renderer/styleProps";
import { PRIMITIVE_BY_TYPE, type FieldMeta } from "./primitiveMeta";
import type { TreeNode } from "./specTree";

type Props = {
  node: TreeNode | null;
  onChange: (id: string, props: Record<string, unknown>) => void;
};

/**
 * Design-tool inspector: Content (per-primitive fields) + Typography /
 * Appearance / Layout sections that write the node's `props.style` — the
 * style dictionary the renderer resolves to inline CSS.
 */
export function Inspector({ node, onChange }: Props) {
  if (!node) {
    return (
      <div className="p-4 text-xs text-text-muted leading-relaxed">
        Select an element on the canvas or in the tree to edit its content and
        style.
      </div>
    );
  }

  const meta = PRIMITIVE_BY_TYPE[node.type];
  if (!meta) {
    return <div className="p-4 text-xs text-rose-600 dark:text-rose-400">Unknown primitive: {node.type}</div>;
  }

  const props = node.props ?? {};
  const style: WidgetStyle = (props.style as WidgetStyle) ?? {};

  const setProp = (name: string, v: unknown) => onChange(node._id, { ...props, [name]: v });

  const setStyle = (patch: Partial<WidgetStyle>) => {
    const next: Record<string, unknown> = { ...style };
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "" || v === false || v === "inherit") delete next[k];
      else next[k] = v;
    }
    const out = { ...props };
    if (Object.keys(next).length) out.style = next;
    else delete out.style;
    onChange(node._id, out);
  };

  return (
    <div className="pb-6">
      <div className="px-4 pt-4 pb-3 border-b border-border-subtle">
        <div className="text-[11px] font-semibold uppercase tracking-[0.15em] text-text-muted">Selected</div>
        <div className="mt-0.5 flex items-center gap-2">
          <span className="text-sm font-semibold text-text-primary">{meta.label}</span>
          <span className="rounded bg-bg-muted border border-border-subtle px-1.5 py-px text-[11px] font-mono text-text-muted">
            {node.type}
          </span>
        </div>
        {meta.hint && <p className="mt-1 text-[12px] text-text-muted leading-relaxed">{meta.hint}</p>}
      </div>

      <Section title="Content" icon={SlidersHorizontal} defaultOpen>
        {meta.fields.length === 0 ? (
          <div className="text-xs text-text-muted">No content properties.</div>
        ) : (
          // Keyed by element as well as field: a JSON field holds its own draft
          // text, and would keep showing the previous element's otherwise.
          meta.fields.map((f) => (
            <Field key={`${node._id}:${f.name}`} meta={f} value={props[f.name]} onChange={(v) => setProp(f.name, v)} />
          ))
        )}
      </Section>

      <Section title="Typography" icon={Type} defaultOpen>
        <Labeled label="Font family">
          <select
            value={(["sans", "serif", "mono"].includes(String(style.font)) ? style.font : style.font ? "custom" : "inherit") as string}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "inherit") setStyle({ font: undefined });
              else if (v === "custom") setStyle({ font: "Verdana, sans-serif" });
              else setStyle({ font: v as WidgetStyle["font"] });
            }}
            className={ctl}
          >
            <option value="inherit">Theme default (Inter)</option>
            <option value="sans">Sans — Inter</option>
            <option value="serif">Serif — Georgia</option>
            <option value="mono">Mono — JetBrains</option>
            <option value="custom">Custom…</option>
          </select>
        </Labeled>
        {style.font && !["sans", "serif", "mono"].includes(String(style.font)) && (
          <Labeled label="Custom font stack">
            <input
              type="text"
              value={String(style.font)}
              onChange={(e) => setStyle({ font: e.target.value })}
              placeholder="e.g. Verdana, sans-serif"
              className={cn(ctl, "font-mono")}
            />
          </Labeled>
        )}

        <div className="grid grid-cols-2 gap-2">
          <Labeled label="Size (px)">
            <NumberBox value={style.size} min={8} max={64} onChange={(v) => setStyle({ size: v })} placeholder="—" />
          </Labeled>
          <Labeled label="Weight">
            <select
              value={String(style.weight ?? "")}
              onChange={(e) => setStyle({ weight: e.target.value ? Number(e.target.value) : undefined })}
              className={ctl}
            >
              <option value="">Default</option>
              <option value="300">Light 300</option>
              <option value="400">Regular 400</option>
              <option value="500">Medium 500</option>
              <option value="600">Semibold 600</option>
              <option value="700">Bold 700</option>
              <option value="800">Extrabold 800</option>
            </select>
          </Labeled>
        </div>

        <div className="flex items-center gap-2">
          <ToggleGroup
            items={[
              { key: "bold", icon: Bold, on: Number(style.weight ?? 400) >= 600, set: (on) => setStyle({ weight: on ? 700 : undefined }), title: "Bold" },
              { key: "italic", icon: Italic, on: Boolean(style.italic), set: (on) => setStyle({ italic: on || undefined }), title: "Italic" },
              { key: "underline", icon: Underline, on: Boolean(style.underline), set: (on) => setStyle({ underline: on || undefined }), title: "Underline" },
              { key: "strike", icon: Strikethrough, on: Boolean(style.strike), set: (on) => setStyle({ strike: on || undefined }), title: "Strikethrough" },
            ]}
          />
          <ToggleGroup
            items={(["left", "center", "right", "justify"] as const).map((a) => ({
              key: a,
              icon: { left: AlignLeft, center: AlignCenter, right: AlignRight, justify: AlignJustify }[a],
              on: style.align === a,
              set: (on: boolean) => setStyle({ align: on ? a : undefined }),
              title: `Align ${a}`,
            }))}
          />
        </div>

        <ColorRow label="Text color" value={style.color} onChange={(v) => setStyle({ color: v })} swatches={TEXT_SWATCHES} />

        <div className="grid grid-cols-3 gap-2">
          <Labeled label="Tracking">
            <NumberBox value={style.spacing} min={-2} max={12} step={0.5} onChange={(v) => setStyle({ spacing: v })} placeholder="—" />
          </Labeled>
          <Labeled label="Line height">
            <NumberBox value={style.leading} min={0.8} max={3} step={0.1} onChange={(v) => setStyle({ leading: v })} placeholder="—" />
          </Labeled>
          <Labeled label="Case">
            <select
              value={style.transform ?? ""}
              onChange={(e) => setStyle({ transform: (e.target.value || undefined) as WidgetStyle["transform"] })}
              className={ctl}
            >
              <option value="">As typed</option>
              <option value="uppercase">UPPER</option>
              <option value="lowercase">lower</option>
              <option value="capitalize">Title</option>
            </select>
          </Labeled>
        </div>
      </Section>

      <Section title="Appearance" icon={Paintbrush}>
        <ColorRow label="Background" value={style.bg} onChange={(v) => setStyle({ bg: v })} swatches={BG_SWATCHES} />
        <ColorRow label="Border color" value={style.borderColor} onChange={(v) => setStyle({ borderColor: v })} swatches={TEXT_SWATCHES} />
        <div className="grid grid-cols-2 gap-2">
          <Labeled label="Border (px)">
            <NumberBox value={style.borderWidth} min={0} max={8} onChange={(v) => setStyle({ borderWidth: v })} placeholder="—" />
          </Labeled>
          <Labeled label="Radius (px)">
            <NumberBox value={style.radius} min={0} max={32} onChange={(v) => setStyle({ radius: v })} placeholder="—" />
          </Labeled>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Labeled label="Shadow">
            <select
              value={style.shadow ?? ""}
              onChange={(e) => setStyle({ shadow: (e.target.value || undefined) as WidgetStyle["shadow"] })}
              className={ctl}
            >
              <option value="">None</option>
              <option value="sm">Subtle</option>
              <option value="md">Medium</option>
              <option value="lg">Large</option>
              <option value="glow">Brand glow</option>
            </select>
          </Labeled>
          <Labeled label={`Opacity ${style.opacity !== undefined ? Math.round((style.opacity > 1 ? style.opacity : style.opacity * 100)) + "%" : ""}`}>
            <input
              type="range"
              min={10}
              max={100}
              value={style.opacity !== undefined ? (style.opacity > 1 ? style.opacity : style.opacity * 100) : 100}
              onChange={(e) => setStyle({ opacity: Number(e.target.value) === 100 ? undefined : Number(e.target.value) })}
              className="w-full accent-[rgb(var(--brand))]"
            />
          </Labeled>
        </div>
      </Section>

      <Section title="Layout" icon={Layout}>
        <div className="grid grid-cols-2 gap-2">
          <Labeled label="Padding (px)">
            <NumberBox value={style.pad} min={0} max={64} onChange={(v) => setStyle({ pad: v })} placeholder="—" />
          </Labeled>
          <Labeled label="Gap (px)">
            <NumberBox value={style.gap} min={0} max={48} onChange={(v) => setStyle({ gap: v })} placeholder="—" />
          </Labeled>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Labeled label="Width (px)">
            <NumberBox value={style.width} min={0} max={1200} onChange={(v) => setStyle({ width: v })} placeholder="auto" />
          </Labeled>
          <Labeled label="Max width (px)">
            <NumberBox value={style.maxWidth} min={0} max={1200} onChange={(v) => setStyle({ maxWidth: v })} placeholder="—" />
          </Labeled>
        </div>
      </Section>
    </div>
  );
}

/* ------------------------------------------------------------- controls -- */

const ctl =
  "w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand";

function Section({ title, icon: Icon, defaultOpen, children }: {
  title: string; icon: React.ComponentType<{ className?: string }>; defaultOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  return (
    <div className="border-b border-border-subtle">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left hover:bg-bg-muted/40 transition-colors"
      >
        <Icon className="h-3.5 w-3.5 text-text-muted" />
        <span className="text-[12px] font-semibold uppercase tracking-[0.12em] text-text-secondary">{title}</span>
        <ChevronDown className={cn("ml-auto h-3.5 w-3.5 text-text-muted transition-transform", open && "rotate-180")} />
      </button>
      {open && <div className="px-4 pb-4 space-y-2.5">{children}</div>}
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="block text-[11px] text-text-muted mb-1">{label}</span>
      {children}
    </label>
  );
}

function NumberBox({ value, onChange, min, max, step, placeholder }: {
  value: number | string | undefined;
  onChange: (v: number | undefined) => void;
  min?: number; max?: number; step?: number; placeholder?: string;
}) {
  return (
    <input
      type="number"
      value={value ?? ""}
      min={min}
      max={max}
      step={step ?? 1}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))}
      className={ctl}
    />
  );
}

function ToggleGroup({ items }: {
  items: Array<{ key: string; icon: React.ComponentType<{ className?: string }>; on: boolean; set: (on: boolean) => void; title: string }>;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border-subtle bg-bg-muted p-0.5">
      {items.map(({ key, icon: Icon, on, set, title }) => (
        <button
          key={key}
          type="button"
          title={title}
          aria-pressed={on}
          onClick={() => set(!on)}
          className={cn(
            "rounded-md p-1.5 transition-colors",
            on ? "bg-brand text-brand-fg shadow-sm" : "text-text-secondary hover:text-text-primary hover:bg-bg-elevated",
          )}
        >
          <Icon className="h-3.5 w-3.5" />
        </button>
      ))}
    </div>
  );
}

const TEXT_SWATCHES: Array<{ v: string; label: string }> = [
  { v: "rgb(var(--text-primary))", label: "Primary" },
  { v: "rgb(var(--text-secondary))", label: "Secondary" },
  { v: "rgb(var(--brand))", label: "Brand" },
  { v: "rgb(var(--accent))", label: "Accent" },
  { v: "#34d399", label: "Green" },
  { v: "#fbbf24", label: "Amber" },
  { v: "#fb7185", label: "Rose" },
  { v: "#38bdf8", label: "Sky" },
];

const BG_SWATCHES: Array<{ v: string; label: string }> = [
  { v: "rgb(var(--bg-muted))", label: "Muted" },
  { v: "rgb(var(--bg-elevated))", label: "Elevated" },
  { v: "rgb(var(--brand-subtle))", label: "Brand tint" },
  { v: "rgb(var(--accent) / 0.12)", label: "Accent tint" },
  { v: "rgb(52 211 153 / 0.10)", label: "Green tint" },
  { v: "rgb(251 191 36 / 0.10)", label: "Amber tint" },
  { v: "rgb(251 113 133 / 0.10)", label: "Rose tint" },
  { v: "transparent", label: "Transparent" },
];

function ColorRow({ label, value, onChange, swatches }: {
  label: string;
  value: string | undefined;
  onChange: (v: string | undefined) => void;
  swatches: Array<{ v: string; label: string }>;
}) {
  return (
    <div>
      <span className="block text-[11px] text-text-muted mb-1">{label}</span>
      <div className="flex items-center gap-1 flex-wrap">
        <button
          type="button"
          title="Theme default"
          onClick={() => onChange(undefined)}
          className={cn(
            "h-5 w-5 rounded-full border grid place-items-center text-[10px] text-text-muted",
            value === undefined ? "border-brand ring-1 ring-brand/50" : "border-border hover:border-border-strong",
          )}
        >
          ∅
        </button>
        {swatches.map((s) => (
          <button
            key={s.v}
            type="button"
            title={s.label}
            onClick={() => onChange(s.v)}
            style={{ background: s.v }}
            className={cn(
              "h-5 w-5 rounded-full border transition-transform hover:scale-110",
              s.v === "transparent" && "bg-[repeating-conic-gradient(rgb(var(--text-muted)/0.35)_0%_25%,transparent_0%_50%)] bg-[length:8px_8px]",
              value === s.v ? "border-brand ring-1 ring-brand/50" : "border-border-strong/40",
            )}
          />
        ))}
        <label
          title="Custom color"
          className={cn(
            "relative h-5 w-5 rounded-full border cursor-pointer overflow-hidden",
            "bg-[conic-gradient(#f87171,#fbbf24,#34d399,#38bdf8,#a78bfa,#f87171)]",
            value && !swatches.some((s) => s.v === value) ? "border-brand ring-1 ring-brand/50" : "border-border-strong/40",
          )}
        >
          <input
            type="color"
            value={/^#([0-9a-f]{6})$/i.test(String(value ?? "")) ? String(value) : "#22a3ab"}
            onChange={(e) => onChange(e.target.value)}
            className="absolute inset-0 opacity-0 cursor-pointer"
          />
        </label>
      </div>
    </div>
  );
}

/* --------------------------------------------------- content field types -- */

function Field({ meta, value, onChange }: { meta: FieldMeta; value: unknown; onChange: (v: unknown) => void }) {
  return (
    <label className="block">
      <span className="block text-[11px] text-text-muted mb-1">{meta.label}</span>

      {meta.type === "string" && (
        <input type="text" value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} className={ctl} />
      )}

      {meta.type === "textarea" && (
        <textarea
          value={(value as string) ?? ""}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          className={cn(ctl, "resize-y font-mono")}
        />
      )}

      {meta.type === "number" && (
        <input
          type="number"
          value={(value as number) ?? 0}
          onChange={(e) => onChange(Number(e.target.value))}
          className={ctl}
        />
      )}

      {meta.type === "boolean" && (
        <span className="inline-flex items-center gap-2 text-xs text-text-primary">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
            className="rounded border-border bg-bg-muted text-brand focus:ring-brand/40"
          />
          Enabled
        </span>
      )}

      {meta.type === "select" && (
        <select value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} className={ctl}>
          {meta.options?.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      )}

      {meta.type === "json" && <JsonField value={value} onChange={onChange} />}

      {meta.help && <span className="block text-[11px] text-text-muted mt-1">{meta.help}</span>}
    </label>
  );
}

function JsonField({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [error, setError] = useState<string | null>(null);

  return (
    <div>
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setError(null);
          } catch (err) {
            setError((err as Error).message);
          }
        }}
        rows={4}
        className={cn(ctl, "font-mono resize-y")}
      />
      {error && <div className="text-[11px] text-rose-600 dark:text-rose-400 mt-1">{error}</div>}
    </div>
  );
}

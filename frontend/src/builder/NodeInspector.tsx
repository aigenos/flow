import { useEffect, useState } from "react";
import { cn } from "../lib/cn";
import { api, WidgetListItem } from "../lib/api";
import type { WfNodeData } from "./nodes";

type Props = {
  node: { id: string; data: WfNodeData } | null;
  overrides: Record<string, any>;
  onOverridesChange: (id: string, patch: Record<string, any>) => void;
  onLabelChange: (id: string, patch: Partial<WfNodeData>) => void;
  onSetStart: (id: string) => void;
  isStart: boolean;
};

export function NodeInspector({ node, overrides, onOverridesChange, onLabelChange, onSetStart, isStart }: Props) {
  const [widgets, setWidgets] = useState<WidgetListItem[]>([]);

  useEffect(() => {
    api.listWidgets().then(setWidgets).catch(() => undefined);
  }, []);

  if (!node) {
    return <div className="p-4 text-xs text-text-muted">Select a node to edit.</div>;
  }

  const cls = "w-full rounded-md border border-border bg-bg-muted px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand";
  const t = node.data.nodeType;
  const ov = overrides[node.id] ?? {};

  const setOv = (patch: Record<string, any>) => onOverridesChange(node.id, { ...ov, ...patch });

  return (
    <div className="p-4 space-y-3">
      <div>
        <div className="text-xs uppercase tracking-wider text-text-muted">Node</div>
        <div className="text-sm font-medium text-text-primary mt-0.5">{node.id}</div>
        <div className="text-[12px] text-text-secondary">type: {t}</div>
      </div>

      <label className="flex items-center gap-2 text-xs text-text-primary">
        <input
          type="checkbox"
          checked={isStart}
          onChange={() => onSetStart(node.id)}
          className="rounded border-border bg-bg-muted text-brand focus:ring-brand/40"
        />
        Start node
      </label>

      {t === "message" && (
        <label className="block">
          <span className="block text-[12px] text-text-secondary mb-1">Text</span>
          <textarea
            value={(ov.text ?? node.data.preview) ?? ""}
            onChange={(e) => {
              setOv({ text: e.target.value });
              onLabelChange(node.id, { preview: e.target.value });
            }}
            rows={3}
            className={cn(cls, "font-mono resize-y")}
          />
        </label>
      )}

      {t === "widget" && (
        <>
          <label className="block">
            <span className="block text-[12px] text-text-secondary mb-1">Widget</span>
            <select
              value={ov.widget ?? node.data.widget ?? ""}
              onChange={(e) => {
                setOv({ widget: e.target.value });
                onLabelChange(node.id, { widget: e.target.value });
              }}
              className={cls}
            >
              <option value="">— choose widget —</option>
              {widgets.map((w) => <option key={w.id} value={w.id}>{w.id}</option>)}
            </select>
          </label>
          <label className="block">
            <span className="block text-[12px] text-text-secondary mb-1">Save submission to</span>
            <input
              type="text"
              value={ov.save_to ?? ""}
              onChange={(e) => setOv({ save_to: e.target.value })}
              placeholder="state key"
              className={cls}
            />
          </label>
          <JsonField
            label="Props (JSON)"
            value={ov.props ?? {}}
            onChange={(v) => setOv({ props: v })}
            cls={cls}
          />
        </>
      )}

      {t === "tool_call" && (
        <>
          <label className="block">
            <span className="block text-[12px] text-text-secondary mb-1">Tool name</span>
            <input
              type="text"
              value={ov.tool ?? node.data.tool ?? ""}
              onChange={(e) => {
                setOv({ tool: e.target.value });
                onLabelChange(node.id, { tool: e.target.value });
              }}
              className={cls}
            />
          </label>
          <label className="block">
            <span className="block text-[12px] text-text-secondary mb-1">Save result to</span>
            <input
              type="text"
              value={ov.save_to ?? ""}
              onChange={(e) => setOv({ save_to: e.target.value })}
              className={cls}
            />
          </label>
          <JsonField
            label="Args (JSON)"
            value={ov.args ?? {}}
            onChange={(v) => setOv({ args: v })}
            cls={cls}
          />
        </>
      )}

      {t === "branch" && (
        <div className="text-[12px] text-text-secondary leading-relaxed">
          Branch nodes route based on outgoing edge labels (Python expressions over state).
          Click an edge in the canvas to set its <code className="text-brand">if</code> condition.
          The last edge with no label is the fallback.
        </div>
      )}

      {t === "end" && (
        <div className="text-[12px] text-text-secondary">Terminal node.</div>
      )}
    </div>
  );
}

function JsonField({ label, value, onChange, cls }: { label: string; value: unknown; onChange: (v: any) => void; cls: string }) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setText(JSON.stringify(value ?? {}, null, 2));
  }, [value]);

  return (
    <label className="block">
      <span className="block text-[12px] text-text-secondary mb-1">{label}</span>
      <textarea
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            onChange(JSON.parse(e.target.value));
            setErr(null);
          } catch (er) {
            setErr((er as Error).message);
          }
        }}
        rows={4}
        className={`${cls} font-mono resize-y`}
      />
      {err && <div className="text-[11px] text-rose-600 dark:text-rose-400 mt-1">{err}</div>}
    </label>
  );
}

import { useEffect, useRef, useState } from "react";
import { Radio, ChevronDown, ChevronUp, Trash2 } from "lucide-react";
import { cn } from "../lib/cn";
import type { Protocol, RawFrame } from "../agui/client";

type Props = {
  protocol: Protocol;
  frames: RawFrame[];
  onClear: () => void;
};

const PROTOCOL_LABEL: Record<Protocol, string> = {
  "ag-ui": "AG-UI",
  "a2ui": "A2UI",
  "mcp-ui": "MCP-UI",
};

// The first two classes are the text colour (light, then dark:) — FrameRow
// takes just those for the event name.
const PROTOCOL_COLOR: Record<Protocol, string> = {
  "ag-ui": "text-violet-700 dark:text-violet-300 border-violet-500/30 bg-violet-500/[0.08]",
  "a2ui": "text-sky-700 dark:text-sky-300 border-sky-500/30 bg-sky-500/[0.08]",
  "mcp-ui": "text-emerald-700 dark:text-emerald-300 border-emerald-500/30 bg-emerald-500/[0.08]",
};

export function ProtocolViewer({ protocol, frames, onClear }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [frames]);

  return (
    <div className="border-t border-border-subtle bg-bg-surface theme-transition">
      <div className="mx-auto max-w-7xl px-6 py-2 flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <Radio className="h-3.5 w-3.5 text-text-muted" />
          <span className="text-text-secondary font-medium">Protocol stream</span>
          <span
            className={cn(
              "inline-flex items-center rounded-md border px-1.5 py-0.5 text-[11px] font-mono",
              PROTOCOL_COLOR[protocol],
            )}
          >
            {PROTOCOL_LABEL[protocol]}
          </span>
          <span className="text-text-muted">{frames.length} events</span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onClear}
            disabled={!frames.length}
            className="inline-flex items-center gap-1 rounded p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-muted disabled:opacity-30 transition-colors"
            title="Clear log"
          >
            <Trash2 className="h-3 w-3" />
          </button>
          <button
            onClick={() => setCollapsed((c) => !c)}
            className="inline-flex items-center gap-1 rounded p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-muted transition-colors"
            title={collapsed ? "Expand" : "Collapse"}
          >
            {collapsed ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </button>
        </div>
      </div>

      {!collapsed && (
        <div
          ref={scrollRef}
          className="mx-auto max-w-7xl px-6 pb-3 max-h-48 overflow-y-auto scrollbar-thin"
        >
          {frames.length === 0 ? (
            <div className="text-xs text-text-muted italic py-2">
              Waiting for events. Start a workflow to see {PROTOCOL_LABEL[protocol]} frames stream in.
            </div>
          ) : (
            <div className="space-y-0.5 font-mono text-[12px] leading-snug">
              {frames.map((f, i) => (
                <FrameRow key={i} frame={f} protocol={protocol} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function FrameRow({ frame, protocol }: { frame: RawFrame; protocol: Protocol }) {
  const [open, setOpen] = useState(false);
  const time = new Date(frame.ts).toISOString().slice(11, 23);
  return (
    <div className="border-l-2 border-border-subtle pl-2 hover:bg-bg-muted/40 -mx-1 px-1 rounded-r">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full text-left flex items-baseline gap-2 py-0.5"
      >
        <span className="text-text-muted shrink-0">{time}</span>
        <span className={cn("shrink-0", PROTOCOL_COLOR[protocol].split(" ").slice(0, 2).join(" "))}>{frame.event}</span>
        <span className="text-text-secondary truncate flex-1">
          {open ? "" : truncate(frame.data, 120)}
        </span>
      </button>
      {open && (
        <pre className="ml-12 mt-0.5 mb-1 text-[11px] text-text-secondary whitespace-pre-wrap break-all">
          {prettyJson(frame.data)}
        </pre>
      )}
    </div>
  );
}

function truncate(s: string, n: number) {
  if (s.length <= n) return s;
  return s.slice(0, n) + "…";
}

function prettyJson(s: string) {
  try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
}

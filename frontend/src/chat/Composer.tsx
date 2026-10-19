import { useEffect, useState } from "react";
import { Play, RotateCw, Radio } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../components/ui/button";
import type { Protocol } from "../agui/client";

type Workflow = { id: string; description: string };

type Props = {
  onStart: (workflowId: string) => void;
  running: boolean;
  onReset: () => void;
  protocol: Protocol;
  onProtocolChange: (p: Protocol) => void;
};

const PROTOCOLS: { id: Protocol; label: string; help: string }[] = [
  { id: "ag-ui", label: "AG-UI", help: "CopilotKit's protocol (default, renders in chat)" },
  { id: "a2ui", label: "A2UI", help: "Google's spec (raw stream — use UI client for rendering)" },
  { id: "mcp-ui", label: "MCP-UI", help: "MCP resource protocol (raw stream)" },
];

export function Composer({ onStart, running, onReset, protocol, onProtocolChange }: Props) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selected, setSelected] = useState<string>("");

  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    fetch("/api/workflows")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((j) => {
        const list: Workflow[] = Array.isArray(j?.workflows) ? j.workflows : [];
        setWorkflows(list);
        if (list[0]) setSelected(list[0].id);
      })
      .catch(() => setLoadError(true));
  }, []);

  return (
    <div className="border-t border-border-subtle glass theme-transition">
      <div className="mx-auto max-w-3xl px-6 py-4 flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          disabled={running}
          className={cn(
            "flex-1 h-10 rounded-xl border border-border bg-bg-muted/70 px-3 text-sm text-text-primary shadow-sm",
            "focus-ring focus:border-brand transition-colors",
          )}
        >
          {workflows.length === 0 && (
            <option>{loadError ? "Backend not reachable (run aigenos_flow.main on :8000)" : "No workflows found"}</option>
          )}
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>{w.description ? `${w.id} — ${w.description}` : w.id}</option>
          ))}
        </select>

        <div
          className="inline-flex items-stretch rounded-lg border border-border bg-bg-muted p-0.5 text-xs"
          role="group"
          aria-label="Protocol"
        >
          <Radio className="h-3.5 w-3.5 self-center ml-2 mr-1 text-text-muted" />
          {PROTOCOLS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => !running && onProtocolChange(p.id)}
              disabled={running}
              title={p.help}
              className={cn(
                "inline-flex items-center rounded-md px-2 py-1 transition-colors",
                protocol === p.id
                  ? "bg-bg-elevated text-text-primary shadow-sm"
                  : "text-text-secondary hover:text-text-primary",
                running && "opacity-50 cursor-not-allowed",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>

        {running ? (
          <Button variant="outline" onClick={onReset}>
            <RotateCw /> Reset
          </Button>
        ) : (
          <Button onClick={() => selected && onStart(selected)} disabled={!selected}>
            <Play /> Start
          </Button>
        )}
      </div>
      {/* Plain anchors, not router Links: this file ships in the npm lib and
          must not pull react-router into the bundle. */}
      <div className="mx-auto max-w-3xl px-6 pb-2.5 -mt-1.5 text-[11px] text-text-muted">
        These are legacy widget-flow graphs — authored in the{" "}
        <a href="/builder" className="text-brand hover:text-brand-hover">builder</a>. New
        deterministic workflows live on the{" "}
        <a href="/canvas" className="text-brand hover:text-brand-hover">Canvas</a> and run in{" "}
        <a href="/flow" className="text-brand hover:text-brand-hover">Flow</a>.
      </div>
    </div>
  );
}

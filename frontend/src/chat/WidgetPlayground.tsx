/**
 * WidgetPlayground — the Studio's protocol playground.
 *
 * NOT part of the published package surface: it drives the Studio's own
 * `/api/runs` endpoints (workflow dropdown, protocol switcher, raw-frame
 * viewer) and only works with the aigenos-flow server app mounted at /api.
 * The drop-in component consumers get is <Chat>, which talks to a chat router
 * at an endpoint they choose.
 */
import { useCallback, useRef, useState } from "react";
import { Info } from "lucide-react";
import { MessageList } from "./MessageList";
import { Composer } from "./Composer";
import { ProtocolViewer } from "./ProtocolViewer";
import { FlowConfigProvider } from "../renderer/FlowConfig";
import { openStream, startRun, submitWidget, type Protocol, type RawFrame } from "../agui/client";
import type { ChatMessage } from "../agui/types";

const PROTOCOL_TAGLINE: Record<Protocol, string> = {
  "ag-ui": "AG-UI (CopilotKit) · widgets render natively in chat",
  "a2ui": "A2UI (Google) · same widgets, A2UI ui_resource events",
  "mcp-ui": "MCP-UI · same widgets delivered as ui:// resources",
};

export function WidgetPlayground() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [protocol, setProtocol] = useState<Protocol>("ag-ui");
  const [rawFrames, setRawFrames] = useState<RawFrame[]>([]);
  const closeRef = useRef<(() => void) | null>(null);

  const start = useCallback(async (workflowId: string) => {
    setMessages([]);
    setRawFrames([]);
    setRunning(true);
    const tid = await startRun(workflowId);
    setThreadId(tid);
    closeRef.current = openStream(
      tid,
      {
        onMessages: setMessages,
        onError: (msg) =>
          setMessages((m) => [
            ...m,
            { id: `err-${Date.now()}`, kind: "text", role: "assistant", content: `Error: ${msg}` },
          ]),
        onDone: () => setRunning(false),
        onRawFrame: (frame) => setRawFrames((fs) => [...fs, frame]),
      },
      protocol,
    );
  }, [protocol]);

  const onWidgetSubmit = useCallback(
    async (toolCallId: string, payload: unknown) => {
      if (!threadId) return;
      setMessages((m) =>
        m.map((x) => (x.kind === "widget" && x.tool_call_id === toolCallId ? { ...x, resolved: true, submission: payload } : x)),
      );
      await submitWidget(threadId, payload);
    },
    [threadId],
  );

  const reset = useCallback(() => {
    closeRef.current?.();
    closeRef.current = null;
    setMessages([]);
    setRawFrames([]);
    setThreadId(null);
    setRunning(false);
  }, []);

  return (
    <div className="flex h-full flex-col theme-transition">
      <div className="glass border-b border-border-subtle px-6 py-2.5 flex items-center gap-3 sticky top-0 z-20">
        <span className="text-sm font-semibold tracking-tight shrink-0">
          Widget <span className="grad-text">playground</span>
        </span>
        <span className="inline-flex items-center gap-1.5 text-xs text-text-muted truncate">
          <Info className="h-3 w-3 shrink-0" />
          {PROTOCOL_TAGLINE[protocol]}
        </span>
        <span className="ml-auto">
          {running ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" /> live
            </span>
          ) : threadId ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-muted px-2.5 py-0.5 text-[11px] font-medium text-text-secondary">
              <span className="h-1.5 w-1.5 rounded-full bg-text-muted" /> finished
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-border-subtle bg-bg-muted px-2.5 py-0.5 text-[11px] font-medium text-text-muted">
              <span className="h-1.5 w-1.5 rounded-full bg-text-muted/50" /> idle
            </span>
          )}
        </span>
      </div>

      {/* The Studio DOES serve the flow router at /api/flow, so live field
          validation is switched on here explicitly. */}
      <FlowConfigProvider value={{ validateEndpoint: "/api/flow/validate-field" }}>
        <MessageList messages={messages} onWidgetSubmit={onWidgetSubmit} protocol={protocol} />
      </FlowConfigProvider>

      <ProtocolViewer
        protocol={protocol}
        frames={rawFrames}
        onClear={() => setRawFrames([])}
      />

      <Composer
        onStart={start}
        running={running}
        onReset={reset}
        protocol={protocol}
        onProtocolChange={setProtocol}
      />
    </div>
  );
}

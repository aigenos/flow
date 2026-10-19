/**
 * Multi-protocol SSE client for the Studio's own /api/runs surface.
 *
 * The wire translation itself lives in ./protocol.ts and is shared with
 * `useFlowChat`, so both clients speak AG-UI, A2UI and MCP-UI from one
 * mapping. This module only adds the EventSource plumbing and the
 * `ChatMessage[]` state mutations the Studio pages expect.
 *
 * Submissions go back through `/runs/{id}/submit` regardless of inbound
 * protocol — the runtime is protocol-agnostic on input.
 */
import { ChatMessage, WidgetPayload } from "./types";
import {
  ALL_EVENT_TYPES,
  eventTypesFor,
  handleProtocolFrame,
  type Protocol,
  type ProtocolSink,
} from "./protocol";

const API = "/api";

export type { Protocol };

export type RawFrame = {
  ts: number;
  event: string;
  data: string;
};

export async function startRun(workflowId: string): Promise<string> {
  const r = await fetch(`${API}/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ workflow_id: workflowId }),
  });
  if (!r.ok) throw new Error(`startRun failed: ${r.status}`);
  const j = (await r.json()) as { thread_id: string };
  return j.thread_id;
}

export async function submitWidget(threadId: string, payload: unknown): Promise<void> {
  const r = await fetch(`${API}/runs/${threadId}/submit`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ thread_id: threadId, payload }),
  });
  if (!r.ok) throw new Error(`submit failed: ${r.status}`);
}

export type StreamHandlers = {
  onMessages: (updater: (msgs: ChatMessage[]) => ChatMessage[]) => void;
  onError: (msg: string) => void;
  onDone: () => void;
  onRawFrame?: (frame: RawFrame) => void;
  onState?: (snapshot: Record<string, unknown>) => void;
};

export function openStream(
  threadId: string,
  handlers: StreamHandlers,
  protocol: Protocol = "ag-ui",
): () => void {
  const url = `${API}/runs/${threadId}/stream?protocol=${protocol}`;
  const es = new EventSource(url);
  const expected = new Set(eventTypesFor(protocol));
  const sink = sinkFor(handlers, () => {
    handlers.onDone();
    es.close();
  });

  for (const t of ALL_EVENT_TYPES) {
    es.addEventListener(t, (ev) => {
      const rawData = (ev as MessageEvent).data as string;
      handlers.onRawFrame?.({ ts: Date.now(), event: t, data: rawData });
      if (!expected.has(t)) return;

      let parsed: any;
      try { parsed = JSON.parse(rawData); } catch { return; }

      try {
        handleProtocolFrame(protocol, t, parsed, sink);
      } catch { /* ignore handler errors so one bad event doesn't kill the stream */ }
    });
  }

  es.onerror = () => {
    es.close();
    handlers.onDone();
  };

  return () => es.close();
}

function sinkFor(h: StreamHandlers, done: () => void): ProtocolSink {
  return {
    textStart: (id) => pushTextStart(h, id),
    textDelta: (id, delta) => appendText(h, id, delta),
    textEnd: (id) => endText(h, id),
    widgetStart: (tcid) => pushWidgetSkeleton(h, tcid),
    widgetArgs: (tcid, delta) => appendWidgetArgs(h, tcid, delta),
    widgetPayload: (tcid, payload) => setWidgetPayload(h, tcid, payload),
    widgetResult: (tcid, submission) => resolveWidget(h, tcid, submission),
    state: (snapshot) => h.onState?.(snapshot),
    runError: (msg) => h.onError(msg),
    runFinished: done,
  };
}

// ---------- shared state mutators ----------

function pushTextStart(h: StreamHandlers, id: string) {
  h.onMessages((msgs) => [...msgs, { id, kind: "text", role: "assistant", content: "", pending: true }]);
}

function appendText(h: StreamHandlers, id: string, delta: string) {
  h.onMessages((msgs) =>
    msgs.map((m) => (m.kind === "text" && m.id === id ? { ...m, content: m.content + delta } : m)),
  );
}

function endText(h: StreamHandlers, id: string) {
  h.onMessages((msgs) =>
    msgs.map((m) => (m.kind === "text" && m.id === id ? { ...m, pending: false } : m)),
  );
}

function pushWidgetSkeleton(h: StreamHandlers, toolCallId: string) {
  h.onMessages((msgs) => {
    // Avoid duplicate skeletons if an event arrives twice
    if (msgs.some((m) => m.kind === "widget" && m.tool_call_id === toolCallId)) return msgs;
    return [
      ...msgs,
      {
        id: toolCallId,
        kind: "widget",
        tool_call_id: toolCallId,
        payload: { widget_id: "", version: "", spec: { type: "loading" } },
        argsBuffer: "",
        resolved: false,
      },
    ];
  });
}

function appendWidgetArgs(h: StreamHandlers, toolCallId: string, delta: string) {
  h.onMessages((msgs) =>
    msgs.map((m) => {
      if (m.kind !== "widget" || m.tool_call_id !== toolCallId) return m;
      const argsBuffer = m.argsBuffer + delta;
      let payload = m.payload;
      try { payload = JSON.parse(argsBuffer) as WidgetPayload; } catch { /* incremental */ }
      return { ...m, argsBuffer, payload };
    }),
  );
}

function setWidgetPayload(h: StreamHandlers, toolCallId: string, payload: WidgetPayload) {
  h.onMessages((msgs) =>
    msgs.map((m) =>
      m.kind === "widget" && m.tool_call_id === toolCallId
        ? { ...m, payload, argsBuffer: JSON.stringify(payload) }
        : m,
    ),
  );
}

function resolveWidget(h: StreamHandlers, toolCallId: string, submission: unknown) {
  h.onMessages((msgs) =>
    msgs.map((m) =>
      m.kind === "widget" && m.tool_call_id === toolCallId
        ? { ...m, resolved: true, submission }
        : m,
    ),
  );
}

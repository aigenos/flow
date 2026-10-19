/**
 * ONE parser for all three wire protocols.
 *
 * AG-UI, A2UI and MCP-UI carry the same semantics — a text channel, a
 * widget-render channel, a widget-result channel — and differ only in event
 * names and payload shapes. This module owns that translation exactly once and
 * reports it through a callback interface, so every consumer (the `useFlowChat`
 * hook, the Studio's `agui/client.ts` EventSource client, tests) speaks all
 * three without duplicating the mapping.
 *
 * Before this existed the hook parsed only AG-UI while advertising three
 * protocols, so `protocol: "a2ui"` silently rendered an empty transcript.
 */
import type { SpecNode, WidgetPayload } from "./types";

export type Protocol = "ag-ui" | "a2ui" | "mcp-ui";

export const PROTOCOLS: readonly Protocol[] = ["ag-ui", "a2ui", "mcp-ui"] as const;

export const AG_UI_EVENT_TYPES = [
  "RUN_STARTED", "RUN_FINISHED", "RUN_ERROR",
  "STEP_STARTED", "STEP_FINISHED",
  "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END",
  "TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END", "TOOL_CALL_RESULT",
  "STATE_SNAPSHOT", "STATE_DELTA", "CUSTOM",
];

export const A2UI_EVENT_TYPES = [
  "a2ui.run.start", "a2ui.run.end", "a2ui.error",
  "a2ui.step.start", "a2ui.step.end",
  "a2ui.message.start", "a2ui.message.delta", "a2ui.message.end",
  "a2ui.tool_call", "a2ui.ui_resource", "a2ui.ui_resource_result",
  "a2ui.state", "a2ui.state.delta",
];

export const MCP_UI_EVENT_TYPES = [
  "notifications/run/started", "notifications/run/finished", "notifications/error",
  "notifications/step/started", "notifications/step/finished",
  "notifications/message/started", "notifications/message/delta", "notifications/message/completed",
  "notifications/tools/called", "notifications/tools/result",
  "notifications/resources/updated",
  "notifications/state/snapshot", "notifications/state/delta",
];

export const ALL_EVENT_TYPES = [
  ...AG_UI_EVENT_TYPES,
  ...A2UI_EVENT_TYPES,
  ...MCP_UI_EVENT_TYPES,
];

export function eventTypesFor(protocol: Protocol): string[] {
  if (protocol === "a2ui") return A2UI_EVENT_TYPES;
  if (protocol === "mcp-ui") return MCP_UI_EVENT_TYPES;
  return AG_UI_EVENT_TYPES;
}

/**
 * What a consumer has to be able to do. Every callback is optional so a
 * caller can subscribe to just the channel it cares about.
 */
export type ProtocolSink = {
  runStarted?: (threadId: string | undefined, runId: string | undefined) => void;
  runFinished?: () => void;
  runError?: (message: string) => void;
  textStart?: (id: string) => void;
  textDelta?: (id: string, delta: string) => void;
  textEnd?: (id: string) => void;
  /** A widget is coming. Push a skeleton keyed by this tool_call_id. */
  widgetStart?: (toolCallId: string) => void;
  /** Incremental JSON for a streamed widget payload (AG-UI only). */
  widgetArgs?: (toolCallId: string, delta: string) => void;
  /** A complete widget payload (a2ui / mcp-ui deliver it in one frame). */
  widgetPayload?: (toolCallId: string, payload: WidgetPayload) => void;
  /** The user's submission for a widget came back from the server. */
  widgetResult?: (toolCallId: string, submission: unknown) => void;
  /** Run-level state, e.g. `{ done: true, flow: "...", awaiting_field: null }`. */
  state?: (snapshot: Record<string, unknown>) => void;
  /** Any non-widget tool call, for callers that want to show tool activity. */
  toolCall?: (toolCallId: string, name: string, args: unknown) => void;
};

/**
 * Translate one SSE frame into sink calls.
 *
 * `eventName` is the SSE `event:` line; `data` the parsed `data:` JSON. AG-UI
 * frames also carry their own `type`, so `eventName` may be omitted for them.
 */
export function handleProtocolFrame(
  protocol: Protocol,
  eventName: string,
  data: unknown,
  sink: ProtocolSink,
): void {
  const d = (data ?? {}) as Record<string, any>;
  if (protocol === "a2ui") return handleA2ui(eventName, d, sink);
  if (protocol === "mcp-ui") return handleMcpUi(eventName, d, sink);
  return handleAgUi(String(d.type ?? eventName), d, sink);
}

// ---------- AG-UI ----------

function handleAgUi(type: string, d: Record<string, any>, s: ProtocolSink): void {
  switch (type) {
    case "RUN_STARTED":
      s.runStarted?.(d.thread_id, d.run_id);
      break;
    case "TEXT_MESSAGE_START":
      s.textStart?.(d.message_id);
      break;
    case "TEXT_MESSAGE_CONTENT":
      s.textDelta?.(d.message_id, d.delta ?? "");
      break;
    case "TEXT_MESSAGE_END":
      s.textEnd?.(d.message_id);
      break;
    case "TOOL_CALL_START":
      if (d.tool_call_name === "render_widget") s.widgetStart?.(d.tool_call_id);
      else s.toolCall?.(d.tool_call_id, d.tool_call_name, undefined);
      break;
    case "TOOL_CALL_ARGS":
      s.widgetArgs?.(d.tool_call_id, d.delta ?? "");
      break;
    case "TOOL_CALL_END":
      break;
    case "TOOL_CALL_RESULT":
      s.widgetResult?.(d.tool_call_id, safeJson(d.content));
      break;
    case "STATE_SNAPSHOT":
      s.state?.(d.snapshot ?? {});
      break;
    case "RUN_ERROR":
      s.runError?.(d.message ?? "run error");
      break;
    case "RUN_FINISHED":
      s.runFinished?.();
      break;
  }
}

// ---------- A2UI ----------
// a2ui.ui_resource delivers the whole widget in one event (not streamed).

function handleA2ui(eventName: string, d: Record<string, any>, s: ProtocolSink): void {
  switch (eventName) {
    case "a2ui.run.start":
      s.runStarted?.(d.thread_id, d.run_id);
      break;
    case "a2ui.message.start":
      s.textStart?.(d.id);
      break;
    case "a2ui.message.delta":
      s.textDelta?.(d.id, d.delta ?? "");
      break;
    case "a2ui.message.end":
      s.textEnd?.(d.id);
      break;
    case "a2ui.ui_resource": {
      const id: string = d.id;
      s.widgetStart?.(id);
      s.widgetPayload?.(id, {
        widget_id: d.widget_id ?? "",
        version: d.version ?? "",
        spec: d.spec as SpecNode,
        submit_schema: d.submit_schema,
      });
      break;
    }
    case "a2ui.ui_resource_result":
      s.widgetResult?.(d.id, d.result);
      break;
    case "a2ui.tool_call":
      s.toolCall?.(d.id, d.name, d.args);
      break;
    case "a2ui.state":
      s.state?.(d.snapshot ?? {});
      break;
    case "a2ui.error":
      s.runError?.(d.message ?? "a2ui error");
      break;
    case "a2ui.run.end":
      s.runFinished?.();
      break;
  }
}

// ---------- MCP-UI ----------
// notifications/resources/updated delivers a ui://widget/<id>/<tc> resource
// whose `text` field is the JSON-encoded widget payload.

function handleMcpUi(eventName: string, d: Record<string, any>, s: ProtocolSink): void {
  switch (eventName) {
    case "notifications/run/started":
      s.runStarted?.(d.threadId ?? d.thread_id, d.runId ?? d.run_id);
      break;
    case "notifications/message/started":
      s.textStart?.(d.id);
      break;
    case "notifications/message/delta":
      s.textDelta?.(d.id, (d.contents ?? []).map((c: any) => c.text ?? "").join(""));
      break;
    case "notifications/message/completed":
      s.textEnd?.(d.id);
      break;
    case "notifications/resources/updated": {
      const parsed = (safeJson(d.text ?? "{}") ?? {}) as Record<string, any>;
      const interaction = parsed.interaction ?? {};
      const tcid: string = interaction.tool_call_id ?? d.uri ?? `mcpui-${Date.now()}`;
      s.widgetStart?.(tcid);
      s.widgetPayload?.(tcid, {
        widget_id: parsed.widget_id ?? d.name ?? "",
        version: parsed.version ?? "",
        spec: parsed.spec as SpecNode,
        submit_schema: parsed.submit_schema,
      });
      break;
    }
    case "notifications/tools/result": {
      const content = (d.content ?? []).map((c: any) => c.text ?? "").join("");
      s.widgetResult?.(d.id, safeJson(content));
      break;
    }
    case "notifications/tools/called":
      s.toolCall?.(d.id, d.name, d.arguments);
      break;
    case "notifications/state/snapshot":
      s.state?.(d.state ?? {});
      break;
    case "notifications/error":
      s.runError?.(d.message ?? "mcp-ui error");
      break;
    case "notifications/run/finished":
      s.runFinished?.();
      break;
  }
}

export function safeJson(s: unknown): unknown {
  if (typeof s !== "string") return s;
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

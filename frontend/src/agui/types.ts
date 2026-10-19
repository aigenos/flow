export type AgUiEvent =
  | { type: "RUN_STARTED"; thread_id: string; run_id: string }
  | { type: "RUN_FINISHED"; thread_id: string; run_id: string }
  | { type: "RUN_ERROR"; message: string; code?: string }
  | { type: "STEP_STARTED"; step_name: string }
  | { type: "STEP_FINISHED"; step_name: string }
  | { type: "TEXT_MESSAGE_START"; message_id: string; role: "assistant" }
  | { type: "TEXT_MESSAGE_CONTENT"; message_id: string; delta: string }
  | { type: "TEXT_MESSAGE_END"; message_id: string }
  | { type: "TOOL_CALL_START"; tool_call_id: string; tool_call_name: string; parent_message_id?: string }
  | { type: "TOOL_CALL_ARGS"; tool_call_id: string; delta: string }
  | { type: "TOOL_CALL_END"; tool_call_id: string }
  | { type: "TOOL_CALL_RESULT"; tool_call_id: string; message_id: string; content: string; role: "tool" }
  | { type: "STATE_SNAPSHOT"; snapshot: Record<string, unknown> }
  | { type: "STATE_DELTA"; delta: Array<Record<string, unknown>> }
  | { type: "CUSTOM"; name: string; value: Record<string, unknown> };

export type SpecNode = {
  type: string;
  props?: Record<string, unknown>;
  children?: SpecNode[];
};

export type WidgetPayload = {
  widget_id: string;
  version: string;
  spec: SpecNode;
  submit_schema?: Record<string, unknown>;
};

export type ChatMessage =
  | { id: string; kind: "text"; role: "assistant" | "user"; content: string; pending?: boolean }
  | {
      id: string;
      kind: "widget";
      tool_call_id: string;
      payload: WidgetPayload;
      argsBuffer: string;
      resolved: boolean;
      submission?: unknown;
    };

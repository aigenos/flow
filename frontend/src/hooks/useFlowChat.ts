/**
 * useFlowChat — headless hook for consuming a flow chat stream from a
 * LangGraph backend that uses make_chat_router or make_flow_router.
 *
 * Plug into ANY chat UI (your own React shell, a CopilotKit shell, etc.). It owns:
 *   - thread_id lifecycle
 *   - SSE connection and event parsing for all three wire protocols
 *     (AG-UI, A2UI, MCP-UI) via the shared parser in ../agui/protocol
 *   - widget-event accumulation (streamed or single-frame → ChatMessage)
 *   - sending user messages
 *   - resuming after widget submission
 *
 * You bring your own renderer. Use the shipped <Widget> / SpecRenderer for
 * the widget UI, or roll your own.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, WidgetPayload } from "../agui/types";
import { asksForInput } from "../lib/asksForInput";
import { handleProtocolFrame, type Protocol, type ProtocolSink } from "../agui/protocol";

export type UseFlowChatOptions = {
  /** Backend URL for the chat router. Default: "/chat" */
  endpoint?: string;
  /** Protocol the backend should emit. Default: "ag-ui" */
  protocol?: Protocol;
  /** Optional persistent thread id (e.g. from localStorage). If omitted, server assigns one on first send. */
  threadId?: string;
  /**
   * Enter this skill directly instead of letting the first-turn classifier
   * pick one. Sent only when opening a new thread — re-pinning mid-run would
   * fight the engine's own push/pop when the user digresses.
   *
   * Both shipped routers accept it. `make_flow_router` 404s an unknown skill;
   * `make_chat_router` 400s when the wrapped agent has no skill registry to
   * pin against, so a typo is never silently ignored.
   */
  skill?: string;
  /**
   * Called with every frame as it arrives, before it is parsed: its SSE event
   * name and its data, as sent. For a protocol inspector; leave it out otherwise.
   */
  onFrame?: (frame: { event: string; data: string }) => void;
};

export type UseFlowChatReturn = {
  messages: ChatMessage[];
  threadId: string | null;
  status: "idle" | "live" | "waiting_for_widget" | "error";
  /**
   * Why the last turn failed, in words you can show the user: the server's
   * run-error message, the body of a non-2xx response, or a network failure.
   * `null` when nothing failed. Cleared by the next send and by `reset()`.
   */
  error: string | null;
  /** Last run-level state snapshot the server sent (e.g. `{ done: true }`). */
  runState: Record<string, unknown> | null;
  send: (text: string) => Promise<void>;
  submitWidget: (toolCallId: string, payload: unknown) => Promise<void>;
  reset: () => void;
  /** Append a user message without triggering the agent — useful if your UI optimistically renders user turns. */
  pushUserMessage: (text: string) => void;
};

/** Marks a widget the user answered by typing or speaking rather than by submitting it. */
export const ANSWERED_IN_CHAT = Object.freeze({ answered_in_chat: true });

export function isAnsweredInChat(submission: unknown): boolean {
  return (
    typeof submission === "object" && submission !== null &&
    (submission as { answered_in_chat?: unknown }).answered_in_chat === true
  );
}

const UNREACHABLE = "Can't reach the aigenos/flow server — is the backend running?";

/**
 * A readable reason for a non-2xx response. FastAPI answers `{"detail": "..."}`
 * or `{"detail": {"message": "...", "install": "..."}}`; a missing LLM provider
 * carries its `install` line beside `detail` instead of inside it.
 */
async function failureMessage(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown; install?: unknown } | null;
    const detail = body?.detail;
    const nested =
      typeof detail === "object" && detail !== null
        ? (detail as { message?: unknown; install?: unknown })
        : null;
    const message = typeof detail === "string" ? detail : nested?.message;
    const install = nested?.install ?? body?.install;
    if (typeof message === "string" && message) {
      return typeof install === "string" && install ? `${message}\n${install}` : message;
    }
  } catch {
    /* not JSON — fall through to the status line */
  }
  return `The server answered ${resp.status} without a reason — is the backend running? Check its console.`;
}

export function useFlowChat(options: UseFlowChatOptions = {}): UseFlowChatReturn {
  const endpoint = options.endpoint ?? "/chat";
  const protocol: Protocol = options.protocol ?? "ag-ui";
  // Kept in a ref so a new callback each render does not rebuild `send`.
  const onFrameRef = useRef(options.onFrame);
  onFrameRef.current = options.onFrame;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [threadId, setThreadId] = useState<string | null>(options.threadId ?? null);
  const [status, setStatus] = useState<UseFlowChatReturn["status"]>("idle");
  const [error, setError] = useState<string | null>(null);
  const [runState, setRunState] = useState<Record<string, unknown> | null>(null);
  const closeStreamRef = useRef<(() => void) | null>(null);

  useEffect(() => () => closeStreamRef.current?.(), []);

  /**
   * The protocol-agnostic sink. Every wire format lands here, so the
   * ChatMessage[] a caller renders is identical across all three.
   */
  const sinkFor = useCallback(
    (run: { sawWidget: boolean; awaiting: boolean | null }): ProtocolSink => ({
      runStarted: (tid) => {
        if (tid && !threadId) setThreadId(tid);
      },

      textStart: (id) =>
        setMessages((m) => [
          ...m,
          { id, kind: "text", role: "assistant", content: "", pending: true },
        ]),

      textDelta: (id, delta) =>
        setMessages((m) =>
          m.map((x) => (x.kind === "text" && x.id === id ? { ...x, content: x.content + delta } : x)),
        ),

      textEnd: (id) =>
        setMessages((m) =>
          m.map((x) => (x.kind === "text" && x.id === id ? { ...x, pending: false } : x)),
        ),

      widgetStart: (tcid) => {
        run.sawWidget = true;
        setMessages((m) => {
          // A repeated frame for the same tool call must not stack skeletons.
          if (m.some((x) => x.kind === "widget" && x.tool_call_id === tcid)) return m;
          return [
            ...m,
            {
              id: tcid,
              kind: "widget",
              tool_call_id: tcid,
              payload: { widget_id: "", version: "", spec: { type: "loading" } },
              argsBuffer: "",
              resolved: false,
            },
          ];
        });
      },

      widgetArgs: (tcid, delta) =>
        setMessages((m) =>
          m.map((x) => {
            if (x.kind !== "widget" || x.tool_call_id !== tcid) return x;
            const argsBuffer = x.argsBuffer + delta;
            let payload = x.payload;
            try {
              payload = JSON.parse(argsBuffer) as WidgetPayload;
            } catch {
              /* incremental — a partial JSON buffer is expected */
            }
            return { ...x, argsBuffer, payload };
          }),
        ),

      widgetPayload: (tcid, payload) =>
        setMessages((m) =>
          m.map((x) =>
            x.kind === "widget" && x.tool_call_id === tcid
              ? { ...x, payload, argsBuffer: JSON.stringify(payload) }
              : x,
          ),
        ),

      widgetResult: (tcid, submission) =>
        setMessages((m) =>
          m.map((x) =>
            x.kind === "widget" && x.tool_call_id === tcid
              ? { ...x, resolved: true, submission }
              : x,
          ),
        ),

      state: (snapshot) => {
        setRunState(snapshot);
        // The server says whether it is waiting, so we no longer have to guess
        // from "a widget went by": a display-only `show:` card is a widget too,
        // and guessing left the composer disabled with nothing to submit.
        if (typeof snapshot.status === "string") {
          run.awaiting = snapshot.status === "awaiting_input";
        }
      },

      runError: (message) => {
        setStatus("error");
        setError(message);
      },

      runFinished: () => {
        // Prefer the server's own verdict; fall back to "a widget went by" for
        // a router that sends no state snapshot. Per-run either way, so a later
        // run can go idle again even though an earlier one paused.
        const waiting = run.awaiting ?? run.sawWidget;
        setStatus((s) => (waiting ? "waiting_for_widget" : s === "error" ? s : "idle"));
      },
    }),
    [threadId],
  );

  const openStream = useCallback(
    async (body: { message?: string; resume_payload?: unknown }) => {
      closeStreamRef.current?.();
      setStatus("live");
      setError(null);

      const tid = threadId ?? "";
      const url = `${endpoint}?protocol=${protocol}`;
      const run = { sawWidget: false, awaiting: null as boolean | null };
      const sink = sinkFor(run);

      // Server-Sent Events doesn't support POST natively. Use fetch + ReadableStream
      // so we can POST the body and still consume the SSE stream.
      let resp: Response;
      try {
        resp = await fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            thread_id: tid || undefined,
            // Only on the opening turn — see the `skill` option's note.
            skill: tid ? undefined : options.skill,
            ...body,
          }),
        });
      } catch (err) {
        // fetch only rejects when no response arrived at all.
        setStatus("error");
        setError(UNREACHABLE);
        throw err;
      }
      if (!resp.ok || !resp.body) {
        setStatus("error");
        setError(resp.ok ? "The server sent an empty response." : await failureMessage(resp));
        throw new Error(`chat request failed: ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let cancelled = false;
      closeStreamRef.current = () => {
        cancelled = true;
        reader.cancel();
      };

      // SSE frames are separated by a blank line. sse_starlette emits
      // \r\n\r\n frame separators; the browser EventSource API normalizes
      // line endings but we are parsing the raw byte stream ourselves, so
      // we have to normalize \r\n → \n before splitting on \n\n.
      while (!cancelled) {
        let chunk: Awaited<ReturnType<typeof reader.read>>;
        try {
          chunk = await reader.read();
        } catch (err) {
          // The connection dropped mid-run. A stream we cancel ourselves ends
          // with `done` rather than throwing, so this is a real failure.
          if (!cancelled) {
            setStatus("error");
            setError(UNREACHABLE);
          }
          throw err;
        }
        const { value, done } = chunk;
        if (done) break;
        buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          let eventName = "message";
          let dataLine = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event:")) eventName = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLine += line.slice(5).trim();
          }
          if (!dataLine) continue;
          onFrameRef.current?.({ event: eventName, data: dataLine });
          try {
            handleProtocolFrame(protocol, eventName, JSON.parse(dataLine), sink);
          } catch (err) {
            // Surface in console so SSE parse failures don't get swallowed
            // silently — easier to spot frame/encoding bugs.
            console.warn("[useFlowChat] failed to parse SSE frame", err, dataLine.slice(0, 200));
          }
        }
      }
    },
    [endpoint, protocol, threadId, options.skill, sinkFor],
  );

  const send = useCallback(
    async (text: string) => {
      // Optimistically append the user turn so the UI feels responsive. A form
      // still open at this point is being answered in words instead (that is
      // how a voice conversation fills a field), so it is retired: left active
      // it would sit beside the new question looking like it still wanted input.
      setMessages((m) => [
        ...m.map((x) =>
          // Only a card that asks for something. A card that just shows data
          // (a summary, a table) stays in the chat when the user types.
          x.kind === "widget" && !x.resolved && asksForInput(x.payload?.spec)
            ? { ...x, resolved: true, submission: ANSWERED_IN_CHAT }
            : x,
        ),
        { id: `u-${Date.now()}`, kind: "text", role: "user", content: text },
      ]);
      await openStream({ message: text });
    },
    [openStream],
  );

  const submitWidget = useCallback(
    async (toolCallId: string, payload: unknown) => {
      setMessages((m) =>
        m.map((x) =>
          x.kind === "widget" && x.tool_call_id === toolCallId
            ? { ...x, resolved: true, submission: payload }
            : x,
        ),
      );
      await openStream({ resume_payload: payload as Record<string, unknown> });
    },
    [openStream],
  );

  // A flow waiting on someone else's yes (`confirm:` with `by:`) shows a card
  // nobody here can answer. Look in on the request every few seconds, and when
  // it has been answered let the flow take its next turn.
  const waitingOn = (() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m.kind === "widget" && !m.resolved && m.payload?.widget_id?.startsWith("approval:")) {
        return { call: m.tool_call_id, request: m.payload.widget_id.slice("approval:".length) };
      }
    }
    return null;
  })();
  const waitingCall = waitingOn?.call ?? null;
  const waitingRequest = waitingOn?.request ?? null;
  useEffect(() => {
    if (!waitingCall || !waitingRequest || status === "live") return;
    const base = endpoint.replace(/\/$/, "");
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const r = await fetch(`${base}/approvals/${encodeURIComponent(waitingRequest)}/status`);
        if (!r.ok || stopped) return;
        const answer = await r.json();
        if (answer.status && answer.status !== "pending") {
          stopped = true;
          window.clearInterval(timer);
          void submitWidget(waitingCall, { _approval: waitingRequest });
        }
      } catch { /* looked in and nobody was home: try again */ }
    }, 2500);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [waitingCall, waitingRequest, status, endpoint, submitWidget]);

  const reset = useCallback(() => {
    closeStreamRef.current?.();
    setMessages([]);
    setThreadId(options.threadId ?? null);
    setStatus("idle");
    setError(null);
    setRunState(null);
  }, [options.threadId]);

  const pushUserMessage = useCallback((text: string) => {
    setMessages((m) => [...m, { id: `u-${Date.now()}`, kind: "text", role: "user", content: text }]);
  }, []);

  return { messages, threadId, status, error, runState, send, submitWidget, reset, pushUserMessage };
}

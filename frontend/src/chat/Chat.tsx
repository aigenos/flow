/**
 * Chat — the drop-in chat shell.
 *
 *   import { Chat } from "@aigenos/flow";
 *   import "@aigenos/flow/style.css";   // only needed outside a bundler
 *
 *   <Chat endpoint="/chat" />
 *
 * `endpoint` is the URL of YOUR backend's chat router — `make_chat_router(...)`
 * or `make_flow_router(...)` mounted wherever you like. Everything else is
 * optional. The component owns the transport (useFlowChat), the transcript and
 * the composer; widgets the engine pauses on render inline through the same
 * <Widget> renderer you get for tier 3.
 *
 * Nothing here talks to any URL you did not pass in.
 */
import { useCallback, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Send, RotateCw, Sparkles } from "lucide-react";
import { MessageList } from "./MessageList";
import { MicButton } from "./MicButton";
import { VoicePanel, VoiceToggle } from "./VoiceConversation";
import { useVoiceConversation } from "../hooks/useVoiceConversation";
import { useFlowChat } from "../hooks/useFlowChat";
import { FlowConfigProvider } from "../renderer/FlowConfig";
import { cn } from "../lib/cn";

export type ChatProps = {
  /** URL of your chat router. Default: "/chat" */
  endpoint?: string;
  /**
   * Wire protocol the backend should emit. All three are parsed by the
   * transport underneath (`useFlowChat`), which dispatches every frame through
   * the shared parser in `../agui/protocol`. Default: "ag-ui".
   */
  protocol?: "ag-ui" | "a2ui" | "mcp-ui";
  /**
   * Enter this skill directly instead of letting the first-turn classifier
   * pick one. Honoured by `make_flow_router`; see its `skill` support.
   */
  skill?: string;
  /** Resume an existing conversation (e.g. a thread id from localStorage). */
  threadId?: string;
  /** Composer placeholder. */
  placeholder?: string;
  /** Replaces the default "start a conversation" panel. */
  emptyState?: ReactNode;
  /** Extra classes on the root element. */
  className?: string;
  /**
   * Turn on inline per-field validation by pointing at your flow router's
   * `validate-field` endpoint (e.g. "/api/flow/validate-field"). Off by
   * default — the renderer never guesses a URL in someone else's app.
   */
  validateEndpoint?: string | false;
  /** Called when a turn fails (network error, non-2xx from the router). */
  onError?: (error: unknown) => void;
  /**
   * Voice input. Two controls, both using the browser's own speech services
   * and both hidden where the browser has none:
   *   - a microphone that DICTATES into the message box (it never sends);
   *   - "Talk", a hands-free conversation: it listens, sends, speaks the answer
   *     and listens again. A confirmation, a secret or a pick from a grid is
   *     never accepted by voice — the bot asks and waits for it on screen.
   * Default: true.
   */
  voice?: boolean;
};

export function Chat({
  endpoint = "/chat",
  protocol = "ag-ui",
  skill,
  threadId,
  placeholder,
  emptyState,
  className,
  validateEndpoint = false,
  onError,
  voice = true,
}: ChatProps = {}) {
  const [input, setInput] = useState("");
  const { messages, status, error, send, submitWidget, reset } = useFlowChat({
    endpoint,
    protocol,
    skill,
    threadId,
  });
  const conversation = useVoiceConversation({ messages, status, send });

  const guard = useCallback(
    (p: Promise<void>) => {
      p.catch((err) => {
        if (onError) onError(err);
        else console.error("[aigenos/flow] chat turn failed", err);
      });
    },
    [onError],
  );

  const onSubmit = useCallback(
    (e: FormEvent) => {
      e.preventDefault();
      const text = input.trim();
      if (!text || status === "live") return;
      setInput("");
      guard(send(text));
    },
    [input, status, send, guard],
  );

  const onWidgetSubmit = useCallback(
    (toolCallId: string, payload: unknown) => {
      guard(submitWidget(toolCallId, payload));
    },
    [submitWidget, guard],
  );

  const busy = status === "live";
  const awaitingWidget = status === "waiting_for_widget";

  return (
    <div className={cn("flex h-full min-h-0 flex-col theme-transition", className)}>
      <FlowConfigProvider value={{ validateEndpoint, skill }}>
        <MessageList
          messages={messages}
          onWidgetSubmit={onWidgetSubmit}
          protocol={protocol}
          emptyState={emptyState ?? <DefaultEmpty />}
        />
      </FlowConfigProvider>

      <form onSubmit={onSubmit} className="glass border-t border-border-subtle">
        <div className="mx-auto flex max-w-3xl flex-col gap-1.5 px-4 py-3.5 sm:px-6">
          {voice && <VoicePanel voice={conversation} />}
          <div className="flex items-stretch gap-2 rounded-2xl border border-border bg-bg-surface/70 p-1.5">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              aria-label="Message"
              placeholder={
                placeholder ??
                (awaitingWidget ? "Answer above, or type to change something…" : "Type a message…")
              }
              // A form on screen is one way to answer, not the only one: a typed
              // correction is sent to the flow, which replays with it.
              className="min-w-0 flex-1 rounded-xl bg-transparent px-3 py-2 text-sm text-text-primary outline-none placeholder:text-text-muted"
            />
            {voice && !conversation.on && (
              <MicButton value={input} onChange={setInput} />
            )}
            {voice && <VoiceToggle voice={conversation} />}
            <button
              type="submit"
              disabled={!input.trim() || busy}
              className="control-press inline-flex items-center gap-2 rounded-xl bg-brand px-4 py-2 text-sm font-semibold text-brand-fg shadow-glow hover:bg-brand-hover focus-ring disabled:opacity-50 disabled:shadow-none"
            >
              <Send className="h-4 w-4" aria-hidden /> Send
            </button>
            {messages.length > 0 && (
              <button
                type="button"
                onClick={reset}
                aria-label="Start a new conversation"
                title="Start a new conversation"
                className="control-press inline-flex items-center justify-center rounded-xl border border-border bg-bg-elevated px-3 py-2 text-text-secondary hover:border-border-strong hover:text-text-primary focus-ring"
              >
                <RotateCw className="h-4 w-4" aria-hidden />
              </button>
            )}
          </div>
          {status === "error" ? (
            <span role="alert" className="text-[11px] whitespace-pre-line text-rose-500">
              {error ?? "The last turn failed — check your backend and try again."}
            </span>
          ) : null}
        </div>
      </form>
    </div>
  );
}

function DefaultEmpty() {
  return (
    <div className="py-20 text-center animate-fade-in">
      <div className="mb-4 inline-flex h-14 w-14 items-center justify-center rounded-2xl border border-brand/20 bg-brand-subtle shadow-card">
        <Sparkles className="h-6 w-6 text-brand" aria-hidden />
      </div>
      <h2 className="text-lg font-semibold tracking-tight text-text-primary">
        Start a <span className="grad-text">conversation</span>
      </h2>
      <p className="mx-auto mt-1.5 max-w-md text-sm text-text-secondary">
        Say what you want to do. When the flow needs structured input it renders a widget here
        instead of asking you to type it.
      </p>
    </div>
  );
}

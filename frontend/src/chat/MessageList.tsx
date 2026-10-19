import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Sparkles, Radio } from "lucide-react";
import { Message } from "./Message";
import type { ChatMessage, SpecNode } from "../agui/types";
import type { Protocol } from "../agui/client";
import { isAnsweredInChat } from "../hooks/useFlowChat";

const FIELD_TYPES = new Set(["input", "select", "autosuggest"]);
const norm = (s: string) => s.trim().toLowerCase();

/** The question a widget asks: its card title, and its label when it has exactly one field. */
function questionsOf(spec: SpecNode | undefined): string[] {
  const out: string[] = [];
  const fields: SpecNode[] = [];
  const walk = (node: SpecNode | undefined) => {
    if (!node) return;
    const title = node.props?.title;
    if (node.type === "card" && typeof title === "string") out.push(norm(title));
    if (FIELD_TYPES.has(node.type)) fields.push(node);
    node.children?.forEach(walk);
  };
  walk(spec);
  const label = fields.length === 1 ? fields[0].props?.label : undefined;
  if (typeof label === "string") out.push(norm(label));
  return out;
}

/**
 * A flow asks its question twice on the wire — once as assistant text (for
 * clients that render no widgets) and once inside the widget. Shown together
 * the transcript reads as the bot repeating itself, so the text is hidden when
 * the widget directly below it already asks the same thing.
 */
function withoutEchoedQuestions(messages: ChatMessage[]): ChatMessage[] {
  return messages.filter((m, i) => {
    // A form the user answered in words is gone; its question stays as text.
    if (m.kind === "widget") return !isAnsweredInChat(m.submission);
    if (m.role !== "assistant") return true;
    // Look through the run of widgets that follows: a result card can sit
    // between a question's text and the form that asks it.
    for (let j = i + 1; j < messages.length; j += 1) {
      const next = messages[j];
      if (next.kind !== "widget") break;
      if (isAnsweredInChat(next.submission)) continue;
      if (questionsOf(next.payload?.spec).includes(norm(m.content))) return false;
    }
    return true;
  });
}

type Props = {
  messages: ChatMessage[];
  onWidgetSubmit: (toolCallId: string, payload: unknown) => void;
  protocol?: Protocol;
  /** Shown while the transcript is empty. Defaults to the protocol blurb. */
  emptyState?: ReactNode;
};

export function MessageList({ messages, onWidgetSubmit, protocol = "ag-ui", emptyState }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // `scrollIntoView` is missing in jsdom, so a consumer unit-testing their
    // chat would otherwise crash on mount. Feature-detect instead of assuming.
    endRef.current?.scrollIntoView?.({ behavior: "smooth", block: "end" });
  }, [messages]);

  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-3xl px-6 py-8 space-y-5">
        {messages.length === 0 && (emptyState ?? <Empty protocol={protocol} />)}
        {withoutEchoedQuestions(messages).map((m) => (
          <Message key={m.id} message={m} onWidgetSubmit={onWidgetSubmit} />
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Empty({ protocol }: { protocol: Protocol }) {
  const label =
    protocol === "ag-ui" ? "AG-UI" : protocol === "a2ui" ? "A2UI" : "MCP-UI";
  return (
    <div className="text-center py-20 animate-fade-in">
      <div className="inline-flex items-center justify-center h-14 w-14 rounded-2xl bg-gradient-to-br from-brand to-brand-hover shadow-glow mb-4">
        <Sparkles className="h-6 w-6 text-brand-fg" />
      </div>
      <h2 className="text-lg font-semibold tracking-tight text-text-primary">
        Pick a workflow to <span className="grad-text">begin</span>
      </h2>
      <p className="mt-1.5 text-sm text-text-secondary max-w-md mx-auto">
        Widgets stream live over <span className="text-brand font-medium">{label}</span> as the
        agent walks the workflow. Switch protocols anytime to see the same flow on the wire below.
      </p>
      <div className="mt-4 flex items-center justify-center gap-2">
        {(["AG-UI", "A2UI", "MCP-UI"] as const).map((p) => (
          <span
            key={p}
            className={
              p === label
                ? "rounded-full border border-brand/40 bg-brand-subtle px-2.5 py-0.5 text-[11px] font-semibold text-brand"
                : "rounded-full border border-border-subtle bg-bg-muted/70 px-2.5 py-0.5 text-[11px] font-medium text-text-muted"
            }
          >
            {p}
          </span>
        ))}
      </div>
      <p className="mt-3 text-[12px] text-text-muted inline-flex items-center gap-1">
        <Radio className="h-3 w-3" />
        Same backend, same widgets, three protocols
      </p>
    </div>
  );
}

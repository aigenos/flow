import { Bot, Check, User, Zap } from "lucide-react";
import { asksForInput } from "../lib/asksForInput";
import { cn } from "../lib/cn";
import { SpecRenderer } from "../renderer/SpecRenderer";
import type { ChatMessage } from "../agui/types";

type Props = {
  message: ChatMessage;
  onWidgetSubmit: (toolCallId: string, payload: unknown) => void;
};

export function Message({ message, onWidgetSubmit }: Props) {
  if (message.kind === "text") {
    const isUser = message.role === "user";
    return (
      <div className={cn("flex gap-3", isUser && "flex-row-reverse")}>
        <Avatar role={message.role} />
        <div
          className={cn(
            "max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
            isUser
              ? "bg-gradient-to-br from-brand to-brand-hover text-brand-fg shadow-[0_8px_24px_-8px_rgb(var(--brand)/0.5)]"
              : "bg-bg-elevated/80 backdrop-blur-sm border border-border-subtle text-text-primary shadow-sm",
          )}
        >
          {message.content || (message.pending && <Typing />)}
        </div>
      </div>
    );
  }

  // Interactive widget message — framed so it reads as a first-class object
  // in the transcript, with a state chip that flips once submitted. A card that
  // only shows data gets no chip, so it never reads "awaiting input".
  const wantsInput = asksForInput(message.payload.spec);
  const waiting = wantsInput && !message.resolved;
  return (
    <div className="flex gap-3">
      <Avatar role="assistant" />
      <div className="flex-1 min-w-0 max-w-[88%]">
        <div
          className={cn(
            "rounded-2xl border bg-bg-surface/70 backdrop-blur-md shadow-card overflow-hidden transition-colors",
            waiting ? "border-brand/25" : "border-border-subtle",
          )}
        >
          <div className="flex items-center gap-1.5 border-b border-border-subtle/70 px-3.5 py-1.5">
            <Zap className={cn("h-3 w-3", waiting ? "text-brand" : "text-text-muted")} />
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
              widget
            </span>
            <span className="ml-auto">
              {!wantsInput ? null : message.resolved ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-px text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                  <Check className="h-2.5 w-2.5" /> submitted
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full border border-brand/30 bg-brand-subtle px-2 py-px text-[10px] font-medium text-brand">
                  <span className="h-1 w-1 rounded-full bg-brand animate-pulse" /> awaiting input
                </span>
              )}
            </span>
          </div>
          <div className="p-3.5">
            {message.payload.spec && message.payload.spec.type !== "loading" ? (
              <SpecRenderer
                spec={message.payload.spec}
                disabled={message.resolved}
                onSubmit={(p) => !message.resolved && onWidgetSubmit(message.tool_call_id, p)}
              />
            ) : (
              <div className="h-20 rounded-xl border border-border-subtle bg-[linear-gradient(90deg,transparent,rgb(var(--text-muted)/0.08),transparent)] bg-[length:200%_100%] animate-shimmer" />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Avatar({ role }: { role: "assistant" | "user" }) {
  return (
    <div
      className={cn(
        "h-8 w-8 shrink-0 rounded-xl grid place-items-center border shadow-sm",
        role === "user"
          ? "bg-gradient-to-br from-brand to-brand-hover border-brand/30 text-brand-fg"
          : "bg-brand-subtle border-brand/25 text-brand",
      )}
    >
      {role === "user" ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
    </div>
  );
}

/** Three-dot typing indicator (staggered bounce). */
function Typing() {
  return (
    <span className="inline-flex items-center gap-1 py-0.5" role="status" aria-label="Assistant is typing">
      {[0, 150, 300].map((delay) => (
        <span
          key={delay}
          className="h-1.5 w-1.5 rounded-full bg-text-secondary/70 animate-bounce"
          style={{ animationDelay: `${delay}ms`, animationDuration: "900ms" }}
        />
      ))}
    </span>
  );
}

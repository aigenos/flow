import { AudioLines, Hand, Mic, Square, X } from "lucide-react";
import { cn } from "../lib/cn";
import type { UseVoiceConversationReturn, VoicePhase } from "../hooks/useVoiceConversation";

const LABEL: Record<VoicePhase, string> = {
  off: "",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking — tap to interrupt",
  on_screen: "Answer on screen to continue",
  paused: "Paused — tap to keep talking",
};

/** The composer button that starts or ends a voice conversation. */
export function VoiceToggle({
  voice,
  className,
}: {
  voice: UseVoiceConversationReturn;
  className?: string;
}) {
  if (!voice.supported) return null;
  const label = voice.on ? "End voice conversation" : "Talk — have a voice conversation";
  return (
    <button
      type="button"
      onClick={voice.on ? voice.stop : voice.start}
      aria-pressed={voice.on}
      aria-label={label}
      title={label}
      className={cn(
        "control-press inline-flex shrink-0 items-center justify-center gap-1.5 rounded-xl border px-3 py-2 text-sm font-medium focus-ring",
        voice.on
          ? "border-brand bg-brand text-brand-fg shadow-glow"
          : "border-border bg-bg-elevated text-text-secondary hover:border-border-strong hover:text-text-primary",
        className,
      )}
    >
      {voice.on ? <Square className="h-4 w-4" aria-hidden /> : <AudioLines className="h-4 w-4" aria-hidden />}
      <span className="hidden sm:inline">{voice.on ? "End" : "Talk"}</span>
    </button>
  );
}

/** The live panel shown above the composer while a voice conversation is on. */
export function VoicePanel({ voice }: { voice: UseVoiceConversationReturn }) {
  if (!voice.on) return null;
  const { phase } = voice;
  const tappable = phase === "speaking" || phase === "paused";
  return (
    <div
      role="status"
      aria-live="polite"
      className="mx-auto mb-2 flex w-full max-w-3xl items-center gap-3 rounded-2xl border border-brand/30 bg-bg-surface/80 px-4 py-2.5 shadow-card backdrop-blur-md"
    >
      <button
        type="button"
        onClick={tappable ? voice.interrupt : undefined}
        disabled={!tappable}
        aria-label={tappable ? "Interrupt and listen" : LABEL[phase]}
        className={cn(
          "relative inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full",
          phase === "listening" && "bg-brand text-brand-fg",
          phase === "speaking" && "bg-brand/15 text-brand",
          phase === "thinking" && "bg-bg-muted text-text-secondary",
          (phase === "on_screen" || phase === "paused") && "bg-amber-500/15 text-amber-500",
          tappable && "cursor-pointer",
        )}
      >
        {phase === "listening" && (
          <span className="absolute inset-0 rounded-full bg-brand/40 animate-ping" aria-hidden />
        )}
        {phase === "on_screen" ? (
          <Hand className="relative h-4 w-4" aria-hidden />
        ) : phase === "speaking" || phase === "thinking" ? (
          <span className="voice-bars relative flex h-4 items-center gap-[3px]" aria-hidden>
            {[0, 1, 2, 3].map((i) => (
              <span
                key={i}
                className={cn("w-[3px] rounded-full bg-current", phase === "thinking" && "opacity-60")}
                style={{ height: "100%", animationDelay: `${i * 0.13}s` }}
              />
            ))}
          </span>
        ) : (
          <Mic className="relative h-4 w-4" aria-hidden />
        )}
      </button>

      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
          {voice.error ?? LABEL[phase]}
        </div>
        <div className="truncate text-sm text-text-primary">
          {phase === "listening"
            ? voice.heard || "Say something — I'm listening."
            : phase === "on_screen"
              ? "This step is confirmed by hand, not by voice."
              : phase === "paused"
                ? "I stopped listening after a few quiet moments."
                : " "}
        </div>
      </div>

      <button
        type="button"
        onClick={voice.stop}
        aria-label="End voice conversation"
        title="End voice conversation"
        className="control-press inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-text-muted hover:bg-bg-muted hover:text-text-primary focus-ring"
      >
        <X className="h-4 w-4" aria-hidden />
      </button>
    </div>
  );
}

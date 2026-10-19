import { useRef } from "react";
import { Mic, Square } from "lucide-react";
import { cn } from "../lib/cn";
import { useSpeechInput } from "../hooks/useSpeechInput";

type Props = {
  /** Current text of the box this button dictates into. */
  value: string;
  /** Receives the box's new text: what was there, plus what was just said. */
  onChange: (next: string) => void;
  disabled?: boolean;
  /** BCP-47 tag, e.g. "en-US". Defaults to the browser's language. */
  lang?: string;
  className?: string;
};

/**
 * Speak instead of type. Dictates INTO the text box — it never sends: the user
 * reads what was heard and presses Send themselves.
 *
 * Renders nothing where the browser has no speech recognition.
 */
export function MicButton({ value, onChange, disabled, lang, className }: Props) {
  // What was already typed when dictation began; speech is appended to it.
  const before = useRef("");
  const { supported, listening, error, start, stop } = useSpeechInput({
    lang,
    onText: (text) => onChange(before.current ? `${before.current} ${text}`.trim() : text),
  });

  if (!supported) return null;

  const label = listening ? "Stop voice input" : "Speak your message";
  return (
    <>
      <button
        type="button"
        onClick={() => {
          if (listening) {
            stop();
          } else {
            before.current = value.trim();
            start();
          }
        }}
        disabled={disabled}
        aria-pressed={listening}
        aria-label={label}
        title={error ?? (listening ? "Listening… click to stop" : label)}
        className={cn(
          "control-press inline-flex shrink-0 items-center justify-center rounded-xl border bg-bg-elevated px-3 py-2 focus-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          listening
            ? "animate-pulse border-brand text-brand"
            : "border-border text-text-secondary hover:border-border-strong hover:text-text-primary",
          className,
        )}
      >
        {listening ? <Square className="h-4 w-4" aria-hidden /> : <Mic className="h-4 w-4" aria-hidden />}
      </button>
      {/* Announced, and available as the button's tooltip; no layout shift. */}
      <span role="status" aria-live="polite" className="sr-only">
        {error ?? (listening ? "Listening" : "")}
      </span>
    </>
  );
}

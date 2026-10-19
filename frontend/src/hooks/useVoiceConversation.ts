/**
 * A hands-free voice conversation over a chat: listen, send, speak the answer,
 * listen again.
 *
 * What is spoken is sent as an ordinary chat MESSAGE — the same path as typing
 * it — so the engine's rules apply unchanged: a gate input must be something
 * the user actually said, and a confirmation is only accepted on the turn the
 * flow asked for it.
 *
 * THE ONE THING VOICE MAY NOT DO is answer a step that commits or reveals
 * something: a yes/no confirmation, a secret (a one-time code), or a choice
 * from a grid. A recognizer that mishears "no" as "now" must not be able to
 * move money. For those the bot asks out loud, stops listening, and waits for
 * the answer on screen.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage, SpecNode } from "../agui/types";
import { asksForInput } from "../lib/asksForInput";
import { useSpeechInput } from "./useSpeechInput";

export type VoicePhase =
  | "off"
  | "listening"
  | "thinking"
  | "speaking"
  /** The pending step must be answered on screen. */
  | "on_screen"
  /** Heard nothing for a while; waiting for a tap to resume. */
  | "paused";

type Status = "idle" | "live" | "waiting_for_widget" | "error";

export type UseVoiceConversationOptions = {
  messages: ChatMessage[];
  status: Status;
  send: (text: string) => Promise<void> | void;
  /** BCP-47 tag, e.g. "en-US". Defaults to the browser's language. */
  lang?: string;
};

export type UseVoiceConversationReturn = {
  /** False where the browser lacks speech recognition or speech synthesis. */
  supported: boolean;
  on: boolean;
  phase: VoicePhase;
  /** What is being heard right now, as it firms up. */
  heard: string;
  error: string | null;
  start: () => void;
  stop: () => void;
  /** Cut the bot off mid-sentence and listen (or resume from a pause). */
  interrupt: () => void;
};

const FIELD_TYPES = new Set(["input", "select", "autosuggest"]);
const SILENT_ROUNDS_BEFORE_PAUSE = 3;

function fieldsOf(spec: SpecNode | undefined): SpecNode[] {
  const out: SpecNode[] = [];
  const walk = (node: SpecNode | undefined) => {
    if (!node) return;
    if (FIELD_TYPES.has(node.type)) out.push(node);
    node.children?.forEach(walk);
  };
  walk(spec);
  return out;
}

function questionOf(spec: SpecNode | undefined): string {
  const fields = fieldsOf(spec);
  const label = fields.length === 1 ? fields[0].props?.label : undefined;
  if (typeof label === "string" && label.trim()) return label.trim();
  const title = spec?.props?.title;
  return typeof title === "string" ? title.trim() : "";
}

/** May this pending widget be answered by speaking? Only a single plain field. */
export function isVoiceAnswerable(spec: SpecNode | undefined): boolean {
  const fields = fieldsOf(spec);
  if (fields.length !== 1) return false; // a grid, a multi-field form, a card
  const field = fields[0];
  const props = field.props ?? {};
  if (props.type === "password" || props.sensitive === true) return false;
  if (field.type === "select") {
    const options = Array.isArray(props.options) ? props.options : [];
    const values = options.map((o) => String((o as { value?: unknown })?.value));
    const yesNo = values.length === 2 && values.includes("true") && values.includes("false");
    if (yesNo) return false; // a confirmation — tapped, never spoken
  }
  return true;
}

function pendingWidget(messages: ChatMessage[]): SpecNode | undefined {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.kind === "widget" && !m.resolved) return m.payload?.spec;
  }
  return undefined;
}

/** Text for the speech synthesizer: no markup, no symbols it would read aloud. */
function forSpeech(text: string): string {
  return text
    .replace(/[*_`#>]/g, "")
    .replace(/→/g, " to ")
    .replace(/—/g, ", ")
    .replace(/\s+/g, " ")
    .trim();
}

export function useVoiceConversation({
  messages,
  status,
  send,
  lang,
}: UseVoiceConversationOptions): UseVoiceConversationReturn {
  const [supported, setSupported] = useState(false);
  const [on, setOn] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>("off");
  const [heard, setHeard] = useState("");

  // Mutable state the callbacks need without re-subscribing.
  const onRef = useRef(false);
  const spokenUpTo = useRef(0); // messages before this index are already spoken
  const speaking = useRef(false);
  const silentRounds = useRef(0);
  const latest = useRef({ messages, status, send });
  latest.current = { messages, status, send };
  const afterSpeechRef = useRef<() => void>(() => {});

  const recognizer = useSpeechInput({
    lang,
    onText: (text) => setHeard(text),
    onEnd: (finalText) => {
      if (!onRef.current || speaking.current) return;
      const text = finalText.trim();
      setHeard("");
      if (text) {
        silentRounds.current = 0;
        setPhase("thinking");
        void latest.current.send(text);
        return;
      }
      // Silence. Try again a few times, then stop burning the microphone.
      silentRounds.current += 1;
      if (silentRounds.current >= SILENT_ROUNDS_BEFORE_PAUSE) {
        setPhase("paused");
      } else if (latest.current.status !== "live") {
        window.setTimeout(() => onRef.current && listen(), 350);
      }
    },
  });
  const recognizerRef = useRef(recognizer);
  recognizerRef.current = recognizer;

  const listen = useCallback(() => {
    if (!onRef.current || speaking.current) return;
    setHeard("");
    setPhase("listening");
    recognizerRef.current.start();
  }, []);

  useEffect(() => {
    setSupported(
      typeof window !== "undefined" &&
        "speechSynthesis" in window &&
        typeof window.SpeechSynthesisUtterance === "function",
    );
    return () => {
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  /** Say each line, then decide what happens next. */
  const speak = useCallback((lines: string[]) => {
    const texts = lines.map(forSpeech).filter(Boolean);
    if (!texts.length) {
      afterSpeechRef.current();
      return;
    }
    speaking.current = true;
    setPhase("speaking");
    recognizerRef.current.stop(); // never listen to our own voice
    window.speechSynthesis.cancel();
    texts.forEach((text, i) => {
      const utterance = new SpeechSynthesisUtterance(text);
      if (lang) utterance.lang = lang;
      utterance.rate = 1.04;
      if (i === texts.length - 1) {
        const finish = () => {
          if (!speaking.current) return;
          speaking.current = false;
          afterSpeechRef.current();
        };
        utterance.onend = finish;
        utterance.onerror = finish;
      }
      window.speechSynthesis.speak(utterance);
    });
  }, [lang]);

  afterSpeechRef.current = () => {
    if (!onRef.current) return;
    const { status: now, messages: all } = latest.current;
    if (now === "live") {
      setPhase("thinking");
    } else if (now === "waiting_for_widget" && !isVoiceAnswerable(pendingWidget(all))) {
      setPhase("on_screen");
    } else if (now === "error") {
      setPhase("paused");
    } else {
      listen();
    }
  };

  // Speak whatever the bot has said since we last spoke, once a turn settles.
  useEffect(() => {
    if (!on) return;
    if (status === "live") {
      if (!speaking.current) setPhase("thinking");
      return;
    }
    if (speaking.current) return;

    // The wire carries a question twice — as text and inside its widget — and
    // a display-only card has nothing to ask. Say each thing once.
    const lines: string[] = [];
    const said = new Set<string>();
    for (let i = spokenUpTo.current; i < messages.length; i += 1) {
      const m = messages[i];
      let line = "";
      if (m.kind === "text" && m.role === "assistant") line = m.content;
      else if (m.kind === "widget" && !m.resolved && asksForInput(m.payload?.spec)) {
        line = questionOf(m.payload?.spec);
      }
      const key = line.trim().toLowerCase();
      if (key && !said.has(key)) {
        said.add(key);
        lines.push(line);
      }
    }
    spokenUpTo.current = messages.length;

    const pending = status === "waiting_for_widget" ? pendingWidget(messages) : undefined;
    if (pending && !isVoiceAnswerable(pending) && lines.length) {
      lines.push("Please answer that one on screen.");
    }
    if (lines.length) speak(lines);
    else if (phase === "thinking" || phase === "on_screen") afterSpeechRef.current();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [on, messages, status]);

  const start = useCallback(() => {
    if (onRef.current) return;
    onRef.current = true;
    silentRounds.current = 0;
    spokenUpTo.current = latest.current.messages.length; // don't read old history aloud
    setOn(true);
    afterSpeechRef.current();
  }, []);

  const stop = useCallback(() => {
    onRef.current = false;
    speaking.current = false;
    recognizerRef.current.stop();
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    setOn(false);
    setHeard("");
    setPhase("off");
  }, []);

  const interrupt = useCallback(() => {
    if (!onRef.current) return;
    speaking.current = false;
    window.speechSynthesis.cancel();
    silentRounds.current = 0;
    afterSpeechRef.current();
  }, []);

  return {
    supported: supported && recognizer.supported,
    on,
    phase,
    heard,
    error: recognizer.error,
    start,
    stop,
    interrupt,
  };
}

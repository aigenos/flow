/**
 * Voice input for any text box, on top of the browser's own speech
 * recognition (the Web Speech API: Chrome, Edge, Safari).
 *
 * Deliberately dictation, not a voice assistant: speech becomes TEXT IN THE
 * BOX, which the user reads and then sends. A flow can move money on a "yes",
 * so nothing is ever submitted on the strength of a transcription alone.
 *
 * Where the browser has no recognizer (Firefox), `supported` is false and
 * callers render nothing rather than a dead button.
 */
import { useCallback, useEffect, useRef, useState } from "react";

// The Web Speech API is not in TypeScript's DOM lib, so the slice used here
// is typed by hand.
type RecognitionAlternative = { transcript: string };
type RecognitionResult = ArrayLike<RecognitionAlternative> & { isFinal: boolean };
type RecognitionEvent = { results: ArrayLike<RecognitionResult> };
type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  onresult: ((event: RecognitionEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | undefined {
  if (typeof window === "undefined") return undefined;
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition;
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access is blocked. Allow it in your browser's site settings.",
  "service-not-allowed": "Microphone access is blocked. Allow it in your browser's site settings.",
  "audio-capture": "No microphone was found.",
  network: "Voice input needs a network connection in this browser.",
};

export type UseSpeechInputOptions = {
  /** BCP-47 tag, e.g. "en-US". Defaults to the browser's language. */
  lang?: string;
  /** Called with everything heard so far in this utterance, as it firms up. */
  onText: (text: string, isFinal: boolean) => void;
  /** Called when the recognizer stops, with the last text it produced ("" for silence). */
  onEnd?: (finalText: string) => void;
};

export type UseSpeechInputReturn = {
  /** False where the browser has no speech recognizer. */
  supported: boolean;
  listening: boolean;
  /** A sentence for the user, or null. Cleared on the next start. */
  error: string | null;
  start: () => void;
  stop: () => void;
};

export function useSpeechInput({ lang, onText, onEnd }: UseSpeechInputOptions): UseSpeechInputReturn {
  // Decided after mount so server-rendered markup and the first client render agree.
  const [supported, setSupported] = useState(false);
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognition = useRef<Recognition | null>(null);
  const onTextRef = useRef(onText);
  onTextRef.current = onText;
  const onEndRef = useRef(onEnd);
  onEndRef.current = onEnd;
  const lastText = useRef("");

  useEffect(() => {
    setSupported(recognitionCtor() !== undefined);
    return () => {
      recognition.current?.abort();
      recognition.current = null;
    };
  }, []);

  const stop = useCallback(() => {
    recognition.current?.stop();
  }, []);

  const start = useCallback(() => {
    const Ctor = recognitionCtor();
    if (!Ctor || recognition.current) return;

    const rec = new Ctor();
    rec.lang = lang ?? ((typeof navigator !== "undefined" && navigator.language) || "en-US");
    rec.continuous = false; // one utterance; it ends itself on a pause
    rec.interimResults = true; // words appear as they are recognised
    rec.maxAlternatives = 1;

    rec.onresult = (event) => {
      let text = "";
      let isFinal = true;
      for (let i = 0; i < event.results.length; i += 1) {
        const result = event.results[i];
        text += result[0]?.transcript ?? "";
        if (!result.isFinal) isFinal = false;
      }
      lastText.current = text.trim();
      onTextRef.current(lastText.current, isFinal);
    };
    rec.onerror = (event) => {
      // "no-speech" and "aborted" are the user pausing or cancelling, not faults.
      if (event.error === "no-speech" || event.error === "aborted") return;
      setError(ERRORS[event.error] ?? `Voice input stopped (${event.error}).`);
    };
    rec.onend = () => {
      recognition.current = null;
      setListening(false);
      onEndRef.current?.(lastText.current);
    };

    recognition.current = rec;
    lastText.current = "";
    setError(null);
    try {
      rec.start();
      setListening(true);
    } catch {
      recognition.current = null;
      setError("Voice input could not start.");
    }
  }, [lang]);

  return { supported, listening, error, start, stop };
}

/**
 * ProtocolPlayground — run any skill of this agent over AG-UI, A2UI or MCP-UI,
 * type to it like a user, and read every frame the server sends: exactly what
 * your own frontend would receive.
 *
 * It talks to the same flow endpoint as the Flow page. Changing the protocol
 * or the skill starts a fresh conversation, so the frames on screen always
 * belong to one wire format and one run.
 */
import { useCallback, useEffect, useState } from "react";
import { Info, RotateCcw, Send } from "lucide-react";
import { cn } from "../lib/cn";
import { MessageList } from "./MessageList";
import { ProtocolViewer } from "./ProtocolViewer";
import { FlowConfigProvider } from "../renderer/FlowConfig";
import { useFlowChat } from "../hooks/useFlowChat";
import type { Protocol, RawFrame } from "../agui/client";

const PROTOCOLS: Array<{ value: Protocol; label: string; tagline: string }> = [
  { value: "ag-ui", label: "AG-UI", tagline: "AG-UI events: text messages, and each widget as a render_widget tool call." },
  { value: "a2ui", label: "A2UI", tagline: "A2UI: the same widgets as ui_resource events." },
  { value: "mcp-ui", label: "MCP-UI", tagline: "MCP-UI: the same widgets as ui:// resources." },
];

type SkillItem = { name: string; description?: string };

export function ProtocolPlayground() {
  const [protocol, setProtocol] = useState<Protocol>("ag-ui");
  const [skill, setSkill] = useState<string>("");
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [frames, setFrames] = useState<RawFrame[]>([]);
  const [text, setText] = useState("");

  useEffect(() => {
    fetch("/api/flow/skills")
      .then((r) => (r.ok ? r.json() : { skills: [] }))
      .then((j) => setSkills(Array.isArray(j.skills) ? j.skills : []))
      .catch(() => setSkills([]));
  }, []);

  const onFrame = useCallback((frame: { event: string; data: string }) => {
    setFrames((fs) => [...fs, { ts: Date.now(), event: frame.event, data: frame.data }]);
  }, []);

  const { messages, status, error, send, submitWidget, reset } = useFlowChat({
    endpoint: "/api/flow",
    protocol,
    skill: skill || undefined,
    onFrame,
  });

  // A new protocol or skill is a new conversation: one run, one wire format.
  const restart = useCallback(() => { reset(); setFrames([]); }, [reset]);
  useEffect(() => { restart(); }, [protocol, skill]);   // eslint-disable-line react-hooks/exhaustive-deps

  const busy = status === "live";
  const submit = async () => {
    const value = text.trim();
    if (!value || busy) return;
    setText("");
    await send(value);
  };

  return (
    <div className="flex h-full flex-col theme-transition">
      <div className="glass border-b border-border-subtle px-6 py-2.5 flex flex-wrap items-center gap-3 sticky top-0 z-20">
        <span className="text-sm font-semibold tracking-tight shrink-0">
          Protocol <span className="grad-text">playground</span>
        </span>
        <div className="flex items-center rounded-lg border border-border-subtle bg-bg-muted/50 p-0.5" role="tablist" aria-label="Wire protocol">
          {PROTOCOLS.map((p) => (
            <button key={p.value} role="tab" aria-selected={protocol === p.value}
                    onClick={() => setProtocol(p.value)}
                    className={cn(
                      "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                      protocol === p.value ? "bg-bg-surface text-text-primary shadow-sm" : "text-text-secondary hover:text-text-primary",
                    )}>
              {p.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-xs text-text-secondary">
          Skill
          <select value={skill} onChange={(e) => setSkill(e.target.value)}
                  className="h-7 rounded-md border border-border bg-bg-muted/70 px-2 text-xs focus:outline-none focus:ring-2 focus:ring-brand/30">
            <option value="">Let the agent pick from what is typed</option>
            {skills.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
          </select>
        </label>
        <span className="inline-flex min-w-0 flex-1 items-center gap-1.5 truncate text-xs text-text-muted">
          <Info className="h-3 w-3 shrink-0" />
          {PROTOCOLS.find((p) => p.value === protocol)?.tagline}
        </span>
        <button onClick={restart} title="Start a new conversation"
                className="inline-flex items-center gap-1 rounded-md border border-border-subtle px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:border-border">
          <RotateCcw className="h-3 w-3" /> New
        </button>
      </div>

      {messages.length === 0 ? (
        <div className="flex-1 grid place-items-center px-6">
          <div className="max-w-lg text-center">
            <p className="text-sm font-medium text-text-secondary">
              Talk to any of your skills, and read what the server sends.
            </p>
            <p className="mt-2 text-xs leading-relaxed text-text-muted">
              The same flow, the same widgets and the same guarantees as the Flow page. The frames below are
              exactly what your own frontend receives over {PROTOCOLS.find((p) => p.value === protocol)?.label}:
              copy one to see how a question, a widget or a confirmation is encoded.
            </p>
          </div>
        </div>
      ) : (
        <FlowConfigProvider value={{ validateEndpoint: "/api/flow/validate-field", skill: skill || undefined }}>
          <MessageList messages={messages} onWidgetSubmit={submitWidget} protocol={protocol} />
        </FlowConfigProvider>
      )}
      {error && (
        <div className="mx-6 mb-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
          {error}
        </div>
      )}

      <ProtocolViewer protocol={protocol} frames={frames} onClear={() => setFrames([])} />

      <form onSubmit={(e) => { e.preventDefault(); void submit(); }}
            className="border-t border-border-subtle bg-bg-surface px-6 py-3">
        <div className="mx-auto flex max-w-3xl items-center gap-2">
          <input value={text} onChange={(e) => setText(e.target.value)} disabled={busy}
                 placeholder={skill ? `Say something to ${skill}…` : "Type what a user would, e.g. “I need to send some money”"}
                 aria-label="Message"
                 className="h-10 flex-1 rounded-xl border border-border bg-bg-muted/60 px-3.5 text-sm placeholder:text-text-muted focus:outline-none focus:border-brand/60 focus:ring-2 focus:ring-brand/25 disabled:opacity-60" />
          <button type="submit" disabled={busy || !text.trim()}
                  className="inline-flex h-10 items-center gap-1.5 rounded-xl bg-brand px-4 text-sm font-medium text-white disabled:opacity-50">
            <Send className="h-4 w-4" /> Send
          </button>
        </div>
      </form>
    </div>
  );
}

/**
 * FlowChat — free-form chat UI that consumes the aigenos flow engine.
 *
 * Differences vs the YAML workflow Chat:
 *  - User types messages instead of picking a workflow from a dropdown.
 *  - Engine is the deterministic aigenos flow runtime; widgets are auto-
 *    generated from FieldSpec metadata on every interrupt.
 *  - Talks to /flow (via /api proxy in dev) — same AG-UI protocol
 *    on the wire, so MessageList + SpecRenderer work unchanged.
 */
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Sparkles, Send, RotateCw, Radio, Wand2, ArrowRight, Pin, X } from "lucide-react";
import { MessageList } from "./MessageList";
import { MicButton } from "./MicButton";
import { VoicePanel, VoiceToggle } from "./VoiceConversation";
import { useVoiceConversation } from "../hooks/useVoiceConversation";
import { SkillCreator } from "./SkillCreator";
import { SkillEditor } from "./SkillEditor";
import { useFlowChat } from "../hooks/useFlowChat";
import { FlowConfigProvider } from "../renderer/FlowConfig";
import { cn } from "../lib/cn";

type SkillInfo = {
  name: string;
  description: string;
  kind?: "workflow" | "digression";
  metadata?: Record<string, unknown>;
  fields: Record<string, { type: string; description: string; required: boolean; values: string[] | null }>;
};

const STARTER_PROMPTS = [
  "I want to book a meeting room",
  "Book the redwood room tomorrow at 3pm for an hour",
  "What rooms do you have available?",
];

type LlmInfo = { provider: string; model: string; hosted: boolean };

export function FlowChat() {
  const [input, setInput] = useState("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [editingSkill, setEditingSkill] = useState<string | null>(null);
  const [llmInfo, setLlmInfo] = useState<LlmInfo | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  // ?skill=<name> — the Canvas's "Test in Flow" lands here and the run enters
  // that flow directly instead of waiting for the classifier to route to it.
  const pinned = searchParams.get("skill") ?? undefined;

  const { messages, status, error, send, submitWidget, reset } = useFlowChat({
    endpoint: "/api/flow",
    protocol: "ag-ui",
    skill: pinned,
  });

  const voice = useVoiceConversation({ messages, status, send });

  const unpin = useCallback(() => {
    searchParams.delete("skill");
    setSearchParams(searchParams, { replace: true });
    reset();
  }, [searchParams, setSearchParams, reset]);

  const refreshSkills = useCallback(() => {
    fetch("/api/flow/skills")
      .then((r) => (r.ok ? r.json() : { skills: [] }))
      .then((j) => setSkills(j.skills ?? []))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshSkills();
    fetch("/api/flow/llm-info")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setLlmInfo(j))
      .catch(() => undefined);
  }, [refreshSkills]);

  // "Run it" on a new skill: start a fresh run pinned to it, then say its
  // trigger phrase. Sending straight away would put the phrase into whatever
  // run is open (or into another pinned skill), so the send waits until the
  // pin has reached the hook. The list refreshes separately, via onRefresh.
  const [queued, setQueued] = useState<{ skill: string; text: string } | null>(null);
  const onSkillCreated = useCallback(
    (name: string, trigger: string) => {
      setSearchParams({ skill: name }, { replace: true });
      reset();
      setQueued({ skill: name, text: trigger });
    },
    [setSearchParams, reset],
  );
  useEffect(() => {
    if (!queued || pinned !== queued.skill || messages.length > 0 || status === "live") return;
    setQueued(null);
    void send(queued.text);
  }, [queued, pinned, messages.length, status, send]);

  // The starter prompts are about booking a room, so they only make sense
  // when that skill is loaded and the run is not pinned to another one.
  const showStarters = !pinned && skills.some((s) => s.name === "book-meeting-room");

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const text = input.trim();
      if (!text || status === "live") return;
      setInput("");
      void send(text);
    },
    [input, status, send],
  );

  const onWidgetSubmit = useCallback(
    (toolCallId: string, payload: unknown) => {
      void submitWidget(toolCallId, payload);
    },
    [submitWidget],
  );

  return (
    <div className="flex h-full flex-col theme-transition">
      <header className="relative nav-edge px-4 sm:px-6 py-2.5 flex items-center justify-between text-xs gap-4 bg-bg-base/40">
        <span className="inline-flex items-center gap-1.5 text-text-muted truncate">
          <Sparkles className="h-3 w-3 shrink-0" />
          <span className="font-medium text-text-secondary">Flow engine</span>
          <span className="hidden lg:inline">· deterministic skills with auto-generated widgets</span>
          {llmInfo && (
            <span
              className={cn(
                "ml-2 inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-medium font-mono",
                llmInfo.hosted
                  ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300",
              )}
              title={`Powered by ${llmInfo.provider} / ${llmInfo.model}${llmInfo.hosted ? "" : " (local)"}`}
            >
              {llmInfo.hosted ? "⚡" : "○"} {llmInfo.provider}:{llmInfo.model}
            </span>
          )}
        </span>
        <div className="flex items-center gap-2.5 shrink-0">
          {pinned && (
            <span
              className="inline-flex items-center gap-1 rounded-md border border-brand/30 bg-brand-subtle px-2 py-1 text-[12px] font-medium text-brand"
              title={`This run enters ${pinned} directly — the classifier is bypassed`}
            >
              <Pin className="h-3 w-3" />
              <span className="font-mono">{pinned}</span>
              <button
                onClick={unpin}
                aria-label="Unpin skill"
                className="ml-0.5 rounded p-0.5 transition-colors hover:bg-brand/20"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </span>
          )}
          <button
            onClick={() => setCreatorOpen(true)}
            className="control-press inline-flex items-center gap-1.5 rounded-lg border border-brand/30 bg-brand/10 px-2.5 py-1.5 text-[12px] font-semibold text-brand hover:bg-brand/20 focus-ring"
            title="Generate a new skill from a description"
          >
            <Wand2 className="h-3 w-3" />
            <span className="hidden sm:inline">Create skill</span>
          </button>
          <StatusPill status={status} />
        </div>
      </header>

      {messages.length === 0 ? (
        <Empty
          skills={skills}
          showStarters={showStarters}
          onPick={(p) => void send(p)}
          onCreateSkill={() => setCreatorOpen(true)}
          onEditSkill={(name) => setEditingSkill(name)}
        />
      ) : (
        // This app DOES serve the flow router at /api/flow, so inline
        // per-field validation is switched on explicitly.
        <FlowConfigProvider value={{ validateEndpoint: "/api/flow/validate-field", skill: pinned }}>
          <MessageList messages={messages} onWidgetSubmit={onWidgetSubmit} protocol="ag-ui" />
        </FlowConfigProvider>
      )}

      <SkillCreator
        open={creatorOpen}
        onClose={() => setCreatorOpen(false)}
        onRefresh={refreshSkills}
        onCreated={onSkillCreated}
      />

      <SkillEditor
        open={editingSkill !== null}
        skillName={editingSkill}
        onClose={() => setEditingSkill(null)}
        onSaved={() => refreshSkills()}
        onDeleted={() => {
          setEditingSkill(null);
          refreshSkills();
        }}
      />


      <form
        onSubmit={onSubmit}
        className="glass theme-transition"
      >
        <div className="mx-auto max-w-3xl px-4 sm:px-6 py-3.5 flex flex-col gap-1.5">
          {error && (
            <div
              role="alert"
              className="rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-xs whitespace-pre-line break-words text-rose-700 dark:text-rose-300"
            >
              {error}
            </div>
          )}
          <VoicePanel voice={voice} />
          <div className="chat-composer flex items-stretch gap-2 p-1.5">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              aria-label="Message the flow engine"
              // A form on screen is one way to answer, not the only one: the
              // user can type a correction ("make it 4 nights") at any point
              // and the flow replays with it.
              placeholder={
                status === "waiting_for_widget"
                  ? "Answer above, or type to change something…"
                  : showStarters
                    ? "Type a message (e.g. ‘book a meeting room’)"
                    : "Type a message…"
              }
              className="min-w-0 flex-1 rounded-xl bg-transparent px-3 py-2 text-sm text-text-primary outline-none placeholder:text-text-muted"
            />

            {!voice.on && <MicButton value={input} onChange={setInput} />}
            <VoiceToggle voice={voice} />

            <button
              type="submit"
              disabled={!input.trim() || status === "live"}
              className="control-press inline-flex items-center gap-2 rounded-xl bg-brand text-brand-fg px-4 py-2 text-sm font-semibold shadow-glow hover:bg-brand-hover disabled:opacity-50 disabled:shadow-none focus-ring"
            >
              <Send className="h-4 w-4" /> Send
            </button>

            {messages.length > 0 && (
              <button
                type="button"
                onClick={reset}
                aria-label="Start a new conversation"
                className="control-press icon-control inline-flex items-center justify-center rounded-xl bg-bg-elevated border border-border px-3 py-2 text-text-secondary hover:text-text-primary hover:border-border-strong focus-ring"
                title="Clear conversation"
              >
                <RotateCw className="h-4 w-4" />
              </button>
            )}
          </div>
          {input.length > 100 ? <div className="char-count visible" aria-live="polite">{input.length} characters</div> : null}
        </div>
      </form>
    </div>
  );
}

function Empty({
  skills,
  showStarters,
  onPick,
  onCreateSkill,
  onEditSkill,
}: {
  skills: SkillInfo[];
  showStarters: boolean;
  onPick: (text: string) => void;
  onCreateSkill: () => void;
  onEditSkill: (name: string) => void;
}) {
  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-3xl px-5 sm:px-6 py-10 sm:py-14 space-y-8 animate-fade-in">
        <div className="text-center">
          <div className="inline-flex items-center justify-center h-14 w-14 rounded-2xl border border-brand/20 bg-brand-subtle shadow-card mb-4">
            <Sparkles className="h-6 w-6 text-brand" />
          </div>
          <h1 className="text-2xl sm:text-[1.75rem] font-semibold tracking-[-0.025em] leading-tight text-text-primary">Talk to a deterministic flow</h1>
          <p className="mt-2 text-sm text-text-secondary leading-relaxed max-w-lg mx-auto">
            Free-form chat. The aigenos flow engine activates a skill, collects fields with auto-generated widgets, then runs deterministic Python actions.
          </p>
          <p className="mt-3 text-[12px] text-text-muted inline-flex items-center gap-1">
            <Radio className="h-3 w-3" />
            100% deterministic runtime · LLM constrained to interpretation
          </p>
        </div>

        {/* Hero CTA: describe a workflow → the creator writes and loads a skill. */}
        <button
          onClick={onCreateSkill}
          className="surface-card control-press group w-full rounded-2xl hover:border-brand/50 px-5 sm:px-6 py-5 text-left focus-ring"
        >
          <div className="flex items-start gap-3">
            <div className="shrink-0 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-brand text-brand-fg shadow-glow">
              <Wand2 className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-sm font-semibold text-text-primary">
                Add a skill from one sentence
                <ArrowRight className="motion-arrow h-3.5 w-3.5 text-brand transition-transform duration-150" aria-hidden />
              </div>
              <p className="mt-0.5 text-xs text-text-secondary">
                Describe it, for example "collect a refund request". A SKILL.md and a handlers.py are generated from your description, the engine loads them without a restart, and you can chat with the new skill here.
              </p>
            </div>
          </div>
        </button>

        {skills.length > 0 && (
          <div className="space-y-2.5">
            <div className="flex items-baseline justify-between">
              <h3 className="text-xs uppercase tracking-wider text-text-muted">Loaded skills</h3>
              <span className="text-[11px] text-text-muted">Click a skill to edit</span>
            </div>
            <div className="space-y-2">
              {skills.map((s) => (
                <button
                  key={s.name}
                  onClick={() => onEditSkill(s.name)}
                  className="surface-card control-press w-full text-left rounded-xl hover:border-brand/40 px-4 py-3 focus-ring group"
                >
                  <div className="flex flex-col gap-0.5 sm:flex-row sm:items-baseline sm:gap-2">
                    <span className="shrink-0 whitespace-nowrap font-mono text-sm text-text-primary group-hover:text-brand">{s.name}</span>
                    <span className="text-xs text-text-secondary">{s.description}</span>
                  </div>
                  {Object.keys(s.fields).length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1.5">
                      {Object.entries(s.fields).map(([fname, fspec]) => (
                        <span
                          key={fname}
                          className="inline-flex items-center gap-1 rounded-md bg-bg-muted border border-border-subtle px-1.5 py-0.5 text-[12px] text-text-muted"
                          title={fspec.description}
                        >
                          <span className="text-text-primary">{fname}</span>
                          <span className="text-text-muted">:{fspec.type}</span>
                          {fspec.values && (
                            <span className="text-brand">[{fspec.values.length}]</span>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {showStarters && (
          <div className="space-y-2.5">
            <h2 className="text-sm font-semibold text-text-secondary">Try saying</h2>
            <div className="space-y-2">
              {STARTER_PROMPTS.map((p) => (
                <button
                  key={p}
                  onClick={() => onPick(p)}
                  className="surface-card control-press group w-full flex items-center justify-between gap-3 text-left rounded-xl hover:border-border px-4 py-3 text-sm text-text-primary focus-ring"
                >
                  <span>{p}</span>
                  <ArrowRight className="motion-arrow h-3.5 w-3.5 shrink-0 text-text-muted transition-[transform,color] duration-150 group-hover:text-brand" aria-hidden />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status }: { status: "idle" | "live" | "waiting_for_widget" | "error" }) {
  const config = {
    idle: { label: "Ready", dot: "bg-text-muted", tone: "text-text-muted border-border-subtle bg-bg-muted/60", title: "Ready for a message" },
    live: { label: "Thinking", dot: "bg-emerald-400 animate-pulse", tone: "text-emerald-700 dark:text-emerald-300 border-emerald-500/25 bg-emerald-500/10", title: "The model is thinking. A local model can take up to 30 seconds on its first call." },
    waiting_for_widget: { label: "Needs input", dot: "bg-amber-400", tone: "text-amber-700 dark:text-amber-300 border-amber-500/25 bg-amber-500/10", title: "Complete the widget to continue" },
    error: { label: "Error", dot: "bg-red-400", tone: "text-red-700 dark:text-red-300 border-red-500/25 bg-red-500/10", title: "The flow hit an error. Check the backend console for details." },
  } as const;
  const current = config[status];

  return (
    <span
      aria-live="polite"
      title={current.title}
      className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] font-semibold", current.tone)}
    >
      <span className={cn("h-1.5 w-1.5 rounded-full", current.dot)} aria-hidden />
      {current.label}
    </span>
  );
}

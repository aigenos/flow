import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useNavigate, useLocation, Link } from "react-router-dom";
import { ArrowLeft, Play, Send, RotateCw, Sparkles } from "lucide-react";
import { MessageList } from "../chat/MessageList";
import { MicButton } from "../chat/MicButton";
import { VoicePanel, VoiceToggle } from "../chat/VoiceConversation";
import { useVoiceConversation } from "../hooks/useVoiceConversation";
import { useFlowChat } from "../hooks/useFlowChat";
import { listSkills } from "../lib/api";
import { FlowConfigProvider } from "../renderer/FlowConfig";

type FieldSpec = {
  type: string; description: string; required: boolean; values: string[] | null;
  /** Whose yes this is, when it is not the user's. */
  asked_of?: string | null;
};
type SkillInfo = {
  name: string; description: string; fields: Record<string, FieldSpec>;
  /** Written as `steps:`: each field's description is the question itself. */
  short_form?: boolean;
};

const humanize = (s: string) =>
  s.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
// A description opens with what the skill does; the rest is routing notes for the classifier.
const firstSentence = (s: string) => {
  const text = (s ?? "").trim();
  return text.match(/^.*?[.!?](?=\s|$)/s)?.[0] ?? text;
};
const isConfirm = (name: string, type: string) => /confirm/i.test(name) && /^bool/i.test(type);

export function WorkspaceAgentPage() {
  const { name = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [skill, setSkill] = useState<SkillInfo | null>(null);
  const [load, setLoad] = useState<"loading" | "ok" | "error">("loading");
  const [loadError, setLoadError] = useState("");
  const [input, setInput] = useState("");
  const startedRef = useRef(false);

  // Pinned to this skill: the run enters it directly instead of asking the
  // classifier to pick one from the opening message.
  const { messages, status, error, send, submitWidget, reset } = useFlowChat({
    endpoint: "/api/flow",
    protocol: "ag-ui",
    skill: name,
  });
  const voice = useVoiceConversation({ messages, status, send });

  useEffect(() => {
    listSkills<SkillInfo>()
      .then((list) => {
        setSkill(list.find((s) => s.name === name) ?? null);
        setLoad("ok");
      })
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : String(e));
        setLoad("error");
      });
  }, [name]);

  // Auto-start with the trigger phrase passed from creation (once).
  useEffect(() => {
    const triggers = (location.state as { triggers?: string[] } | null)?.triggers;
    if (!startedRef.current && triggers && triggers[0]) {
      startedRef.current = true;
      void send(triggers[0]);
    }
  }, [location.state, send]);

  const steps = useMemo(() => {
    if (!skill) return [];
    const KIND: Record<string, string> = {
      string: "text", number: "a number", integer: "a whole number", boolean: "yes or no",
    };
    return Object.entries(skill.fields || {}).map(([fname, spec]) => ({
      name: fname,
      label: humanize(fname),
      // A skill written as steps words each question itself: show that. In
      // the long form a field's description is guidance for the model.
      question: skill.short_form ? (spec.description || "").trim() : "",
      // Every question is listed; one with a condition is asked only when the flow needs it.
      detail: [
        spec.values?.length ? `pick one of ${spec.values.length}` : (KIND[spec.type] ?? spec.type),
        spec.required === false ? "may be left blank" : "",
      ].filter(Boolean).join(" · "),
      confirm: isConfirm(fname, spec.type),
      askedOf: spec.asked_of || "",
    }));
  }, [skill]);

  const start = () => {
    void send(`Start ${humanize(name).toLowerCase()}`);
  };

  const onSend = (e: React.FormEvent) => {
    e.preventDefault();
    const t = input.trim();
    if (!t || status === "live") return;
    setInput("");
    void send(t);
  };

  return (
    <div className="h-full overflow-y-auto scrollbar-thin theme-transition">
      <div className="mx-auto max-w-6xl px-5 sm:px-6 py-6 animate-fade-in">
        {/* header */}
        <button
          onClick={() => navigate("/workspace")}
          className="inline-flex items-center gap-1.5 text-sm text-text-secondary hover:text-text-primary transition-colors mb-4 focus-ring rounded-lg"
        >
          <ArrowLeft className="h-4 w-4" /> All skills
        </button>
        <div className="mb-5">
          <h1 className="text-2xl font-semibold tracking-tight">{humanize(name)}</h1>
          {(skill || load === "loading") && (
            <p className="text-sm text-text-secondary mt-1 max-w-2xl">
              {skill ? firstSentence(skill.description) : "Loading…"}
            </p>
          )}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] gap-5">
          {/* what the skill collects */}
          <section className="glass-card p-5 h-fit">
            <h2 className="text-[12px] uppercase tracking-[1.6px] text-text-muted font-medium mb-3">What it asks for</h2>
            <ol className="space-y-2.5">
              {steps.map((s, i) => (
                <li
                  key={s.name}
                  className="flex items-start gap-3 rounded-xl border border-border-subtle bg-bg-muted/40 px-3.5 py-3"
                >
                  <span className="shrink-0 h-6 w-6 rounded-lg grid place-items-center text-xs font-semibold bg-bg-elevated text-text-secondary">
                    {i + 1}
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-text-primary">
                      {s.question || (s.confirm ? "Ask you to confirm" : `Collect ${s.label.toLowerCase()}`)}
                    </div>
                    <div className="text-xs text-text-muted mt-0.5">
                      {s.askedOf
                        ? `a yes from ${s.askedOf}, not from you; the flow waits for it`
                        : s.confirm ? "a yes or a no; nothing changes before it" : s.detail}
                    </div>
                  </div>
                </li>
              ))}
              {steps.length === 0 && (
                <li className="text-sm text-text-muted">
                  {load === "loading" && "Loading…"}
                  {load === "error" && <span className="whitespace-pre-line">{loadError}</span>}
                  {load === "ok" && skill && "Nothing to fill in — press Start."}
                  {load === "ok" && !skill && (
                    <>
                      Skill not found.{" "}
                      <Link to="/workspace" className="font-medium text-brand hover:text-brand-hover focus-ring rounded">
                        Back to all skills
                      </Link>
                    </>
                  )}
                </li>
              )}
            </ol>
          </section>

          {/* live run */}
          <section className="glass-card flex flex-col h-[60vh] min-h-[420px] overflow-hidden">
            <div className="flex items-center justify-between px-4 py-2.5 border-b border-border-subtle text-xs">
              <span className="inline-flex items-center gap-1.5 text-text-muted">
                <Sparkles className="h-3.5 w-3.5" /> Run it live
              </span>
              <span className="text-text-muted">
                {status === "live" && <span className="text-emerald-700 dark:text-emerald-300">● thinking…</span>}
                {status === "waiting_for_widget" && <span className="text-amber-700 dark:text-amber-300">● your turn</span>}
                {status === "idle" && <span>○ ready</span>}
                {status === "error" && <span className="text-red-700 dark:text-red-300">● error — check backend</span>}
              </span>
            </div>

            {messages.length === 0 ? (
              <div className="flex-1 grid place-items-center p-6 text-center">
                <div>
                  <p className="text-sm text-text-secondary max-w-xs mx-auto">
                    Press Start and the agent runs this skill, asking for what it needs from the list.
                  </p>
                  <button
                    onClick={start}
                    className="mt-4 inline-flex items-center gap-2 rounded-xl bg-brand text-brand-fg px-5 py-2.5 text-sm font-medium shadow-glow hover:bg-brand-hover focus-ring"
                  >
                    <Play className="h-4 w-4" /> Start
                  </button>
                </div>
              </div>
            ) : (
              // The Studio serves the flow router at /api/flow, so inline
              // per-field validation is switched on explicitly here (the
              // published renderer never assumes an endpoint).
              <FlowConfigProvider value={{ validateEndpoint: "/api/flow/validate-field", skill: name }}>
                <MessageList
                  messages={messages}
                  onWidgetSubmit={(id, payload) => void submitWidget(id, payload)}
                  protocol="ag-ui"
                />
              </FlowConfigProvider>
            )}

            {error && (
              <div
                role="alert"
                className="mx-3 mb-2.5 rounded-lg border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-xs whitespace-pre-line break-words text-rose-700 dark:text-rose-300"
              >
                {error}
              </div>
            )}
            {voice.on && (
              <div className="border-t border-border-subtle px-3 pt-2.5">
                <VoicePanel voice={voice} />
              </div>
            )}
            <form onSubmit={onSend} className="border-t border-border-subtle px-3 py-2.5 flex items-center gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={status === "waiting_for_widget" ? "Answer above, or type to change something…" : "Type a reply…"}
                className="flex-1 rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-ring focus:border-brand disabled:opacity-60"
              />
              {!voice.on && (
                <MicButton value={input} onChange={setInput} className="rounded-lg" />
              )}
              <VoiceToggle voice={voice} className="rounded-lg" />
              <button
                type="submit"
                disabled={!input.trim() || status === "live"}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-3.5 py-2 text-sm font-medium shadow-glow hover:bg-brand-hover focus-ring disabled:opacity-50"
              >
                <Send className="h-4 w-4" />
              </button>
              {messages.length > 0 && (
                <button
                  type="button"
                  onClick={reset}
                  title="Restart"
                  className="inline-flex items-center justify-center h-9 w-9 rounded-lg border border-border bg-bg-muted text-text-secondary hover:text-text-primary focus-ring"
                >
                  <RotateCw className="h-4 w-4" />
                </button>
              )}
            </form>
          </section>
        </div>
      </div>
    </div>
  );
}

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Sparkles, ArrowRight, Play, Receipt, Rocket, Banknote,
  MessageSquare, GitBranch, Bot,
} from "lucide-react";
import { SkillCreator } from "../chat/SkillCreator";
import { useMode } from "../hooks/useMode";
import { listSkills } from "../lib/api";

type Skill = { name: string; description: string };

const TEMPLATES = [
  {
    icon: Banknote, title: "Refund request", blurb: "Order number, amount and reason, then a confirmation.",
    desc: "Collect a customer refund request — ask for the order number, the amount, and the reason; confirm before submitting.",
  },
  {
    icon: Receipt, title: "Expense report", blurb: "Amount, category and a one-line justification, then a confirmation.",
    desc: "File a business expense — collect the amount, category, and a one-line justification; confirm before submitting.",
  },
  {
    icon: Rocket, title: "New-hire onboarding", blurb: "Name, role, start date and equipment needs, then a confirmation.",
    desc: "Onboard a new hire — collect the new hire's full name, role, start date, and equipment needs; confirm before submitting.",
  },
];

const humanize = (s: string) =>
  s.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
// A description opens with what the skill does; the rest is routing notes for the classifier.
const firstSentence = (s: string) => {
  const text = (s ?? "").trim();
  return text.match(/^.*?[.!?](?=\s|$)/s)?.[0] ?? text;
};

export function WorkspacePage() {
  const navigate = useNavigate();
  const { setMode } = useMode();
  const [prompt, setPrompt] = useState("");
  const [skills, setSkills] = useState<Skill[]>([]);
  // "ok" only after a successful fetch, so an unreachable backend never reads as "no skills".
  const [load, setLoad] = useState<"loading" | "ok" | "error">("loading");
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [seed, setSeed] = useState("");

  const [loadError, setLoadError] = useState("");

  const refresh = useCallback(() => {
    listSkills<Skill>()
      .then((list) => {
        setSkills(list);
        setLoad("ok");
      })
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : String(e));
        setLoad("error");
      });
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const openCreator = (description: string) => {
    setSeed(description);
    setCreatorOpen(true);
  };

  const submitPrompt = (e: React.FormEvent) => {
    e.preventDefault();
    openCreator(prompt.trim());
  };

  // "Run it" on a new skill. The creator closes itself and has already
  // refreshed the list through onRefresh.
  const onCreated = (name: string, trigger: string) => {
    navigate(`/workspace/run/${encodeURIComponent(name)}`, { state: { triggers: [trigger] } });
  };

  const openCanvas = (name: string) => {
    // The Canvas is a Studio surface; switch role so its tabs are shown.
    setMode("studio");
    navigate(`/canvas/${encodeURIComponent(name)}`);
  };

  const hasSkills = load === "ok" && skills.length > 0;

  const skillsSection = (
    <section aria-labelledby="skills-heading">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 id="skills-heading" className="text-base font-semibold tracking-[-0.012em] text-text-primary">Your agent's skills</h2>
          {hasSkills && (
            <p className="mt-0.5 text-xs text-text-muted">
              One agent, {skills.length} {skills.length === 1 ? "skill" : "skills"}. Run a skill as a guided task, or open the chat and say what you need — the agent picks the skill.
            </p>
          )}
        </div>
        {hasSkills && (
          <button
            onClick={() => navigate("/flow")}
            className="control-press inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border-subtle bg-bg-muted/60 px-3 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary hover:border-border focus-ring"
          >
            <MessageSquare className="h-3.5 w-3.5" /> Chat with your agent
          </button>
        )}
      </div>
      {load === "loading" && (
        <div className="surface-card rounded-2xl px-5 py-6 text-center text-sm text-text-muted">Loading skills…</div>
      )}
      {load === "error" && (
        <div role="alert" className="surface-card rounded-2xl px-5 py-6 text-center text-sm text-text-secondary">
          <p className="whitespace-pre-line">{loadError}</p>
          <button onClick={() => { setLoad("loading"); refresh(); }} className="control-press mt-3 rounded-lg px-2 py-1 text-sm font-medium text-brand hover:text-brand-hover focus-ring">Try again</button>
        </div>
      )}
      {load === "ok" && skills.length === 0 && (
        <div className="surface-card rounded-2xl px-5 py-8 text-center text-sm text-text-secondary">
          <span className="mx-auto mb-3 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-bg-muted border border-border-subtle text-text-muted">
            <Bot className="h-5 w-5" aria-hidden />
          </span>
          <p>No skills yet. Describe one above, or start from a quick start.</p>
          <button onClick={() => openCreator("")} className="control-press mt-3 rounded-lg px-2 py-1 text-sm font-medium text-brand hover:text-brand-hover focus-ring">Create your first skill</button>
        </div>
      )}
      {hasSkills && (
        <div className="space-y-2.5">
          {skills.map((s) => (
            <div
              key={s.name}
              className="glass-card group flex items-center gap-4 px-5 py-3.5 transition-all hover:border-brand/35 hover:-translate-y-px"
            >
              <span className="h-2 w-2 rounded-full bg-brand shadow-glow shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-col sm:flex-row sm:items-baseline sm:gap-2">
                  <span className="min-w-0 truncate text-sm font-medium text-text-primary">{humanize(s.name)}</span>
                  <span className="min-w-0 truncate font-mono text-[11px] text-text-muted">{s.name}</span>
                </div>
                <div className="text-xs text-text-muted truncate">{firstSentence(s.description)}</div>
              </div>

              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  onClick={() => navigate(`/workspace/run/${encodeURIComponent(s.name)}`)}
                  title="Guided run — step-by-step task UI with widgets"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-brand/15 border border-brand/35 text-brand px-3 py-1.5 text-xs font-medium hover:bg-brand/25 transition-colors focus-ring"
                >
                  <Play className="h-3.5 w-3.5" /> Run
                </button>
                <button
                  onClick={() => openCanvas(s.name)}
                  title="Open this skill's steps on the Canvas"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border-subtle bg-bg-muted/60 px-3 py-1.5 text-xs font-medium text-text-secondary hover:text-text-primary hover:border-border transition-colors focus-ring"
                >
                  <GitBranch className="h-3.5 w-3.5" /> Edit
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );

  return (
    <div className="h-full overflow-y-auto scrollbar-thin theme-transition">
      <div className="mx-auto max-w-5xl px-5 sm:px-6 py-8 sm:py-10 space-y-10 animate-fade-in">
        {/* hero */}
        <section className="hero-material overflow-hidden px-5 sm:px-10 py-9 sm:py-11 text-center">
          <div aria-hidden className="absolute inset-x-20 -top-24 h-44 rounded-full bg-brand/10 blur-3xl pointer-events-none" />
          <div className="relative">
          <div className="mb-3 inline-flex items-center gap-1.5 text-[12px] font-semibold uppercase tracking-[0.14em] text-brand">
            <Sparkles className="h-3 w-3" /> Teach your agent a new skill
          </div>
          <h1 className="text-3xl sm:text-[2.65rem] font-semibold tracking-[-0.035em] leading-[1.08]">
            What should your <span className="grad-text">agent</span> do?
          </h1>
          <p className="mt-3 text-text-secondary text-sm sm:text-base leading-relaxed max-w-xl mx-auto">
            Describe it in a sentence. You get a step-by-step skill you can run right away and edit on the Canvas.
          </p>

          <form
            onSubmit={submitPrompt}
            className="agent-composer mt-7 mx-auto max-w-2xl grid grid-cols-[auto_1fr] sm:flex items-center gap-2.5 rounded-2xl px-3 py-3"
          >
            <Sparkles className="h-4 w-4 text-brand shrink-0 ml-1" aria-hidden />
            <label htmlFor="agent-description" className="visually-hidden">Describe what the new skill should do</label>
            <input
              id="agent-description"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Collect a refund request: order number, reason, then confirm"
              className="min-w-0 flex-1 bg-transparent outline-none text-sm text-text-primary placeholder:text-text-muted"
            />
            <button
              type="submit"
              className="control-press col-span-2 sm:col-span-1 inline-flex items-center justify-center gap-1.5 rounded-xl bg-brand text-brand-fg px-4 py-2 text-sm font-semibold shadow-glow hover:bg-brand-hover focus-ring"
            >
              Build it <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </form>
          </div>
        </section>

        {/* templates */}
        <section aria-labelledby="templates-heading">
          <div className="mb-4">
            <h2 id="templates-heading" className="text-base font-semibold tracking-[-0.012em] text-text-primary">Quick starts</h2>
            <p className="mt-0.5 text-xs text-text-muted">Each one opens the creator with an example description you can change first.</p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
            {TEMPLATES.map(({ icon: Icon, title, blurb, desc }) => (
              <button
                type="button"
                key={title}
                onClick={() => openCreator(desc)}
                className="surface-card hover-lift control-press relative flex flex-col items-start rounded-2xl text-left p-4 hover:border-brand/50 focus-ring group"
              >
                <ArrowRight className="motion-arrow absolute right-4 top-4 h-3.5 w-3.5 text-text-muted opacity-0 transition-opacity duration-150 group-hover:opacity-100" aria-hidden />
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-brand/15 bg-brand-subtle mb-3">
                  <Icon className="h-5 w-5 text-brand" />
                </span>
                <div className="text-sm font-semibold tracking-[-0.01em] group-hover:text-brand transition-colors">{title}</div>
                <p className="text-xs text-text-muted mt-1 leading-relaxed">{blurb}</p>
              </button>
            ))}
          </div>
        </section>

        {skillsSection}
      </div>

      <SkillCreator
        open={creatorOpen}
        seedDescription={seed}
        onClose={() => setCreatorOpen(false)}
        onRefresh={refresh}
        onCreated={onCreated}
      />
    </div>
  );
}

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Sparkles, MessageSquare, Boxes, GitBranch, LayoutGrid,
  ArrowDown, Database, Globe, Plug, ShieldCheck, Bot, User,
  Wrench, Play, History, Inbox, LibraryBig,
} from "lucide-react";
import { api, type ActivityEvent } from "../lib/api";
import { timeAgo } from "../components/HistoryPanel";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "../components/ui/card";

/**
 * The product map: what each surface is FOR, who uses it, and how everything
 * lands on the same backend. Linked from the nav so new users orient in one
 * screen instead of clicking through every tab.
 */

const SURFACES = [
  {
    to: "/workspace",
    icon: LayoutGrid,
    name: "Workspace",
    who: "Business users",
    when: "Run a skill as a guided task, chat with the agent, or open a skill's Canvas.",
    detail: "The simple view. Describe a job in a sentence and it becomes a skill.",
    tone: "border-sky-500/40",
  },
  {
    to: "/canvas",
    icon: GitBranch,
    name: "Canvas",
    who: "Flow authors",
    when: "Build the workflow: a column of steps, checked as you edit.",
    detail: "Ask · Say · Widget · Tool · Link · Confirm · Approval · Condition · Repeat · Chat. The loader reads the skill while you build and writes any problem on the step it is about. Procedure reads it back in plain words. Saving hot-reloads the agent.",
    tone: "border-emerald-500/40",
  },
  {
    to: "/gallery",
    icon: LibraryBig,
    name: "Gallery",
    who: "Flow authors",
    when: "Start from an example instead of a blank page.",
    detail: "Templates written as steps (a repeat with an approval, a designed list with a chat, a booking with rules), installed with one click through the same checked save as any edit.",
    tone: "border-teal-500/40",
  },
  {
    to: "/widgets",
    icon: Boxes,
    name: "Widgets",
    who: "Designers",
    when: "Design the UI a step renders — typography, colors, layout.",
    detail: "Click-to-select canvas + full style inspector. Canvas steps reference these by id (`widget: <id>`); auto-widgets cover the rest.",
    tone: "border-violet-500/40",
  },
  {
    to: "/tools",
    icon: Wrench,
    name: "Tools",
    who: "Developers",
    when: "See, write and try the functions a step can run.",
    detail: "Every tool your skills can name: what it needs, whether it changes anything, which skills use it. A tool that changes something cannot run without a Confirm step above it.",
    tone: "border-orange-500/40",
  },
  {
    to: "/connectors",
    icon: Database,
    name: "Connectors",
    who: "Data owners",
    when: "Wire managed databases your flows can call — typed, gated (needs a server token).",
    detail: "PostgreSQL / MongoDB operations become named, typed tools. A write runs only after a confirmation field is set.",
    tone: "border-amber-500/40",
  },
  {
    to: "/approvals",
    icon: Inbox,
    name: "Approvals",
    who: "Approvers",
    when: "Give the yes that is someone else's: a manager's, finance's.",
    detail: "A flow that needs it stops and files a request here, showing the tool a yes will run and its inputs. The chat cannot answer in your place, and neither can the model.",
    tone: "border-rose-500/40",
  },
  {
    to: "/flow",
    icon: Sparkles,
    name: "Flow",
    who: "Everyone",
    when: "Try what you built — the same deterministic runtime users get.",
    detail: "Chat + a2ui widgets + starter prompts. This is the product experience.",
    tone: "border-brand/50",
  },
  {
    to: "/chat",
    icon: MessageSquare,
    name: "Playground",
    who: "Developers",
    when: "Run any skill over AG-UI, A2UI or MCP-UI and read the raw frames.",
    detail: "Type like a user. Every frame the server sends is shown under the chat: exactly what your own frontend would receive.",
    tone: "border-slate-500/40",
  },
];

export function MapPage() {
  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="hero-material overflow-hidden px-8 py-10 hero-enter">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-end">
            <div className="flex-1">
              <Badge className="hero-enter">
                <Sparkles className="h-3 w-3" /> aigenos/flow Studio
              </Badge>
              <h1 className="mt-3 text-4xl font-bold tracking-tight hero-enter hero-enter-delay-1">
                Build <span className="grad-text">deterministic agents</span>,
                <br className="hidden sm:block" /> visually.
              </h1>
              <p className="mt-3 max-w-xl text-sm leading-relaxed text-text-secondary hero-enter hero-enter-delay-2">
                Design flows on a canvas, style the widgets they render, wire
                real tools — then run it all on a runtime where the LLM fills
                fields and <em>nothing else</em>.
              </p>
              <div className="mt-5 flex flex-wrap items-center gap-2.5 hero-enter hero-enter-delay-3">
                <Button size="lg" onClick={() => window.dispatchEvent(new Event("aigenos:tour"))}>
                  <Play /> Take the 60-second tour
                </Button>
                <Button variant="secondary" size="lg" asChild>
                  <Link to="/canvas">
                    <GitBranch /> Start building
                  </Link>
                </Button>
              </div>
            </div>
            <div className="flex shrink-0 flex-col gap-2 hero-enter hero-enter-delay-3">
              {[
                { icon: ShieldCheck, text: "Fail-closed verifier on every save" },
                { icon: History, text: "Every save versioned — restore anytime" },
                { icon: Boxes, text: "Widgets are steps, not afterthoughts" },
              ].map(({ icon: Icon, text }) => (
                <div
                  key={text}
                  className="flex items-center gap-2.5 rounded-xl border border-border-subtle bg-bg-muted/50 px-3.5 py-2.5 text-xs text-text-secondary"
                >
                  <Icon className="h-3.5 w-3.5 shrink-0 text-brand" /> {text}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* surface cards */}
        <div data-tour="map-cards" className="mt-8 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {SURFACES.map(({ to, icon: Icon, name, who, when, detail, tone }) => (
            <Link key={to} to={to} className="block group focus-ring rounded-2xl">
              <Card interactive className="h-full">
                <CardHeader className="gap-2">
                  <div className="flex items-center gap-2.5">
                    <span className={`inline-flex h-8 w-8 items-center justify-center rounded-lg border ${tone} bg-bg-muted/60 transition-transform duration-200 group-hover:scale-110`}>
                      <Icon className="h-4 w-4 text-text-primary" />
                    </span>
                    <CardTitle className="text-[15px]">{name}</CardTitle>
                    <Badge variant="secondary" className="ml-auto">{who}</Badge>
                  </div>
                  <p className="text-xs leading-relaxed text-text-primary/90">{when}</p>
                  <CardDescription>{detail}</CardDescription>
                </CardHeader>
              </Card>
            </Link>
          ))}
        </div>

        {/* workspace activity — the SQLite store's audit trail */}
        <ActivityFeed />

        {/* data flow */}
        <h2 className="mt-12 text-sm font-semibold uppercase tracking-[0.15em] text-text-muted">
          How a message flows
        </h2>
        <div className="mt-3 glass-card p-6">
          <div className="flex flex-col items-center gap-1 text-center">
            <Tier icon={User} title="User"
                  body="Types a message — or submits a grid / form / autosuggest (no LLM in that path)." />
            <ArrowDown className="h-4 w-4 text-text-muted" />
            <Tier icon={MessageSquare} title="Chat UI  ·  @aigenos/flow (React)"
                  body="Renders text + a2ui widgets (cards, grids, timelines) streamed from the backend." />
            <ArrowDown className="h-4 w-4 text-text-muted" />
            <Tier icon={Bot} title="Interpreter LLM  —  understands, never decides"
                  body="Turns each message into typed commands (set a field, confirm, correct, switch). Free-form questions route to the scoped assist/chat answerer." />
            <ArrowDown className="h-4 w-4 text-text-muted" />
            <Tier icon={ShieldCheck} title="Deterministic runtime  —  decides, never guesses"
                  body="Replays your flow: asks fields, gates effects (check / requires / 2FA-style rules), re-arms on corrections, renders the right widget per step." />
            <ArrowDown className="h-4 w-4 text-text-muted" />
            <div className="flex flex-wrap items-stretch justify-center gap-2">
              <Leaf icon={Globe} title="REST APIs" body="search, lookups, validation" />
              <Leaf icon={Database} title="Databases" body="via your endpoints / tools" />
              <Leaf icon={Plug} title="MCP servers" body="summaries, history, claims…" />
              <Leaf icon={Wrench} title="Python functions" body="anything callable" />
            </div>
          </div>
          <p className="mt-4 text-[12px] text-text-muted text-center">
            The model chooses <em>which</em> tool answers a question; the runtime owns <em>which account /
            amount / record</em> it may touch (pinned from validated state) and <em>whether</em> any
            side effect fires. Free chat mid-flow returns to the pending step; chat after the flow
            keeps the workflow's outputs as context.
          </p>
        </div>

        <p className="mt-8 text-center text-[12px] text-text-muted">
          <Link to="/welcome" className="hover:text-text-secondary transition-colors">
            ← Back to the product page
          </Link>
        </p>
      </div>
    </div>
  );
}

const ACTION_TONE: Record<string, string> = {
  saved: "bg-brand",
  created: "bg-emerald-400",
  restored: "bg-amber-400",
  deleted: "bg-rose-400",
};

/** Recent saves/restores/deletes across skills, widgets, and workflows —
 *  read from the SQLite workspace store, where every Save is versioned. */
function ActivityFeed() {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);

  useEffect(() => {
    api.activity(8).then(setEvents).catch(() => setEvents([]));
  }, []);

  if (!events || events.length === 0) return null;

  const linkFor = (e: ActivityEvent) =>
    e.kind === "skill" ? `/canvas/${e.ref}` : e.kind === "widget" ? `/widgets/${e.ref}` : "/builder";

  return (
    <>
      <h2 className="mt-12 text-sm font-semibold uppercase tracking-[0.15em] text-text-muted">
        Recent activity
      </h2>
      <p className="mt-1.5 text-xs text-text-secondary max-w-2xl">
        Every save is versioned in the local workspace database — open any
        artifact's <span className="font-medium text-text-primary">History</span> to
        restore a prior version.
      </p>
      <div className="mt-3 glass-card divide-y divide-border-subtle">
        {events.map((e, i) => (
          <Link
            key={i}
            to={linkFor(e)}
            className="flex items-center gap-3 px-4 py-2.5 text-xs transition-colors hover:bg-bg-muted/60 first:rounded-t-2xl last:rounded-b-2xl"
          >
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${ACTION_TONE[e.action] ?? "bg-border"}`} />
            <span className="font-medium text-text-primary truncate">{e.ref}</span>
            <Badge variant="secondary">{e.kind}</Badge>
            <span className="text-text-secondary">
              {e.action}
              {e.detail ? ` · ${e.detail}` : ""}
            </span>
            <span className="ml-auto shrink-0 text-[11px] text-text-muted">{timeAgo(e.created_at)}</span>
          </Link>
        ))}
      </div>
    </>
  );
}

function Tier({ icon: Icon, title, body }: { icon: any; title: string; body: string }) {
  return (
    <div className="w-full max-w-2xl rounded-xl border border-border-subtle bg-bg-muted/50 px-4 py-3 transition-colors hover:border-brand/40 hover:bg-bg-muted/80">
      <div className="flex items-center justify-center gap-2 text-sm font-semibold tracking-tight">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md bg-brand-subtle">
          <Icon className="h-3.5 w-3.5 text-brand" />
        </span>
        {title}
      </div>
      <p className="mt-1 text-[12px] leading-relaxed text-text-muted">{body}</p>
    </div>
  );
}

function Leaf({ icon: Icon, title, body }: { icon: any; title: string; body: string }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-bg-muted/50 px-3 py-2.5 min-w-[150px] transition-all hover:-translate-y-0.5 hover:border-accent/40">
      <div className="flex items-center justify-center gap-1.5 text-xs font-semibold">
        <Icon className="h-3 w-3 text-accent" /> {title}
      </div>
      <p className="mt-0.5 text-[11px] text-text-muted text-center">{body}</p>
    </div>
  );
}

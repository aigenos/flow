import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  AlertTriangle, Check, CheckCircle2, ClipboardCopy, GitBranch, HelpCircle, Link2, Lock, MessageSquare,
  MessagesSquare, Play, Printer, Repeat, Search, ShieldCheck, Wrench,
} from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../components/ui/button";

type Cond = { expr: string; text: string };
type Step = {
  n: number; kind: "ask" | "say" | "do" | "confirm" | "chat" | "repeat"; text: string; notes: string[]; when: Cond[];
  user_only?: boolean; changes_something?: boolean; tool?: string; skill?: string;
  /** A step asked once per item of this list (inside a `repeat:`). */
  in_repeat?: string;
};
type PathRow = {
  steps: number[]; holds: Record<string, boolean>; asks: string[]; yeses: number; runs: string[]; hands_to: string[];
};
type Procedure = {
  name: string; description: string; steps: Step[];
  paths: { conditions: Cond[]; rows: PathRow[]; exact: boolean; too_many?: number };
  writes: Array<{
    tool: string; step: number; description: string; needs: string[];
    inputs: Array<{ name: string; from: string; who: string }>;
  }>;
  lookups: Array<{ name: string; description: string }>;
  markdown?: string; unsupported?: string;
};

const KIND = {
  ask: { icon: HelpCircle, label: "Ask", tone: "bg-sky-500/10 text-sky-700 border-sky-600/25 dark:text-sky-400" },
  say: { icon: MessageSquare, label: "Say", tone: "bg-slate-500/10 text-slate-600 border-slate-500/25 dark:text-slate-300" },
  do: { icon: Wrench, label: "Do", tone: "bg-amber-500/10 text-amber-700 border-amber-600/25 dark:text-amber-400" },
  confirm: { icon: CheckCircle2, label: "Confirm", tone: "bg-emerald-500/10 text-emerald-700 border-emerald-600/25 dark:text-emerald-400" },
  chat: { icon: MessagesSquare, label: "Chat", tone: "bg-fuchsia-500/10 text-fuchsia-700 border-fuchsia-600/25 dark:text-fuchsia-400" },
  repeat: { icon: Repeat, label: "Repeat", tone: "bg-teal-500/10 text-teal-700 border-teal-600/25 dark:text-teal-400" },
} as const;

const title = (name: string) => name.replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

/**
 * A skill read back as a procedure: its steps in plain words, every way
 * through it, and what must be true before anything changes.
 *
 * Everything on this page is computed by the server from the skill file and
 * the tool library. No model is involved, so what is read here is what runs.
 */
export function ProcedurePage() {
  const { name } = useParams();
  const navigate = useNavigate();
  const [page, setPage] = useState<Procedure | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!name) return;
    let live = true;
    setPage(null);
    setError(null);
    setChosen(null);
    fetch(`/api/flow/skills/${encodeURIComponent(name)}/procedure`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 300)}`);
        return r.json();
      })
      .then((j) => { if (live) setPage(j); })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : String(e)); });
    return () => { live = false; };
  }, [name]);

  const onPath = useMemo(() => {
    if (!page || chosen == null) return null;
    return new Set(page.paths.rows[chosen]?.steps ?? []);
  }, [page, chosen]);

  const copy = async () => {
    if (!page?.markdown) return;
    try {
      await navigator.clipboard.writeText(page.markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { /* the clipboard is not available here */ }
  };

  if (error) {
    return (
      <div className="mx-auto max-w-3xl p-8">
        <div className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-sm text-rose-700 dark:text-rose-300">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </div>
      </div>
    );
  }
  if (!page) return <div className="mx-auto max-w-3xl p-8"><div className="h-64 rounded-2xl bg-bg-muted/50 animate-pulse" /></div>;

  const rows = page.paths.rows;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-6 py-8 print:max-w-none print:px-0 print:py-0">
        {/* title */}
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-muted">Procedure</div>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-text-primary">{title(page.name)}</h1>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">{page.description}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2 print:hidden">
            <Button variant="outline" size="sm" onClick={copy} disabled={!page.markdown}>
              {copied ? <Check /> : <ClipboardCopy />} {copied ? "Copied" : "Copy as Markdown"}
            </Button>
            <Button variant="outline" size="sm" onClick={() => window.print()}><Printer /> Print</Button>
            <Button variant="outline" size="sm" onClick={() => navigate(`/canvas/${encodeURIComponent(page.name)}`)}>
              <GitBranch /> Canvas
            </Button>
            <Button size="sm" onClick={() => navigate(`/flow?skill=${encodeURIComponent(page.name)}`)}><Play /> Run it</Button>
          </div>
        </div>

        <p className="mt-4 flex items-start gap-2 rounded-lg border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2 text-[12px] leading-relaxed text-emerald-800 dark:text-emerald-300">
          <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          This page is generated from the skill file and its tools. No model wrote it or reads it at run time, so it cannot say something the flow does not do.
        </p>

        {page.unsupported ? (
          <p className="mt-8 rounded-lg border border-border-subtle bg-bg-muted/40 px-4 py-3 text-sm text-text-secondary">{page.unsupported}</p>
        ) : (
          <>
            {/* 1. the steps */}
            <Heading n="1" title="The procedure">
              {chosen != null && (
                <button onClick={() => setChosen(null)} className="text-[12px] text-brand hover:text-brand-hover print:hidden">
                  showing way {chosen + 1} — show all steps
                </button>
              )}
            </Heading>
            <ol className="mt-3 space-y-2">
              {page.steps.map((step) => {
                const kind = KIND[step.skill ? "do" : step.kind] ?? KIND.say;
                const Icon = step.skill ? Link2 : kind.icon;
                const off = onPath != null && !onPath.has(step.n);
                return (
                  <li key={step.n}
                      className={cn(
                        "flex gap-3 rounded-xl border border-border-subtle bg-bg-surface px-4 py-3 transition-opacity print:break-inside-avoid",
                        step.in_repeat && "ml-8",
                        off && "opacity-30",
                      )}>
                    <span className="mt-0.5 w-5 shrink-0 text-right text-[12px] font-semibold tabular-nums text-text-muted">{step.n}</span>
                    <span className={cn("mt-px inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md border", kind.tone)}>
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                        <span className="text-[14px] font-medium leading-snug text-text-primary">{step.text}</span>
                        {step.user_only && (
                          <span title="Only the user can give this. A value the user did not type or pick is dropped."
                                className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-1.5 py-px text-[10px] font-medium text-emerald-700 dark:text-emerald-400">
                            <Lock className="h-2.5 w-2.5" /> the user only
                          </span>
                        )}
                      </div>
                      {step.when.length > 0 && (
                        <div className="mt-1 text-[12px] text-text-secondary">
                          Only when {step.when.map((w, i) => (
                            <span key={w.expr}>
                              {i > 0 && " and "}
                              <span title={w.expr} className="rounded bg-bg-muted px-1 py-px font-medium text-text-primary">{w.text}</span>
                            </span>
                          ))}
                        </div>
                      )}
                      {step.notes.length > 0 && (
                        <ul className="mt-1.5 space-y-0.5">
                          {step.notes.map((note, i) => (
                            <li key={i} className="flex gap-1.5 text-[12px] leading-relaxed text-text-secondary">
                              <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-text-muted/60" />
                              <span>{note}</span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>

            {/* 2. every way through */}
            <Heading n="2" title="Every way through it" />
            {page.paths.too_many ? (
              <p className="mt-3 text-sm text-text-secondary">
                There are too many combinations of answers to list ({page.paths.too_many}).
              </p>
            ) : (
              <>
                <p className="mt-2 text-sm leading-relaxed text-text-secondary">
                  {rows.length === 1 ? "There is one way through this flow." : `There are ${rows.length} ways through this flow.`}{" "}
                  {page.paths.conditions.length > 0 && (page.paths.exact
                    ? "Each is a combination of answers that can really happen; two conditions that cannot both hold never make a way."
                    : "A condition that reads a tool's result is taken both ways, since only the tool knows.")}{" "}
                  <span className="print:hidden">Select a row to see its steps above.</span>
                </p>
                <div className="mt-3 overflow-x-auto rounded-xl border border-border-subtle">
                  <table className="w-full text-left text-[12px]">
                    <thead className="bg-bg-muted/60 text-text-secondary">
                      <tr>
                        <th className="px-3 py-2 font-semibold">#</th>
                        {page.paths.conditions.map((c) => (
                          <th key={c.expr} title={c.expr} className="px-3 py-2 font-semibold">{c.text}</th>
                        ))}
                        <th className="px-3 py-2 font-semibold">Asks</th>
                        <th className="px-3 py-2 font-semibold">Yeses</th>
                        <th className="px-3 py-2 font-semibold">Runs</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, i) => (
                        <tr key={i} onClick={() => setChosen(chosen === i ? null : i)}
                            className={cn(
                              "cursor-pointer border-t border-border-subtle transition-colors hover:bg-bg-muted/40",
                              chosen === i && "bg-brand-subtle",
                            )}>
                          <td className="px-3 py-2 font-semibold tabular-nums text-text-muted">{i + 1}</td>
                          {page.paths.conditions.map((c) => (
                            <td key={c.expr} className="px-3 py-2">
                              {row.holds[c.expr]
                                ? <span className="font-medium text-emerald-600 dark:text-emerald-400">yes</span>
                                : <span className="text-text-muted">no</span>}
                            </td>
                          ))}
                          <td className="px-3 py-2 font-mono text-[11px] text-text-secondary">{row.asks.join(", ") || "—"}</td>
                          <td className="px-3 py-2 tabular-nums text-text-secondary">{row.yeses}</td>
                          <td className="px-3 py-2 font-mono text-[11px]">
                            {[...row.runs, ...row.hands_to.map((s) => `→ ${s}`)].join(", ") || <span className="text-text-muted">nothing</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="mt-2 text-[12px] text-text-muted">At every confirmation, “no” ends the flow there and nothing below it runs.</p>
              </>
            )}

            {/* 3. before anything changes */}
            {page.writes.length > 0 && (
              <>
                <Heading n="3" title="Before anything changes" />
                <div className="mt-3 space-y-3">
                  {page.writes.map((write) => (
                    <div key={`${write.tool}-${write.step}`} className="rounded-xl border border-amber-500/25 bg-amber-500/[0.04] px-4 py-3 print:break-inside-avoid">
                      <div className="flex items-baseline gap-2">
                        <Wrench className="h-3.5 w-3.5 shrink-0 self-center text-amber-600 dark:text-amber-400" />
                        <span className="font-mono text-[13px] font-semibold text-text-primary">{write.tool}</span>
                        <span className="text-[12px] text-text-muted">step {write.step}</span>
                        {write.description && <span className="text-[12px] text-text-secondary">· {write.description}</span>}
                      </div>
                      <ul className="mt-2 space-y-1">
                        {write.needs.map((need, i) => (
                          <li key={i} className="flex gap-2 text-[12px] leading-relaxed text-text-secondary">
                            <Check className="mt-[3px] h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400" /> {need}
                          </li>
                        ))}
                        <li className="flex gap-2 text-[12px] leading-relaxed text-text-secondary">
                          <Check className="mt-[3px] h-3 w-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
                          It is called with the inputs the user saw when they said yes, or not at all. It runs at most once, and each run is on the audit trail.
                        </li>
                      </ul>
                      {write.inputs.length > 0 && (
                        <table className="mt-2 w-full text-left text-[12px]">
                          <thead className="text-text-muted">
                            <tr><th className="py-1 pr-3 font-medium">Input</th><th className="py-1 pr-3 font-medium">Comes from</th><th className="py-1 font-medium">Who may give it</th></tr>
                          </thead>
                          <tbody>
                            {write.inputs.map((item) => (
                              <tr key={item.name} className="border-t border-border-subtle/70">
                                <td className="py-1 pr-3 font-mono text-text-primary">{item.name}</td>
                                <td className="py-1 pr-3 text-text-secondary">{item.from}</td>
                                <td className="py-1 text-text-secondary">{item.who || "—"}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}

            {/* 4. lookups */}
            {page.lookups.length > 0 && (
              <>
                <Heading n={page.writes.length > 0 ? "4" : "3"} title="What it looks up" />
                <ul className="mt-3 space-y-1.5">
                  {page.lookups.map((tool) => (
                    <li key={tool.name} className="flex items-baseline gap-2 text-[12px]">
                      <Search className="h-3 w-3 shrink-0 self-center text-sky-500" />
                      <span className="font-mono text-text-primary">{tool.name}</span>
                      <span className="text-text-secondary">{tool.description}</span>
                    </li>
                  ))}
                </ul>
                <p className="mt-2 text-[12px] text-text-muted">
                  A lookup changes nothing. It runs when a step first needs its result, and again when an answer it used changes.
                </p>
              </>
            )}
          </>
        )}

        <p className="mt-10 border-t border-border-subtle pt-4 text-[11px] text-text-muted print:hidden">
          Written as steps in <span className="font-mono">skills/{page.name}/SKILL.md</span>.{" "}
          <Link to={`/canvas/${encodeURIComponent(page.name)}`} className="text-brand hover:text-brand-hover">Edit the steps on the canvas →</Link>
        </p>
      </div>
    </div>
  );
}

function Heading({ n, title, children }: { n: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="mt-9 flex items-baseline gap-2.5 border-b border-border-subtle pb-2">
      <span className="text-[12px] font-semibold tabular-nums text-text-muted">{n}</span>
      <h2 className="text-base font-semibold tracking-tight text-text-primary">{title}</h2>
      <span className="ml-auto">{children}</span>
    </div>
  );
}

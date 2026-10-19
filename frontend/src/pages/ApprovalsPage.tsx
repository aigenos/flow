import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Check, Clock, Inbox, ShieldAlert, Wrench, X } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../components/ui/button";

type Request = {
  id: string; who: string; question: string; skill: string;
  runs: Array<{ title: string; note: string; inputs: Array<{ label: string; shown: string }> }>;
  answers: Array<{ label: string; shown: string }>;
  status: "pending" | "approved" | "declined" | "withdrawn";
  decided_by: string; note: string; created: string; decided: string;
};

const NAME_KEY = "aigenos-approver-name";
const words = (name: string) => name.replace(/[-_]/g, " ").replace(/^\w/, (c) => c.toUpperCase());

function ago(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/**
 * Requests waiting on someone else's yes.
 *
 * A flow reaches a `confirm:` step with `by:` and stops. What it filed is
 * listed here: the question, which tool a yes will run, and with which inputs.
 * Answering here is the only way that yes reaches the flow; the user in the
 * conversation, and the model, cannot give it.
 */
export function ApprovalsPage() {
  const [requests, setRequests] = useState<Request[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [denied, setDenied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState(() => { try { return localStorage.getItem(NAME_KEY) ?? ""; } catch { return ""; } });
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/flow/approvals");
      if (r.status === 403) {
        const text = await r.text();
        let detail = text;
        try { detail = String(JSON.parse(text).detail ?? text); } catch { /* shown as it came */ }
        setDenied(detail);
        return;
      }
      if (r.ok) setRequests((await r.json()).approvals ?? []);
    } catch { /* the list keeps what it had */ } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const decide = async (request: Request, approved: boolean) => {
    setBusy(request.id);
    setError(null);
    try { localStorage.setItem(NAME_KEY, name); } catch { /* not remembered */ }
    try {
      const r = await fetch(`/api/flow/approvals/${encodeURIComponent(request.id)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ approved, by: name.trim(), note: (notes[request.id] ?? "").trim() }),
      });
      if (!r.ok) {
        const text = await r.text();
        let detail = text;
        try { detail = String(JSON.parse(text).detail ?? text); } catch { /* shown as it came */ }
        throw new Error(detail);
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const waiting = requests.filter((r) => r.status === "pending");
  const answered = requests.filter((r) => r.status !== "pending").slice(0, 12);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-6 py-8">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-text-muted">Approvals</div>
            <h1 className="mt-1 text-2xl font-bold tracking-tight text-text-primary">Waiting for a yes</h1>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">
              A flow that needs someone else's yes stops and files a request here. You see which tool a yes will
              run and with which inputs. Your answer is the only way it reaches the flow: the user in the
              conversation cannot give it, and neither can the model.
            </p>
          </div>
          <label className="block w-44 shrink-0">
            <span className="text-[11px] uppercase tracking-wider text-text-muted">Answering as</span>
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="your name"
                   className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand" />
          </label>
        </div>

        {denied && (
          <div className="mt-5 flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/[0.08] px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="leading-relaxed">Requests cannot be read or answered from here. {denied}</span>
          </div>
        )}
        {error && (
          <div className="mt-5 flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /> {error}
          </div>
        )}

        <div className="mt-6 space-y-4">
          {loaded && waiting.length === 0 && !denied && (
            <div className="rounded-2xl border border-dashed border-border px-6 py-10 text-center">
              <Inbox className="mx-auto h-6 w-6 text-text-muted" />
              <p className="mt-2 text-sm font-medium text-text-secondary">Nothing is waiting</p>
              <p className="mx-auto mt-1 max-w-sm text-xs leading-relaxed text-text-muted">
                A request appears when a flow reaches a step written{" "}
                <code className="font-mono">confirm: "…"</code> with <code className="font-mono">by: someone</code>.
                Add one on the <Link to="/canvas" className="text-brand hover:text-brand-hover">Canvas</Link>: a Confirm
                step, then “Who says yes?”.
              </p>
            </div>
          )}
          {waiting.map((request) => (
            <div key={request.id} className="rounded-2xl border border-border bg-bg-surface p-5 shadow-card">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-px text-[11px] font-medium text-amber-700 dark:text-amber-400">
                  for {request.who}
                </span>
                <span className="font-mono text-[11px] text-text-muted">{request.id}</span>
                <span className="text-[11px] text-text-muted">· {words(request.skill)} · {ago(request.created)}</span>
              </div>
              <h2 className="mt-2 text-lg font-semibold tracking-tight text-text-primary">{request.question}</h2>

              {request.runs.length > 0 && (
                <div className="mt-3 rounded-xl border border-amber-500/25 bg-amber-500/[0.04] px-4 py-3">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
                    {request.runs.length === 1 ? "A yes runs this, once" : "A yes runs these, once each"}
                  </div>
                  {request.runs.map((run, i) => (
                    <div key={i} className="mt-2">
                      <div className="flex items-baseline gap-2">
                        <Wrench className="h-3.5 w-3.5 shrink-0 self-center text-amber-600 dark:text-amber-400" />
                        <span className="text-[13px] font-semibold text-text-primary">{run.title}</span>
                        {run.note && <span className="text-[12px] text-text-secondary">{run.note}</span>}
                      </div>
                      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 pl-5 text-[12px]">
                        {run.inputs.map((item) => (
                          <div key={item.label} className="contents">
                            <dt className="text-text-muted">{item.label}</dt>
                            <dd className="font-medium text-text-primary">{item.shown}</dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                  ))}
                </div>
              )}

              {request.answers.length > 0 && (
                <details className="mt-3 text-[12px] text-text-secondary">
                  <summary className="cursor-pointer select-none text-text-muted hover:text-text-primary">
                    What the user answered ({request.answers.length})
                  </summary>
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
                    {request.answers.map((item) => (
                      <div key={item.label} className="contents">
                        <dt className="text-text-muted">{item.label}</dt>
                        <dd className="text-text-primary">{item.shown}</dd>
                      </div>
                    ))}
                  </dl>
                </details>
              )}

              <div className="mt-4 flex flex-wrap items-end gap-2">
                <label className="block min-w-0 flex-1">
                  <span className="text-[11px] uppercase tracking-wider text-text-muted">A note for them (optional)</span>
                  <input value={notes[request.id] ?? ""} placeholder="why, or what to do next"
                         onChange={(e) => setNotes({ ...notes, [request.id]: e.target.value })}
                         className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand" />
                </label>
                <Button variant="outline" size="sm" className="h-9" disabled={busy === request.id}
                        onClick={() => decide(request, false)}>
                  <X /> No
                </Button>
                <Button size="sm" className="h-9" disabled={busy === request.id} onClick={() => decide(request, true)}>
                  <Check /> Yes, go ahead
                </Button>
              </div>
            </div>
          ))}
        </div>

        {answered.length > 0 && (
          <>
            <h3 className="mt-10 border-b border-border-subtle pb-2 text-sm font-semibold text-text-primary">Answered</h3>
            <ul className="mt-2 divide-y divide-border-subtle">
              {answered.map((request) => (
                <li key={request.id} className="flex items-baseline gap-2 py-2 text-[12px]">
                  {request.status === "approved" && <Check className="h-3 w-3 shrink-0 self-center text-emerald-500" />}
                  {request.status === "declined" && <X className="h-3 w-3 shrink-0 self-center text-rose-500" />}
                  {request.status === "withdrawn" && <Clock className="h-3 w-3 shrink-0 self-center text-text-muted" />}
                  <span className={cn("font-medium", request.status === "withdrawn" ? "text-text-muted" : "text-text-primary")}>
                    {request.question}
                  </span>
                  <span className="text-text-muted">
                    {request.status === "withdrawn"
                      ? "withdrawn: what it asked for changed, or the flow was cancelled"
                      : `${request.status} by ${request.decided_by || request.who}${request.note ? `: ${request.note}` : ""}`}
                  </span>
                  <span className="ml-auto shrink-0 font-mono text-[11px] text-text-muted">{request.id}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}

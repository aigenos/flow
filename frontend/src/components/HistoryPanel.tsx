import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GitCompare, History, Loader2, RotateCcw, X } from "lucide-react";
import { api, type Revision, type RevisionKind } from "../lib/api";
import { countChanges, diffLines, hunks, type DiffLine } from "../lib/diff";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { cn } from "../lib/cn";

/**
 * The comparable text for a stored revision. A skill snapshots several files,
 * and SKILL.md is the one worth diffing — the flow itself lives there.
 * Widgets and workflows are stored as specs, so their JSON is the text.
 */
function primaryText(kind: RevisionKind, body: unknown): string {
  if (kind === "skill" && body && typeof body === "object") {
    const files = body as Record<string, string>;
    return files["SKILL.md"] ?? JSON.stringify(body, null, 2);
  }
  return typeof body === "string" ? body : JSON.stringify(body, null, 2);
}

/** "2m ago" / "3h ago" / "yesterday" — compact, no library. */
export function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const s = Math.max(0, (Date.now() - then) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 172800) return "yesterday";
  if (s < 2592000) return `${Math.floor(s / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

type Props = {
  kind: RevisionKind;
  refId: string;
  /** Called with the version to restore; the panel refreshes afterwards. */
  onRestore: (version: number) => Promise<unknown>;
  /** Fired after a successful restore so the host page can reload its editor. */
  onRestored?: () => void;
  className?: string;
};

/**
 * Version history for one artifact — every Save in the Studio lands in the
 * SQLite workspace store, and this panel lists those snapshots with
 * one-click restore. Files on disk stay the source of truth; a restore is
 * just another validated save.
 */
export function HistoryPanel({ kind, refId, onRestore, onRestored, className }: Props) {
  const [open, setOpen] = useState(false);
  const [revisions, setRevisions] = useState<Revision[] | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which older version is being compared against the current one, and the
  // computed hunks. Loaded lazily — most opens never diff.
  const [diffOf, setDiffOf] = useState<number | null>(null);
  const [diff, setDiff] = useState<DiffLine[] | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Both are O(diff) and the render path touches them repeatedly.
  const blocks = useMemo(() => (diff ? hunks(diff) : []), [diff]);
  const counts = useMemo(() => (diff ? countChanges(diff) : { added: 0, removed: 0 }), [diff]);

  const load = useCallback(() => {
    api
      .listRevisions(kind, refId)
      .then(setRevisions)
      .catch(() => setRevisions([]));
  }, [kind, refId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  /** Compare one revision against the newest — "what changed since then?". */
  const showDiff = async (version: number) => {
    const current = revisions?.[0]?.version;
    if (current === undefined) return;
    setDiffOf(version);
    setDiff(null);
    setError(null);
    try {
      const [older, newer] = await Promise.all([
        api.getRevision(kind, refId, version),
        api.getRevision(kind, refId, current),
      ]);
      setDiff(diffLines(primaryText(kind, older.body), primaryText(kind, newer.body)));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setDiffOf(null);
    }
  };

  const restore = async (version: number) => {
    setBusy(version);
    setError(null);
    try {
      await onRestore(version);
      load();
      onRestored?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <Button variant="outline" size="sm" onClick={() => setOpen((v) => !v)} title="Version history">
        <History /> History
      </Button>

      {open && (
        <div
          className={cn(
            "absolute right-0 top-full z-50 mt-2 overflow-hidden rounded-xl",
            "border border-border bg-bg-elevated shadow-xl theme-transition",
            "animate-in fade-in slide-in-from-top-1 duration-150",
            // A diff needs room for real lines; the list alone does not.
            diffOf === null ? "w-80" : "w-[32rem]",
          )}
        >
          <div className="flex items-center justify-between border-b border-border-subtle px-3.5 py-2.5">
            <div className="text-xs font-semibold text-text-primary">Version history</div>
            <div className="font-mono text-[11px] text-text-muted">{refId}</div>
          </div>

          <div className="max-h-72 overflow-y-auto p-1.5">
            {revisions === null && (
              <div className="flex items-center gap-2 px-3 py-4 text-xs text-text-muted">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading…
              </div>
            )}
            {revisions?.length === 0 && (
              <div className="px-3 py-4 text-xs text-text-muted">
                No saved versions yet — every Save lands here.
              </div>
            )}
            {revisions?.map((rev, i) => (
              <div
                key={rev.version}
                className="group flex items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-bg-muted/70"
              >
                <div className="relative flex h-full flex-col items-center">
                  <span
                    className={cn(
                      "h-2 w-2 rounded-full",
                      i === 0 ? "bg-brand shadow-glow" : "bg-border",
                    )}
                  />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="font-mono text-xs font-semibold text-text-primary">
                      v{rev.version}
                    </span>
                    {i === 0 && <Badge variant="success">current</Badge>}
                    {rev.label && (
                      <span className="truncate text-[11px] italic text-text-muted">{rev.label}</span>
                    )}
                  </div>
                  <div className="text-[11px] text-text-muted">{timeAgo(rev.created_at)}</div>
                </div>
                {i !== 0 && (
                  <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                    <Button
                      variant="ghost"
                      size="sm"
                      title="Compare with the current version"
                      onClick={() => showDiff(rev.version)}
                    >
                      <GitCompare /> Diff
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy !== null}
                      onClick={() => restore(rev.version)}
                    >
                      {busy === rev.version ? (
                        <Loader2 className="animate-spin" />
                      ) : (
                        <RotateCcw />
                      )}
                      Restore
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>

          {diffOf !== null && (
            <div className="border-t border-border-subtle">
              <div className="flex items-center justify-between px-3.5 py-2">
                <span className="text-[12px] font-medium text-text-primary">
                  v{diffOf} → v{revisions?.[0]?.version}
                  {diff && (
                    <span className="ml-2 font-mono text-[11px] tabular-nums">
                      <span className="text-emerald-600 dark:text-emerald-400">
                        +{counts.added}
                      </span>{" "}
                      <span className="text-rose-600 dark:text-rose-400">
                        −{counts.removed}
                      </span>
                    </span>
                  )}
                </span>
                <button
                  onClick={() => { setDiffOf(null); setDiff(null); }}
                  aria-label="Close diff"
                  className="rounded p-1 text-text-muted transition-colors hover:text-text-primary"
                >
                  <X className="h-3 w-3" />
                </button>
              </div>

              <div className="max-h-72 overflow-auto border-t border-border-subtle bg-bg-muted/30">
                {diff === null ? (
                  <div className="flex items-center gap-2 px-3.5 py-4 text-xs text-text-muted">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Comparing…
                  </div>
                ) : blocks.length === 0 ? (
                  <div className="px-3.5 py-4 text-xs text-text-muted">
                    No changes — these versions are identical.
                  </div>
                ) : (
                  blocks.map((hunk, hi) => (
                    <div key={hi} className={hi > 0 ? "border-t border-border-subtle" : ""}>
                      {hunk.map((line, li) => (
                        <div
                          key={li}
                          className={cn(
                            "flex gap-2 px-3 py-px font-mono text-[10.5px] leading-relaxed",
                            line.kind === "add" && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                            line.kind === "del" && "bg-rose-500/10 text-rose-700 dark:text-rose-300",
                            line.kind === "same" && "text-text-muted",
                          )}
                        >
                          <span className="w-3 shrink-0 select-none opacity-70">
                            {line.kind === "add" ? "+" : line.kind === "del" ? "−" : " "}
                          </span>
                          <span className="whitespace-pre-wrap break-all">{line.text || " "}</span>
                        </div>
                      ))}
                    </div>
                  ))
                )}
              </div>
            </div>
          )}

          {error && (
            <div className="border-t border-border-subtle px-3.5 py-2 text-[11px] text-rose-600 dark:text-rose-400">
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

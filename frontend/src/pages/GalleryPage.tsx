import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight, Check, Download, FileCode2, GitBranch, Loader2, ShieldCheck, Sparkles,
} from "lucide-react";
import { api, type GalleryTemplate } from "../lib/api";
import { cn } from "../lib/cn";
import { Button } from "../components/ui/button";

/**
 * The skills that ship with the repo, as starting points.
 *
 * A blank canvas is the worst first experience for a tool whose whole idea is
 * "the flow is data" — the fastest way to understand the vocabulary is to open
 * one that already works. Installing copies the folder into the live skills
 * dir through the same validated save path as any other edit, so a template
 * that doesn't load is rejected instead of half-installed.
 */
/** How each template is written, as the card says it. */
const MODE_LABEL: Record<string, string> = {
  steps: "steps",
  declarative: "long form",
  imperative: "python",
  none: "instructions",
};

export function GalleryPage() {
  const navigate = useNavigate();
  const [templates, setTemplates] = useState<GalleryTemplate[] | null>(null);
  const [installing, setInstalling] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.gallery().then(setTemplates).catch(() => setTemplates([]));
  }, []);

  useEffect(load, [load]);

  const install = async (name: string) => {
    setInstalling(name);
    setError(null);
    try {
      await api.installTemplate(name);
      navigate(`/canvas/${encodeURIComponent(name)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      load();
    } finally {
      setInstalling(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto w-full max-w-5xl px-6 py-10">
        <header className="border-b border-border-subtle pb-6">
          <div className="flex items-baseline gap-3">
            <span className="font-mono text-[12px] tabular-nums text-text-muted">00</span>
            <span className="text-[12px] font-semibold uppercase tracking-[0.18em] text-brand">
              Gallery
            </span>
          </div>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.02em]">
            Start from one that works.
          </h1>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-text-secondary">
            The Gallery lists the templates that ship in the repository&rsquo;s{" "}
            <code className="text-brand">examples/gallery/</code> folder. Each one is a skill
            written as steps, with its tools beside it in <code className="text-brand">scripts.py</code>.
            Installing copies the folder into your skills folder and opens it on the Canvas, where
            you change it like any skill of your own.
          </p>
        </header>

        {error && (
          <div className="mt-5 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
            {error}
          </div>
        )}

        {templates === null && (
          <div className="flex items-center gap-2 py-16 text-sm text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading templates…
          </div>
        )}

        {templates?.length === 0 && (
          <p className="py-16 text-sm text-text-muted">
            No example skills found — the Gallery reads the repository&rsquo;s{" "}
            <code className="text-brand">examples/</code> folder, which a pip install does not
            include.
          </p>
        )}

        <div className="mt-8 grid gap-x-8 gap-y-9 sm:grid-cols-2">
          {templates?.map((t) => (
            <article key={t.name} className="flex flex-col border-t border-border-subtle pt-5">
              <div className="flex items-center gap-2">
                <h2 className="font-mono text-[13px] font-semibold text-text-primary">{t.name}</h2>
                {t.installed && (
                  <span className="inline-flex items-center gap-1 rounded-full border border-emerald-600/30 bg-emerald-500/10 px-1.5 py-px text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                    <Check className="h-2.5 w-2.5" /> installed
                  </span>
                )}
              </div>

              <p className="mt-2 line-clamp-3 flex-1 text-[13px] leading-relaxed text-text-secondary">
                {t.description || "No description."}
              </p>

              <dl className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[12px] text-text-muted">
                <div className="flex items-center gap-1.5">
                  {t.mode === "imperative" ? (
                    <FileCode2 className="h-3 w-3" />
                  ) : (
                    <GitBranch className="h-3 w-3" />
                  )}
                  <dt className="sr-only">Written as</dt>
                  <dd>{MODE_LABEL[t.mode] ?? t.mode}</dd>
                </div>
                <div className="flex items-center gap-1.5">
                  <Sparkles className="h-3 w-3" />
                  <dt className="sr-only">Nodes on the Canvas</dt>
                  <dd className="tabular-nums">{t.steps} {t.steps === 1 ? "node" : "nodes"}</dd>
                </div>
                <div className="flex items-center gap-1.5">
                  <ShieldCheck className="h-3 w-3" />
                  <dt className="sr-only">Guarantees holding</dt>
                  <dd className="tabular-nums">
                    {t.guarantees} {t.guarantees === 1 ? "guarantee" : "guarantees"}
                  </dd>
                </div>
              </dl>

              <div className="mt-4">
                {t.installed ? (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => navigate(`/canvas/${encodeURIComponent(t.name)}`)}
                  >
                    Open on the Canvas <ArrowRight />
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    disabled={installing !== null}
                    onClick={() => install(t.name)}
                  >
                    {installing === t.name ? (
                      <Loader2 className={cn("animate-spin")} />
                    ) : (
                      <Download />
                    )}
                    Install
                  </Button>
                )}
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

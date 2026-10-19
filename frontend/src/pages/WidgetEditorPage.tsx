import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Code2, Eye, Save, Sparkles, Wand2, X } from "lucide-react";
import { MicButton } from "../chat/MicButton";
import { cn } from "../lib/cn";
import { api } from "../lib/api";
import { HistoryPanel } from "../components/HistoryPanel";
import { CodeEditor } from "../widget-editor/CodeEditor";
import { VisualBuilder } from "../widget-editor/VisualBuilder";
import { stripIds, withIds, type TreeNode } from "../widget-editor/specTree";
import { SpecRenderer } from "../renderer/SpecRenderer";
import { AppDialog, AppDialogDescription, AppDialogTitle } from "../components/AppDialog";

type Mode = "visual" | "code";

const BLANK = {
  id: "",
  version: "1.0.0",
  description: "",
  bindings: [] as string[],
  submit_schema: {},
  spec: { type: "card", props: { title: "New widget" }, children: [] },
};

export function WidgetEditorPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const isNew = !id;

  const [mode, setMode] = useState<Mode>("visual");
  const [meta, setMeta] = useState({ id: "", version: "1.0.0", description: "" });
  // Data-flow contract of the widget (flow bindings + what submit returns).
  // The visual builder doesn't edit these, so a visual save must carry them
  // through unchanged — never reset them to empty.
  const [extras, setExtras] = useState<{ bindings: string[]; submit_schema: any }>({
    bindings: [],
    submit_schema: {},
  });
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [codeText, setCodeText] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [genOpen, setGenOpen] = useState(false);
  const [genPrompt, setGenPrompt] = useState("");
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [providers, setProviders] = useState<Awaited<ReturnType<typeof api.aiProviders>> | null>(null);
  const [lastUsed, setLastUsed] = useState<{ provider: string; model: string } | null>(null);

  // Re-checked on every open: Generate is disabled while no provider is active,
  // so one that was started since the last look has to be noticed.
  useEffect(() => {
    if (genOpen) {
      api.aiProviders().then(setProviders).catch(() => undefined);
    }
  }, [genOpen]);

  const loadWidget = useCallback(() => {
    api.getWidget(id!).then((w) => {
      setMeta({ id: w.id, version: w.version ?? "1.0.0", description: w.description ?? "" });
      setExtras({ bindings: w.bindings ?? [], submit_schema: w.submit_schema ?? {} });
      setTree(withIds(w.spec));
      setCodeText(JSON.stringify(w, null, 2));
    });
  }, [id]);

  useEffect(() => {
    if (isNew) {
      setMeta({ id: "", version: "1.0.0", description: "" });
      setExtras({ bindings: [], submit_schema: {} });
      setTree(withIds(BLANK.spec));
      setCodeText(JSON.stringify(BLANK, null, 2));
      return;
    }
    loadWidget();
  }, [isNew, loadWidget]);

  // Visual → Code: rebuild JSON when tree/meta changes (only while in visual mode)
  useEffect(() => {
    if (mode !== "visual" || !tree) return;
    const obj = {
      id: meta.id,
      version: meta.version,
      description: meta.description,
      bindings: extras.bindings,
      submit_schema: extras.submit_schema,
      spec: stripIds(tree),
    };
    setCodeText(JSON.stringify(obj, null, 2));
  }, [tree, meta, extras, mode]);

  const enterCode = () => setMode("code");

  // Code → Visual: parse JSON, repopulate tree
  const enterVisual = () => {
    try {
      const parsed = JSON.parse(codeText);
      setMeta({
        id: parsed.id ?? "",
        version: parsed.version ?? "1.0.0",
        description: parsed.description ?? "",
      });
      setExtras({ bindings: parsed.bindings ?? [], submit_schema: parsed.submit_schema ?? {} });
      setTree(withIds(parsed.spec ?? BLANK.spec));
      setCodeError(null);
      setMode("visual");
    } catch (e) {
      setCodeError((e as Error).message);
    }
  };

  const save = async () => {
    let payload: any;
    if (mode === "code") {
      try {
        payload = JSON.parse(codeText);
      } catch (e) {
        setCodeError((e as Error).message);
        return;
      }
    } else {
      if (!tree) return;
      payload = {
        id: meta.id,
        version: meta.version,
        description: meta.description,
        bindings: extras.bindings,
        submit_schema: extras.submit_schema,
        spec: stripIds(tree),
      };
    }
    if (!payload.id) {
      alert("Widget id is required");
      return;
    }
    setSaving(true);
    try {
      await api.saveWidget(payload.id, payload);
      setSavedAt(Date.now());
      if (isNew) navigate(`/widgets/${payload.id}`, { replace: true });
    } catch (e) {
      alert(`Save failed: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  };

  const runGenerate = async () => {
    if (!genPrompt.trim()) return;
    setGenerating(true);
    setGenError(null);
    try {
      const { widget, provider, model } = await api.generateWidget(
        genPrompt.trim(),
        meta.id || undefined,
      );
      setMeta({
        id: widget.id ?? meta.id,
        version: widget.version ?? "1.0.0",
        description: widget.description ?? "",
      });
      setExtras({ bindings: widget.bindings ?? [], submit_schema: widget.submit_schema ?? {} });
      setTree(withIds(widget.spec));
      setCodeText(JSON.stringify(widget, null, 2));
      setLastUsed({ provider, model });
      setMode("visual");
      setGenOpen(false);
      setGenPrompt("");
    } catch (e) {
      setGenError((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const savedText = useMemo(() => {
    if (!savedAt) return null;
    const sec = Math.round((Date.now() - savedAt) / 1000);
    return sec < 5 ? "Saved" : `Saved ${sec}s ago`;
  }, [savedAt]);

  if (!tree) {
    return <div className="p-10 text-sm text-text-muted">Loading…</div>;
  }

  return (
    <div className="h-full flex flex-col">
      <div className="border-b border-border-subtle px-6 py-3 flex items-center gap-3">
        <button
          onClick={() => navigate("/widgets")}
          className="rounded-lg p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-muted"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>

        <input
          type="text"
          placeholder="widget_id"
          value={meta.id}
          onChange={(e) => setMeta({ ...meta, id: e.target.value })}
          disabled={!isNew && Boolean(id)}
          className={cn(
            "rounded-md border border-border bg-bg-muted px-2.5 py-1 text-sm w-40",
            "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand",
            !isNew && "opacity-60 cursor-not-allowed",
          )}
        />
        <input
          type="text"
          placeholder="v1.0.0"
          value={meta.version}
          onChange={(e) => setMeta({ ...meta, version: e.target.value })}
          className="rounded-md border border-border bg-bg-muted px-2.5 py-1 text-sm w-20 focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
        />
        <input
          type="text"
          placeholder="Description"
          value={meta.description}
          onChange={(e) => setMeta({ ...meta, description: e.target.value })}
          className="flex-1 rounded-md border border-border bg-bg-muted px-2.5 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand"
        />

        <button
          onClick={() => setGenOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-brand/30 bg-brand-subtle px-2.5 py-1 text-xs text-brand hover:bg-brand/20 transition-colors"
          title="Generate widget from natural language"
        >
          <Wand2 className="h-3 w-3" /> Generate
        </button>
        {lastUsed && (
          <span className="text-[11px] text-text-muted">
            via {lastUsed.provider}·{lastUsed.model}
          </span>
        )}

        <div className="flex rounded-lg border border-border-subtle p-0.5 bg-bg-muted">
          <button
            onClick={enterVisual}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs",
              mode === "visual" ? "bg-bg-elevated text-text-primary" : "text-text-secondary hover:text-text-primary",
            )}
          >
            <Sparkles className="h-3 w-3" /> Visual
          </button>
          <button
            onClick={enterCode}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs",
              mode === "code" ? "bg-bg-elevated text-text-primary" : "text-text-secondary hover:text-text-primary",
            )}
          >
            <Code2 className="h-3 w-3" /> Code
          </button>
        </div>

        {savedText && <span className="text-xs text-text-muted">{savedText}</span>}

        {!isNew && id && (
          <HistoryPanel
            kind="widget"
            refId={id}
            onRestore={(version) => api.restoreWidget(id, version)}
            onRestored={loadWidget}
          />
        )}

        <button
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-3 py-1.5 text-sm font-medium shadow-glow hover:bg-brand-hover transition-colors disabled:opacity-50"
        >
          <Save className="h-3.5 w-3.5" /> {saving ? "Saving…" : "Save"}
        </button>
      </div>

      <div className="flex-1 min-h-0 p-4">
        {mode === "visual" ? (
          <VisualBuilder root={tree} onChange={setTree} />
        ) : (
          <div className="grid grid-cols-2 gap-4 h-full">
            <CodeEditor value={codeText} onChange={setCodeText} language="json" />
            <div className="rounded-xl border border-border-subtle bg-bg-surface p-4 overflow-y-auto scrollbar-thin">
              <div className="text-xs uppercase tracking-wider text-text-muted mb-3 flex items-center gap-1.5">
                <Eye className="h-3 w-3" /> Live preview
              </div>
              {codeError ? (
                <div className="text-xs text-rose-600 dark:text-rose-400 font-mono">{codeError}</div>
              ) : (
                <CodePreview text={codeText} />
              )}
            </div>
          </div>
        )}
      </div>

      <AppDialog
        open={genOpen}
        onOpenChange={(nextOpen) => { if (!nextOpen && !generating) setGenOpen(false); }}
        className="max-w-xl p-6"
      >
            <div className="flex items-start justify-between mb-4">
              <div>
                <AppDialogTitle className="text-base font-semibold text-text-primary flex items-center gap-2">
                  <Wand2 className="h-4 w-4 text-brand" /> Generate widget from prompt
                </AppDialogTitle>
                <AppDialogDescription className="text-xs text-text-secondary mt-1">
                  Describe the UI you want. The model drafts a spec you can edit.
                </AppDialogDescription>
              </div>
              <button
                onClick={() => !generating && setGenOpen(false)}
                className="rounded p-1 text-text-muted hover:text-text-primary hover:bg-bg-muted"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="mb-1.5 flex justify-end">
              <MicButton value={genPrompt} onChange={setGenPrompt} disabled={generating} className="rounded-lg px-2 py-1" />
            </div>
            <textarea
              value={genPrompt}
              onChange={(e) => setGenPrompt(e.target.value)}
              disabled={generating}
              rows={4}
              placeholder="e.g. A confirmation card asking the user to approve a refund of $129 for order ORD-1024 with Yes/No buttons"
              className="w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand resize-y"
              autoFocus
            />

            <ProviderStatus providers={providers} />

            {genError && (
              <div className="mt-3 rounded-lg border border-rose-500/30 bg-rose-500/10 p-2.5 text-xs text-rose-700 dark:text-rose-300 font-mono whitespace-pre-wrap">
                {genError}
              </div>
            )}

            <div className="mt-4 flex items-center justify-between">
              <div className="text-[12px] text-text-muted">
                Replaces the current widget contents.
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => !generating && setGenOpen(false)}
                  className="rounded-lg px-3 py-1.5 text-sm text-text-secondary hover:text-text-primary hover:bg-bg-muted"
                >
                  Cancel
                </button>
                <button
                  onClick={runGenerate}
                  disabled={generating || !genPrompt.trim() || (providers !== null && !providers.active)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-3 py-1.5 text-sm font-medium shadow-glow hover:bg-brand-hover transition-colors disabled:opacity-50"
                >
                  <Wand2 className="h-3.5 w-3.5" />
                  {generating ? "Generating…" : "Generate"}
                </button>
              </div>
            </div>
      </AppDialog>
    </div>
  );
}

function ProviderStatus({
  providers,
}: {
  providers: Awaited<ReturnType<typeof api.aiProviders>> | null;
}) {
  if (!providers) {
    return <div className="mt-3 text-[12px] text-text-muted">Checking providers…</div>;
  }
  if (!providers.active) {
    return (
      <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-xs text-amber-800 dark:text-amber-200 leading-relaxed">
        <div className="font-medium mb-1">No provider found for widget generation.</div>
        Widget generation currently works with Anthropic
        (<code className="text-amber-900 dark:text-amber-100">ANTHROPIC_API_KEY</code>) or a local
        Ollama model. Other providers are not wired to this dialog yet.
        <pre className="mt-1 text-[11px] text-amber-900 dark:text-amber-100">ollama pull llama3.2 &amp;&amp; ollama serve</pre>
      </div>
    );
  }
  const active = providers.active;
  const label =
    active === "anthropic"
      ? `Claude · ${providers.anthropic.model}`
      : `Ollama · ${providers.ollama.model}`;
  const other = active === "anthropic" ? providers.ollama : providers.anthropic;
  return (
    <div className="mt-3 flex items-center justify-between text-[12px]">
      <span className="text-text-secondary">
        Using <span className="text-brand font-medium">{label}</span>
      </span>
      {other.available && (
        <span className="text-text-muted">
          (alternate available: {active === "anthropic" ? "Ollama" : "Claude"})
        </span>
      )}
    </div>
  );
}

function CodePreview({ text }: { text: string }) {
  try {
    const parsed = JSON.parse(text);
    if (!parsed.spec) return <div className="text-xs text-text-muted">Missing `spec` field.</div>;
    return <SpecRenderer spec={parsed.spec} onSubmit={(p) => console.log("[preview submit]", p)} />;
  } catch (e) {
    return <div className="text-xs text-rose-600 dark:text-rose-400 font-mono">{(e as Error).message}</div>;
  }
}

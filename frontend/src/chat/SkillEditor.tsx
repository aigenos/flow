/**
 * SkillEditor — Monaco-powered editor for in-place skill iteration.
 *
 * Opened from the empty-state skill list. Lets the user:
 *   - Read the current SKILL.md and handlers.py
 *   - Edit them with full Monaco syntax highlighting
 *   - Save → backend re-validates, hot-registers into the live registry,
 *     and the next chat turn picks up the changes (no server restart)
 *   - Delete the skill entirely
 *
 * Rollback is handled server-side: if the edited files fail to parse
 * or load as a Skill, the prior version is restored from snapshot and
 * the editor receives a 422 with the reason.
 */
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { OnMount } from "@monaco-editor/react";
import { X, Save, Trash2, Loader2, AlertTriangle, Check, FileText, Code2 } from "lucide-react";
import { AppDialog, AppDialogDescription, AppDialogTitle } from "../components/AppDialog";

type Tab = "skill_md" | "handlers_py";

const Editor = lazy(() => import("@monaco-editor/react"));

type Props = {
  /** Open / closed. Closed = component returns null. */
  open: boolean;
  /** Which skill to edit; null = closed. */
  skillName: string | null;
  onClose: () => void;
  /** Notify parent to refresh the skills list after save/delete. */
  onSaved?: (info: { name: string; description: string }) => void;
  onDeleted?: (name: string) => void;
};

type FilesResponse = {
  name: string;
  skill_md: string;
  handlers_py: string;
};

export function SkillEditor({ open, skillName, onClose, onSaved, onDeleted }: Props) {
  const [files, setFiles] = useState<FilesResponse | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("skill_md");
  const [phase, setPhase] = useState<"loading" | "idle" | "saving" | "deleting" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [savedFlash, setSavedFlash] = useState(false);

  // Hold the current editor buffers separately from `files` so cancel works.
  const buffersRef = useRef<{ skill_md: string; handlers_py: string }>({
    skill_md: "",
    handlers_py: "",
  });

  // Load files when opened or skill switched
  useEffect(() => {
    if (!open || !skillName) return;
    setPhase("loading");
    setError(null);
    setDirty(false);
    fetch(`/api/flow/skills/${encodeURIComponent(skillName)}/files`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
        return (await r.json()) as FilesResponse;
      })
      .then((j) => {
        setFiles(j);
        buffersRef.current = { skill_md: j.skill_md, handlers_py: j.handlers_py };
        setPhase("idle");
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        setPhase("error");
      });
  }, [open, skillName]);

  const onChange = (tab: Tab, value: string | undefined) => {
    buffersRef.current[tab] = value ?? "";
    setDirty(true);
  };

  const onSave = async () => {
    if (!skillName || !files) return;
    setPhase("saving");
    setError(null);
    try {
      const body: { skill_md?: string; handlers_py?: string } = {};
      if (buffersRef.current.skill_md !== files.skill_md) body.skill_md = buffersRef.current.skill_md;
      if (buffersRef.current.handlers_py !== files.handlers_py) body.handlers_py = buffersRef.current.handlers_py;

      const r = await fetch(`/api/flow/skills/${encodeURIComponent(skillName)}`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 500)}`);
      const updated = (await r.json()) as { name: string; description: string };

      // Update the baseline so a second save with no further edits is a no-op
      setFiles({
        name: updated.name,
        skill_md: buffersRef.current.skill_md,
        handlers_py: buffersRef.current.handlers_py,
      });
      setDirty(false);
      setPhase("idle");
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
      onSaved?.(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  const onDelete = async () => {
    if (!skillName) return;
    if (!window.confirm(`Delete the skill "${skillName}"?\n\nThis removes the folder from disk and unregisters the skill from the running engine. Cannot be undone.`)) {
      return;
    }
    setPhase("deleting");
    setError(null);
    try {
      const r = await fetch(`/api/flow/skills/${encodeURIComponent(skillName)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).slice(0, 200)}`);
      onDeleted?.(skillName);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  const onEditorMount: OnMount = (editor) => {
    // Cmd/Ctrl+S → save
    editor.addCommand(
      // Monaco's KeyMod.CtrlCmd | KeyCode.KeyS — encoded as constants per docs
      2048 | 49,
      () => void onSave(),
    );
  };

  return (
    <AppDialog
      open={open && Boolean(skillName)}
      onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}
      className="max-w-5xl h-[calc(100dvh-2rem)] flex flex-col overflow-hidden"
    >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-border-subtle">
          <div className="flex items-center gap-3 min-w-0">
            <div className="shrink-0 inline-flex h-8 w-8 items-center justify-center rounded-lg bg-bg-muted border border-border-subtle">
              <Code2 className="h-4 w-4 text-brand" />
            </div>
            <div className="min-w-0">
              <AppDialogTitle className="text-sm font-semibold text-text-primary truncate">
                Edit skill: <span className="font-mono text-brand">{skillName}</span>
              </AppDialogTitle>
              <AppDialogDescription className="text-[12px] text-text-muted">
                Save (Ctrl/Cmd+S) re-validates, hot-reloads. Failed validation rolls back automatically.
              </AppDialogDescription>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-text-muted hover:bg-bg-muted hover:text-text-primary focus-ring"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex items-center border-b border-border-subtle bg-bg text-xs">
          <TabButton
            active={activeTab === "skill_md"}
            onClick={() => setActiveTab("skill_md")}
            icon={<FileText className="h-3.5 w-3.5" />}
            label="SKILL.md"
          />
          <TabButton
            active={activeTab === "handlers_py"}
            onClick={() => setActiveTab("handlers_py")}
            icon={<Code2 className="h-3.5 w-3.5" />}
            label="handlers.py"
          />
          <div className="flex-1" />
          {dirty && (
            <span className="px-3 text-[12px] text-amber-600 dark:text-amber-400">● unsaved</span>
          )}
          {savedFlash && (
            <span className="px-3 inline-flex items-center gap-1 text-[12px] text-emerald-600 dark:text-emerald-400">
              <Check className="h-3 w-3" /> saved + hot-reloaded
            </span>
          )}
        </div>

        {/* Editor */}
        <div className="flex-1 min-h-0 relative">
          {phase === "loading" && (
            <div className="absolute inset-0 grid place-items-center text-text-muted text-sm">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          )}
          {files && phase !== "loading" && (
            <Suspense fallback={<div className="h-full bg-bg-muted animate-pulse" />}>
              <Editor
                key={activeTab}
                defaultValue={
                  activeTab === "skill_md" ? buffersRef.current.skill_md : buffersRef.current.handlers_py
                }
                language={activeTab === "skill_md" ? "markdown" : "python"}
                theme="vs-dark"
                onChange={(v) => onChange(activeTab, v)}
                onMount={onEditorMount}
                options={{
                  fontSize: 13,
                  minimap: { enabled: false },
                  lineNumbers: "on",
                  wordWrap: "on",
                  scrollBeyondLastLine: false,
                  automaticLayout: true,
                  padding: { top: 12, bottom: 12 },
                }}
              />
            </Suspense>
          )}
        </div>

        {/* Error banner */}
        {error && (
          <div className="px-4 py-2 border-t border-red-500/30 bg-red-500/10 flex items-start gap-2 text-xs text-text-secondary">
            <AlertTriangle className="h-4 w-4 shrink-0 text-red-400 mt-0.5" />
            <div className="break-words whitespace-pre-wrap">{error}</div>
          </div>
        )}

        {/* Footer */}
        <div className="flex items-center justify-between px-4 py-3 border-t border-border-subtle bg-bg">
          <button
            onClick={onDelete}
            disabled={phase === "saving" || phase === "deleting"}
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-700 dark:text-red-300 hover:bg-red-500/20 disabled:opacity-50 focus-ring"
            title="Delete this skill (folder + registry)"
          >
            {phase === "deleting" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Trash2 className="h-3.5 w-3.5" />
            )}
            Delete
          </button>
          <div className="flex items-center gap-2">
            <button
              onClick={onClose}
              className="rounded-lg border border-border bg-bg-muted px-3 py-1.5 text-xs text-text-primary hover:border-border-strong"
            >
              Cancel
            </button>
            <button
              onClick={onSave}
              disabled={!dirty || phase === "saving" || phase === "deleting"}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-4 py-1.5 text-xs font-medium shadow-glow hover:bg-brand-hover disabled:opacity-50 focus-ring"
            >
              {phase === "saving" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              Save & reload
            </button>
          </div>
        </div>
    </AppDialog>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "inline-flex items-center gap-1.5 px-4 py-2 font-mono transition-colors " +
        (active
          ? "text-brand bg-bg-elevated border-b-2 border-brand"
          : "text-text-muted hover:text-text-primary hover:bg-bg-elevated")
      }
    >
      {icon} {label}
    </button>
  );
}

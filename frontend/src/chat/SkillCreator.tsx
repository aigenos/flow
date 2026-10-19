/**
 * SkillCreator — modal UI that turns a description into a skill.
 *
 * User flow:
 *   1. Type a natural-language description of a workflow
 *   2. Optionally click Preview → backend asks the LLM for a draft SKILL.md
 *      and handlers.py (collapsed by default); nothing is written
 *   3. Click Create → backend generates the files again, writes them to disk
 *      and hot-registers the skill. An existing skill of the same name is
 *      only replaced after an explicit "Replace it".
 *   4. The parent's list is refreshed as soon as the skill exists (onRefresh)
 *   5. "Run it" hands the skill to the parent (onCreated); "Open in Canvas"
 *      switches to Studio and opens the skill's steps
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMode } from "../hooks/useMode";
import { MicButton } from "./MicButton";
import { AppDialog, AppDialogDescription, AppDialogTitle } from "../components/AppDialog";
import { Sparkles, Wand2, X, Loader2, Check, AlertTriangle, Play } from "lucide-react";

type GenerateResponse = {
  name: string;
  "SKILL.md": string;
  "handlers.py": string;
  spec: Record<string, unknown>;
};

type CreateResponse = {
  name: string;
  description: string;
  skill_md: string;
  handlers_py: string;
  fields: string[];
  triggers: string[];
};

type Props = {
  open: boolean;
  onClose: () => void;
  /** Pre-fill the description (e.g. from the Workspace AI bar or a template). */
  seedDescription?: string;
  /** Called as soon as a skill has been written, so the parent's list is current however the dialog is dismissed. */
  onRefresh?: () => void;
  /** Called when the user presses "Run it" on a created skill. The parent decides what running means. */
  onCreated: (name: string, trigger: string) => void;
};

const EXAMPLES = [
  "Handle customer refund requests — collect order ID, amount, and reason; confirm before submitting.",
  "Gym membership freeze — collect member ID, start date, and number of weeks; confirm before submitting.",
  "Restaurant reservation — date, time, party size, and seating preference; confirm before booking.",
  "Expense report — amount, category, and receipt URL; confirm before filing.",
];

export function SkillCreator({ open, onClose, seedDescription, onRefresh, onCreated }: Props) {
  const navigate = useNavigate();
  const { setMode } = useMode();
  const [description, setDescription] = useState(seedDescription ?? "");
  const [phase, setPhase] = useState<"input" | "previewing" | "creating" | "done" | "error">("input");
  const [preview, setPreview] = useState<GenerateResponse | null>(null);
  const [result, setResult] = useState<CreateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Name of the existing skill a create collided with ("" when the server did not say).
  const [conflict, setConflict] = useState<string | null>(null);
  // Set when "Replace it" came back under another name, leaving this skill as it was.
  const [untouched, setUntouched] = useState<string | null>(null);
  const [showFiles, setShowFiles] = useState<"none" | "skill" | "handlers">("none");

  // Seed (or re-seed) the description each time the modal opens from a prompt/template.
  useEffect(() => {
    if (open && seedDescription) setDescription(seedDescription);
  }, [open, seedDescription]);

  // A draft and a "replace?" question both belong to the description they were
  // made for; neither may outlive an edit to it.
  useEffect(() => {
    setPreview(null);
    setConflict(null);
  }, [description]);

  const reset = () => {
    setDescription("");
    setPhase("input");
    setPreview(null);
    setResult(null);
    setError(null);
    setConflict(null);
    setUntouched(null);
    setShowFiles("none");
  };

  const closeAndReset = () => {
    reset();
    onClose();
  };

  const onPreview = async () => {
    setPhase("previewing");
    setError(null);
    try {
      const r = await fetch("/api/flow/preview-skill", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ description }),
      });
      if (!r.ok) {
        const msg = await r.text();
        throw new Error(`${r.status}: ${msg.slice(0, 300)}`);
      }
      const data = (await r.json()) as GenerateResponse;
      setPreview(data);
      setPhase("input"); // back to input but with preview attached
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  // `overwrite` is true only from the explicit "Replace it" button.
  const onCreate = async (overwrite: boolean) => {
    const replacing = overwrite ? conflict : null;
    setPhase("creating");
    setError(null);
    setConflict(null);
    try {
      const r = await fetch("/api/flow/create-skill", {
        method: "POST",
        headers: { "content-type": "application/json" },
        // `replace` binds the overwrite to the skill the user was asked about:
        // the model picks the name again on this request.
        body: JSON.stringify({ description, overwrite, ...(replacing ? { replace: replacing } : {}) }),
      });
      if (r.status === 409) {
        const body = (await r.json().catch(() => null)) as { name?: unknown } | null;
        setConflict(typeof body?.name === "string" ? body.name : "");
        setPhase("input");
        return;
      }
      if (!r.ok) {
        const msg = await r.text();
        throw new Error(`${r.status}: ${msg.slice(0, 300)}`);
      }
      const data = (await r.json()) as CreateResponse;
      setResult(data);
      // Create generates the files again, so a retry can come back under a
      // different name than the skill it was meant to replace.
      setUntouched(replacing && replacing !== data.name ? replacing : null);
      setPhase("done");
      onRefresh?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("error");
    }
  };

  const onRun = () => {
    if (!result) return;
    onCreated(result.name, result.triggers[0] ?? `start ${result.name}`);
    closeAndReset();
  };

  const onOpenCanvas = () => {
    if (!result) return;
    const { name } = result;
    closeAndReset();
    setMode("studio");
    navigate(`/canvas/${encodeURIComponent(name)}`);
  };

  return (
    <AppDialog
      open={open}
      onOpenChange={(nextOpen) => { if (!nextOpen) closeAndReset(); }}
      className="max-w-2xl max-h-[90dvh] overflow-y-auto"
    >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-border-subtle">
          <div className="flex items-center gap-2">
            <div className="inline-flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-brand to-brand-hover shadow-glow">
              <Wand2 className="h-4 w-4 text-brand-fg" />
            </div>
            <div>
              <AppDialogTitle className="text-sm font-semibold text-text-primary">Create a skill from a description</AppDialogTitle>
              <AppDialogDescription className="text-xs text-text-muted">Generates a SKILL.md and a handlers.py from your description. Run the skill right away, or edit its steps on the Canvas.</AppDialogDescription>
            </div>
          </div>
          <button
            onClick={closeAndReset}
            aria-label="Close skill creator"
            className="control-press icon-control inline-flex items-center justify-center rounded-xl text-text-muted hover:bg-bg-muted hover:text-text-primary focus-ring"
            title="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-4">
          {phase === "done" && result ? (
            <div className="space-y-4 animate-fade-up">
              <div className="flex items-start gap-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
                <Check className="h-5 w-5 shrink-0 text-emerald-400 mt-0.5" />
                <div className="space-y-1 min-w-0">
                  <div className="text-sm font-medium text-text-primary">
                    Skill created: <span className="font-mono text-emerald-700 dark:text-emerald-300">{result.name}</span>
                  </div>
                  <p className="text-xs text-text-secondary">{result.description}</p>
                  {untouched && (
                    <p className="text-xs text-amber-700 dark:text-amber-300">
                      The new skill got a different name, so <span className="font-mono">{untouched}</span> was not replaced.
                    </p>
                  )}
                  <div className="flex flex-wrap gap-1.5 pt-1.5">
                    {result.fields.map((f) => (
                      <span key={f} className="rounded-md bg-bg-muted border border-border-subtle px-1.5 py-0.5 text-[12px] text-text-muted font-mono">
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              </div>

              <FilesPanel skillMd={result.skill_md} handlersPy={result.handlers_py} />

              <div className="flex flex-wrap items-center justify-end gap-2 pt-2">
                <button
                  onClick={closeAndReset}
                  className="rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary hover:border-border-strong"
                >
                  Close
                </button>
                <button
                  onClick={onOpenCanvas}
                  className="rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary hover:border-border-strong focus-ring"
                >
                  Open in Canvas
                </button>
                <button
                  onClick={onRun}
                  className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-fg shadow-glow hover:bg-brand-hover focus-ring"
                >
                  <Play className="h-4 w-4" />
                  Run it
                </button>
              </div>
            </div>
          ) : (
            <>
              <label className="block">
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-text-secondary">Describe what this skill should do</span>
                  <MicButton
                    value={description}
                    onChange={setDescription}
                    disabled={phase === "creating" || phase === "previewing"}
                    className="rounded-lg px-2 py-1"
                  />
                </div>
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="e.g. 'Handle customer refund requests — collect order ID, amount, and reason.'"
                  rows={3}
                  disabled={phase === "creating" || phase === "previewing"}
                  className="w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus-ring focus:border-brand resize-none disabled:opacity-60"
                />
              </label>

              <div className="space-y-1.5">
                <div className="text-[12px] uppercase tracking-wider text-text-muted">Or try one of these</div>
                <div className="grid gap-1.5">
                  {EXAMPLES.map((ex) => (
                    <button
                      key={ex}
                      onClick={() => setDescription(ex)}
                      disabled={phase === "creating" || phase === "previewing"}
                      className="text-left rounded-md border border-border-subtle bg-bg-muted hover:bg-bg-elevated hover:border-border px-3 py-2 text-xs text-text-primary disabled:opacity-50"
                    >
                      {ex}
                    </button>
                  ))}
                </div>
              </div>

              {preview && phase !== "creating" && (
                <div className="animate-fade-up space-y-3">
                  <div className="flex items-center gap-2 rounded-xl border border-brand/30 bg-brand/5 p-3">
                    <Sparkles className="h-4 w-4 shrink-0 text-brand" />
                    <div className="text-xs text-text-secondary min-w-0">
                      Draft of <span className="font-mono text-text-primary">{preview.name}</span>. Create generates the files again, so the saved skill can differ from this draft.
                    </div>
                  </div>
                  <FilesPanel skillMd={preview["SKILL.md"]} handlersPy={preview["handlers.py"]} />
                </div>
              )}

              {conflict !== null && phase !== "creating" && (
                <div role="alert" className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
                  <AlertTriangle className="h-4 w-4 shrink-0 text-amber-400 mt-0.5" />
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="text-xs text-text-primary break-words">
                      {conflict ? (
                        <>A skill named <span className="font-mono">{conflict}</span> already exists.</>
                      ) : (
                        "A skill with this name already exists."
                      )}{" "}
                      Nothing was changed. Replacing it overwrites that skill's SKILL.md and handlers.py.
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Without a name there is nothing to bind the overwrite to. */}
                      {conflict && (
                        <button
                          onClick={() => void onCreate(true)}
                          className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-700 dark:text-amber-300 hover:bg-amber-500/20 focus-ring"
                        >
                          Replace it
                        </button>
                      )}
                      <button
                        onClick={() => setConflict(null)}
                        className="rounded-lg border border-border bg-bg-muted px-3 py-1.5 text-xs text-text-primary hover:border-border-strong focus-ring"
                      >
                        Keep the existing skill
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {phase === "error" && error && (
                <div className="flex items-start gap-2 rounded-xl border border-red-500/30 bg-red-500/10 p-3">
                  <AlertTriangle className="h-4 w-4 shrink-0 text-red-400 mt-0.5" />
                  <div className="text-xs text-text-secondary min-w-0 break-words">{error}</div>
                </div>
              )}

              <div className="flex items-center justify-between gap-2 pt-2">
                <p className="text-[12px] text-text-muted">
                  Local LLM may take 10–60s on first run.
                </p>
                <div className="flex items-center gap-2">
                  {!preview && (
                    <button
                      onClick={onPreview}
                      disabled={!description.trim() || phase === "previewing" || phase === "creating"}
                      className="inline-flex items-center gap-2 rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary hover:border-border-strong disabled:opacity-50 focus-ring"
                    >
                      {phase === "previewing" ? (
                        <>
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Generating…
                        </>
                      ) : (
                        <>
                          <Sparkles className="h-4 w-4" />
                          Preview
                        </>
                      )}
                    </button>
                  )}
                  <button
                    onClick={() => void onCreate(false)}
                    disabled={!description.trim() || phase === "previewing" || phase === "creating"}
                    className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-fg shadow-glow hover:bg-brand-hover focus-ring disabled:opacity-50"
                  >
                    {phase === "creating" ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Creating…
                      </>
                    ) : (
                      <>
                        <Wand2 className="h-4 w-4" />
                        Create
                      </>
                    )}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
    </AppDialog>
  );

  function FilesPanel({ skillMd, handlersPy }: { skillMd: string; handlersPy: string }) {
    return (
      <div className="rounded-xl border border-border-subtle bg-bg-muted overflow-hidden">
        <div className="flex border-b border-border-subtle text-xs">
          <FileTab name="SKILL.md" active={showFiles === "skill"} onClick={() => setShowFiles(showFiles === "skill" ? "none" : "skill")} />
          <FileTab name="handlers.py" active={showFiles === "handlers"} onClick={() => setShowFiles(showFiles === "handlers" ? "none" : "handlers")} />
        </div>
        {showFiles !== "none" && (
          <pre className="max-h-72 overflow-auto p-3 text-[12px] leading-relaxed text-text-secondary font-mono">
            {showFiles === "skill" ? skillMd : handlersPy}
          </pre>
        )}
      </div>
    );
  }
}

function FileTab({ name, active, onClick }: { name: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={
        "px-3 py-2 font-mono transition-colors " +
        (active
          ? "text-brand bg-bg-elevated"
          : "text-text-muted hover:text-text-primary hover:bg-bg-elevated")
      }
    >
      {name}
    </button>
  );
}

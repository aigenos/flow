import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle, Check, FileCode2, FilePlus2, Play, Save, Search, ShieldAlert, Trash2, Wrench,
} from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../components/ui/button";
import { AppDialog, AppDialogDescription, AppDialogTitle } from "../components/AppDialog";
import { useTheme } from "../hooks/useTheme";
import type { ToolInfo } from "../builder/steps";

const Editor = lazy(() => import("@monaco-editor/react"));

type LibraryFile = { path: string; source: string; helper: boolean; tools: string[] };

const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "⌘" : "Ctrl";

/** A first file: one lookup and one write, named after the file so two files
 *  started from it never define the same tool. */
function template(stem: string): string {
  return `"""Tools for ${stem.replace(/_/g, " ")}.

Every public function in this file is a tool. A skill runs it with
\`do: its_name\`, or just reads its result by name (\`when: its_name.found\`).
A function's parameters are the answers it needs: a skill that asks for an
answer of the same name passes it in.
"""
from aigenos_flow import readonly


@readonly
def find_${stem}(reference: str) -> dict:
    """Look one up by its reference."""
    # @readonly: this only looks something up, so a flow may run it whenever
    # it needs the result, and again when the answer it used changes.
    known = {"A-100": "ready", "A-200": "on hold"}
    return {"found": reference in known, "status": known.get(reference, "unknown")}


def update_${stem}(reference: str, note: str = "") -> dict:
    """Change one. (Replace the body with the real call.)"""
    # Not @readonly, so this changes something: a skill must put a \`confirm:\`
    # step above it, it runs at most once per conversation, and it is audited.
    return {"reference": reference, "status": "updated", "note": note}
`;
}

async function detailOf(response: Response): Promise<string> {
  const text = await response.text();
  try { return String(JSON.parse(text).detail ?? text); } catch { return text; }
}

/**
 * The tool library: the Python a skill names instead of containing.
 *
 * A tool is a function in `tools/` beside the skills. Writing one here is the
 * same as saving that file: the server imports it, reloads every skill against
 * it, and refuses the save if the file does not import or a skill that loaded
 * before would stop loading.
 */
export function ToolsPage() {
  const { theme } = useTheme();
  const [files, setFiles] = useState<LibraryFile[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [path, setPath] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [toolName, setToolName] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [denied, setDenied] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedFlash, setSavedFlash] = useState(false);
  const [showNew, setShowNew] = useState(false);

  const refreshTools = useCallback(async () => {
    try {
      const r = await fetch("/api/flow/tools");
      const j = r.ok ? await r.json() : { tools: [] };
      setTools(Array.isArray(j.tools) ? j.tools : []);
    } catch {
      setTools([]);
    }
  }, []);

  useEffect(() => {
    let live = true;
    (async () => {
      await refreshTools();
      try {
        const r = await fetch("/api/flow/tools/files");
        if (!live) return;
        if (r.status === 403) { setDenied(await detailOf(r)); setLoaded(true); return; }
        const j = r.ok ? await r.json() : { files: [] };
        const listed: LibraryFile[] = Array.isArray(j.files) ? j.files : [];
        setFiles(listed);
        const first = listed.find((f) => !f.helper) ?? listed[0];
        if (first) { setPath(first.path); setDraft(first.source); setToolName(first.tools[0] ?? null); }
      } catch {
        /* the list stays empty; the page says so */
      }
      if (live) setLoaded(true);
    })();
    return () => { live = false; };
  }, [refreshTools]);

  const current = files.find((f) => f.path === path) ?? null;
  const dirty = current != null && draft !== current.source;
  const byName = useMemo(() => new Map(tools.map((t) => [t.name, t])), [tools]);
  const tool = toolName ? byName.get(toolName) ?? null : null;
  const inFiles = new Set(files.flatMap((f) => f.tools));
  const elsewhere = tools.filter((t) => !inFiles.has(t.name) && t.source !== "built in");
  const builtIn = tools.filter((t) => t.source === "built in");

  const open = (next: LibraryFile, pick?: string) => {
    if (next.path !== path && dirty && !window.confirm("This file has unsaved changes. Leave it without saving?")) return;
    if (next.path !== path) {
      // A file that was started and never saved does not exist: drop it.
      if (current && current.source === "") setFiles((fs) => fs.filter((f) => f.path !== current.path));
      setPath(next.path);
      setDraft(next.source);
      setError(null);
    }
    setToolName(pick ?? next.tools[0] ?? null);
  };

  const save = useCallback(async (): Promise<boolean> => {
    if (!path) return false;
    setSaving(true);
    setError(null);
    try {
      const r = await fetch("/api/flow/tools/files", {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ path, source: draft }),
      });
      if (!r.ok) throw new Error((await detailOf(r)).replace(/^saving the tool file rejected: /, ""));
      const j = await r.json();
      const listed: LibraryFile[] = j.files ?? [];
      await refreshTools();
      // The file list comes back without tool names; ask for them again.
      const again = await fetch("/api/flow/tools/files");
      const fresh: LibraryFile[] = again.ok ? (await again.json()).files ?? listed : listed;
      setFiles(fresh);
      const mine = fresh.find((f) => f.path === path);
      if (mine && (!toolName || !mine.tools.includes(toolName))) setToolName(mine.tools[0] ?? null);
      setSavedFlash(true);
      window.setTimeout(() => setSavedFlash(false), 2500);
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setSaving(false);
    }
  }, [path, draft, refreshTools, toolName]);

  const remove = async () => {
    if (!current) return;
    if (!window.confirm(`Remove ${current.path}? Its tools (${current.tools.join(", ") || "none"}) will no longer exist.`)) return;
    setError(null);
    const r = await fetch(`/api/flow/tools/files?path=${encodeURIComponent(current.path)}`, { method: "DELETE" });
    if (!r.ok) { setError((await detailOf(r)).replace(/^removing the tool file rejected: /, "")); return; }
    await refreshTools();
    const again = await fetch("/api/flow/tools/files");
    const fresh: LibraryFile[] = again.ok ? (await again.json()).files ?? [] : [];
    setFiles(fresh);
    const first = fresh[0] ?? null;
    setPath(first?.path ?? null);
    setDraft(first?.source ?? "");
    setToolName(first?.tools[0] ?? null);
  };

  const create = (stem: string) => {
    const next: LibraryFile = { path: `tools/${stem}.py`, source: "", helper: stem.startsWith("_"), tools: [] };
    setFiles((fs) => [...fs, next]);
    setPath(next.path);
    setDraft(template(stem.replace(/^_+/, "") || "things"));
    setToolName(null);
    setError(null);
    setShowNew(false);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); void save(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  return (
    <div className="h-full flex flex-col">
      <div className="glass border-b border-border-subtle px-6 py-3 flex items-center gap-3 sticky top-0 z-20">
        <span className="text-sm font-semibold tracking-tight shrink-0">
          Tool <span className="grad-text">library</span>
        </span>
        <span className="text-xs text-text-muted flex-1 truncate">
          Python functions a skill names in a Do step. One function serves every skill, and says for itself whether it changes anything.
        </span>
        {dirty && (
          <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" /> unsaved
          </span>
        )}
        {savedFlash && (
          <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400 animate-fade-in">
            <Check className="h-3 w-3" /> saved, and every skill reloaded against it
          </span>
        )}
        <Button variant="outline" size="sm" onClick={() => setShowNew(true)} disabled={!!denied}>
          <FilePlus2 /> New file
        </Button>
        <Button size="sm" onClick={save} disabled={saving || !current || !!denied} title={`Save (${MOD}S)`}>
          <Save /> {saving ? "Checking…" : "Save"}
        </Button>
      </div>

      {denied && (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/[0.08] px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <ShieldAlert className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span className="leading-relaxed">The tool library's source cannot be read or written from here. {denied}</span>
        </div>
      )}
      {error && (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/[0.08] px-3 py-2 text-xs text-rose-700 dark:text-rose-300">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <pre className="whitespace-pre-wrap font-mono leading-relaxed">{error}</pre>
        </div>
      )}

      <div className="flex-1 min-h-0 flex gap-3 p-4">
        {/* files and their tools */}
        <aside className="w-[250px] shrink-0 glass-card overflow-y-auto scrollbar-thin p-2">
          {files.map((file) => (
            <div key={file.path} className="mb-2">
              <button
                onClick={() => open(file)}
                className={cn(
                  "flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-[12px] font-medium transition-colors",
                  file.path === path ? "bg-bg-muted text-text-primary" : "text-text-secondary hover:bg-bg-muted/60 hover:text-text-primary",
                )}
              >
                <FileCode2 className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate font-mono">{file.path}</span>
                {file.helper && <span className="ml-auto text-[10px] text-text-muted">helpers</span>}
              </button>
              {file.tools.map((name) => (
                <ToolRow key={name} tool={byName.get(name)} name={name} active={toolName === name}
                         onClick={() => open(file, name)} />
              ))}
              {file.tools.length === 0 && !file.helper && file.source !== "" && (
                <p className="px-7 py-1 text-[11px] text-text-muted">No public functions yet.</p>
              )}
              {file.source === "" && (
                <p className="px-7 py-1 text-[11px] text-amber-600 dark:text-amber-400">Not saved yet.</p>
              )}
            </div>
          ))}
          {elsewhere.length > 0 && (
            <Group title="From the host or a connector">
              {elsewhere.map((t) => (
                <ToolRow key={t.name} tool={t} name={t.name} active={toolName === t.name} onClick={() => setToolName(t.name)} />
              ))}
            </Group>
          )}
          {builtIn.length > 0 && (
            <Group title="Built in">
              {builtIn.map((t) => (
                <ToolRow key={t.name} tool={t} name={t.name} active={toolName === t.name} onClick={() => setToolName(t.name)} />
              ))}
            </Group>
          )}
        </aside>

        {/* the file */}
        <div className="flex-1 min-w-0 glass-card overflow-hidden flex flex-col">
          {current ? (
            <>
              <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-2">
                <FileCode2 className="h-3.5 w-3.5 text-text-muted" />
                <span className="font-mono text-[12px] text-text-secondary">{current.path}</span>
                <span className="ml-auto text-[11px] text-text-muted">
                  Mark a function <code className="font-mono text-brand">@readonly</code> if it only looks something up.
                </span>
                <button onClick={remove} title="Remove this file"
                        className="rounded-md p-1.5 text-text-secondary hover:text-rose-400 hover:bg-rose-500/10 transition-colors">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="flex-1 min-h-0">
                <Suspense fallback={<div className="h-full bg-bg-muted animate-pulse" />}>
                  <Editor
                    key={current.path}
                    defaultValue={draft}
                    language="python"
                    theme={theme === "dark" ? "vs-dark" : "light"}
                    onChange={(value) => setDraft(value ?? "")}
                    options={{
                      fontSize: 13, minimap: { enabled: false }, lineNumbers: "on", wordWrap: "on",
                      scrollBeyondLastLine: false, automaticLayout: true, padding: { top: 12, bottom: 12 },
                      tabSize: 4,
                    }}
                  />
                </Suspense>
              </div>
            </>
          ) : (
            <EmptyLibrary loaded={loaded} denied={!!denied} onNew={() => setShowNew(true)} />
          )}
        </div>

        {/* the tool */}
        <aside className="w-[340px] shrink-0 glass-card overflow-y-auto scrollbar-thin">
          {tool ? <ToolCard key={tool.name} tool={tool} unsaved={dirty} /> : (
            <div className="p-4 text-xs leading-relaxed text-text-muted">
              Select a tool to see what it takes, which skills use it, and to try it.
            </div>
          )}
        </aside>
      </div>

      <NewFileDialog open={showNew} taken={files.map((f) => f.path)} onClose={() => setShowNew(false)} onCreate={create} />
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="px-2 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">{title}</div>
      {children}
    </div>
  );
}

function ToolRow({ tool, name, active, onClick }: { tool?: ToolInfo; name: string; active: boolean; onClick: () => void }) {
  const writes = tool?.changes_something ?? true;
  return (
    <button
      onClick={onClick}
      title={tool?.description || name}
      className={cn(
        "flex w-full items-center gap-1.5 rounded-md py-1 pl-7 pr-2 text-left text-[12px] transition-colors",
        active ? "bg-brand-subtle text-brand" : "text-text-secondary hover:bg-bg-muted/60 hover:text-text-primary",
      )}
    >
      {writes ? <Wrench className="h-3 w-3 shrink-0 text-amber-500" /> : <Search className="h-3 w-3 shrink-0 text-sky-500" />}
      <span className="truncate font-mono">{name}</span>
      {(tool?.used_by?.length ?? 0) > 0 && (
        <span className="ml-auto shrink-0 rounded-full bg-bg-muted px-1.5 text-[10px] text-text-muted tabular-nums">
          {tool!.used_by!.length}
        </span>
      )}
    </button>
  );
}

function EmptyLibrary({ loaded, denied, onNew }: { loaded: boolean; denied: boolean; onNew: () => void }) {
  if (!loaded) return <div className="h-full bg-bg-muted/40 animate-pulse" />;
  return (
    <div className="h-full grid place-items-center p-8">
      <div className="max-w-md text-center animate-fade-up">
        <div className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-2xl border border-brand/30 bg-brand-subtle">
          <Wrench className="h-5 w-5 text-brand" />
        </div>
        <h2 className="mt-4 text-xl font-bold tracking-tight">A tool is a <span className="grad-text">Python function</span></h2>
        <p className="mt-2 text-sm leading-relaxed text-text-secondary">
          Put it in a file here and every skill can name it in a Do step. Its parameters are the answers it
          needs, so nothing is declared twice. Mark it <code className="font-mono">@readonly</code> if it only looks
          something up; any other tool needs a Confirm step above it and runs at most once.
        </p>
        {!denied && (
          <Button className="mt-5" onClick={onNew}><FilePlus2 /> Write the first tool file</Button>
        )}
        <p className="mt-4 text-xs text-text-muted">
          A flow needs no tool to be complete: <code className="font-mono">save_request</code> is built in and keeps a record of each confirmed request.
        </p>
      </div>
    </div>
  );
}

/** What one tool takes, who uses it, and a way to call it. */
function ToolCard({ tool, unsaved }: { tool: ToolInfo; unsaved: boolean }) {
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [allow, setAllow] = useState(false);
  const [running, setRunning] = useState(false);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const builtIn = tool.source === "built in";

  const run = async () => {
    setRunning(true);
    setOutcome(null);
    try {
      const r = await fetch(`/api/flow/tools/${encodeURIComponent(tool.name)}/run`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ inputs, allow_write: allow }),
      });
      if (!r.ok) { setOutcome({ ok: false, text: await detailOf(r) }); return; }
      const j = await r.json();
      setOutcome(j.ok
        ? { ok: true, text: JSON.stringify(j.result, null, 2) }
        : { ok: false, text: String(j.error) });
    } catch (e) {
      setOutcome({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div>
      <div className="px-4 pt-4 pb-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <span className={cn(
            "inline-flex h-7 w-7 items-center justify-center rounded-md border",
            tool.changes_something
              ? "bg-amber-500/10 text-amber-700 border-amber-600/25 dark:text-amber-400"
              : "bg-sky-500/10 text-sky-700 border-sky-600/25 dark:text-sky-400",
          )}>
            {tool.changes_something ? <Wrench className="h-4 w-4" /> : <Search className="h-4 w-4" />}
          </span>
          <span className="font-mono text-[13px] font-semibold text-text-primary">{tool.name}</span>
        </div>
        {tool.description && <p className="mt-2 text-[12px] leading-relaxed text-text-secondary">{tool.description}</p>}
        <p className={cn(
          "mt-2 rounded-md border px-2.5 py-2 text-[11px] leading-relaxed",
          tool.changes_something
            ? "border-amber-500/30 bg-amber-500/[0.07] text-amber-700 dark:text-amber-400"
            : "border-border-subtle bg-bg-muted/50 text-text-muted",
        )}>
          {tool.changes_something
            ? "Changes something (it is not marked @readonly). No skill can run it without a Confirm step above it, it runs at most once per conversation, and each run is on the audit trail."
            : "Only looks something up (@readonly). A flow may run it whenever a step needs its result."}
        </p>
      </div>

      <div className="p-4 space-y-4">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">Used by</div>
          {(tool.used_by?.length ?? 0) > 0 ? (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {tool.used_by!.map((skill) => (
                <Link key={skill} to={`/canvas/${encodeURIComponent(skill)}`}
                      className="rounded-full border border-border bg-bg-surface px-2.5 py-1 text-[11px] text-text-secondary hover:border-brand/50 hover:text-text-primary">
                  {skill}
                </Link>
              ))}
            </div>
          ) : (
            <p className="mt-1 text-[11px] text-text-muted">
              No skill yet. On the Canvas, add a Do step and choose <span className="font-mono">{tool.name}</span>.
            </p>
          )}
        </div>

        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
            {tool.takes_everything ? "What it takes" : "Try it"}
          </div>
          {builtIn ? (
            <p className="mt-1 text-[11px] leading-relaxed text-text-muted">
              Built in. It receives every answer asked above it (never a secret, never the yes), writes one line to
              <span className="font-mono"> records.jsonl</span> next to the skill, and returns a reference to quote:
              <span className="font-mono"> {"{{save_request.reference}}"}</span>. A tool of your own with this name replaces it.
            </p>
          ) : (
            <div className="mt-1.5 space-y-2">
              {tool.takes_everything && (
                <p className="text-[11px] leading-relaxed text-text-muted">
                  Written as one-dict-in: it receives every answer asked above it.
                </p>
              )}
              {tool.inputs.map((input) => (
                <label key={input.name} className="block">
                  <span className="flex items-baseline gap-1.5 text-[11px] text-text-muted">
                    <span className="font-mono text-text-secondary">{input.name}</span>
                    {input.type === "choice" ? "one of a list" : input.type}
                    {input.secret ? ", secret" : ""}{input.required ? "" : ", optional"}
                  </span>
                  {input.choices ? (
                    <select value={inputs[input.name] ?? ""} onChange={(e) => setInputs({ ...inputs, [input.name]: e.target.value })}
                            className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs">
                      <option value="">—</option>
                      {input.choices.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  ) : (
                    <input value={inputs[input.name] ?? ""} placeholder={input.description || undefined}
                           type={input.secret ? "password" : "text"}
                           onChange={(e) => setInputs({ ...inputs, [input.name]: e.target.value })}
                           className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand" />
                  )}
                </label>
              ))}
              {tool.changes_something && (
                <label className="flex items-start gap-2 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
                  <input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)}
                         className="mt-0.5 h-3.5 w-3.5 rounded border-border accent-brand" />
                  This is the real function and it changes something. Run it anyway.
                </label>
              )}
              <Button size="sm" variant="outline" onClick={run} disabled={running || (tool.changes_something && !allow)}>
                <Play /> {running ? "Running…" : "Run"}
              </Button>
              {unsaved && (
                <p className="text-[11px] text-text-muted">This runs the saved file. Save first to try your edits.</p>
              )}
              {outcome && (
                <pre className={cn(
                  "max-h-64 overflow-auto rounded-md border px-2.5 py-2 text-[11px] leading-relaxed font-mono whitespace-pre-wrap",
                  outcome.ok
                    ? "border-emerald-500/30 bg-emerald-500/[0.06] text-text-secondary"
                    : "border-rose-500/30 bg-rose-500/[0.07] text-rose-700 dark:text-rose-300",
                )}>{outcome.text}</pre>
              )}
            </div>
          )}
        </div>

        {!builtIn && (
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">In a skill</div>
            <pre className="mt-1.5 rounded-md border border-border-subtle bg-bg-muted/60 px-2.5 py-2 text-[11px] leading-relaxed font-mono text-text-secondary whitespace-pre-wrap">
{tool.changes_something
  ? `  - confirm: "Shall I go ahead?"\n  - do: ${tool.name}`
  : `  - say: "{{${tool.name}}}"\n    when: ${tool.name}.found`}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function NewFileDialog({ open, taken, onClose, onCreate }: {
  open: boolean; taken: string[]; onClose: () => void; onCreate: (stem: string) => void;
}) {
  const [name, setName] = useState("");
  useEffect(() => { if (open) setName(""); }, [open]);
  const stem = name.trim().toLowerCase().replace(/\.py$/, "").replace(/[^a-z0-9_]+/g, "_").replace(/^([0-9])/, "_$1");
  const clash = taken.includes(`tools/${stem}.py`);
  return (
    <AppDialog open={open} onOpenChange={(next) => { if (!next) onClose(); }} className="max-w-md">
      <div className="px-6 py-4 border-b border-border-subtle">
        <AppDialogTitle className="text-sm font-semibold text-text-primary">New tool file</AppDialogTitle>
        <AppDialogDescription className="mt-0.5 text-xs text-text-muted">
          A Python file in the <span className="font-mono">tools/</span> folder beside your skills. It starts with one
          lookup and one tool that changes something, to edit or replace.
        </AppDialogDescription>
      </div>
      <div className="p-6 space-y-3">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-text-muted">File name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="orders" autoFocus
                 onKeyDown={(e) => { if (e.key === "Enter" && stem && !clash) onCreate(stem); }}
                 className="mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand" />
          <span className="mt-1 block text-[11px] text-text-muted">
            {stem ? <>Saved as <code className="font-mono">tools/{stem}.py</code>{clash ? " — that file already exists." : ". Nothing is written until you press Save."}</> : "What these tools are about: orders, bank, calendar."}
          </span>
        </label>
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button size="sm" onClick={() => onCreate(stem)} disabled={!stem || clash}>Create</Button>
        </div>
      </div>
    </AppDialog>
  );
}

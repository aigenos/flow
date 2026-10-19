import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Workflow as WorkflowIcon, Plus, Trash2, Pencil } from "lucide-react";
import { api, WorkflowListItem } from "../lib/api";

export function BuilderPage() {
  const [workflows, setWorkflows] = useState<WorkflowListItem[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    setLoading(true);
    api.listWorkflows().then(setWorkflows).finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  const remove = async (id: string) => {
    if (!confirm(`Delete workflow "${id}"?`)) return;
    await api.deleteWorkflow(id);
    refresh();
  };

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-xl font-semibold text-text-primary flex items-center gap-2">
              <WorkflowIcon className="h-5 w-5 text-brand" /> Widget flows
              <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                legacy
              </span>
            </h1>
            <p className="text-sm text-text-secondary mt-1">
              The previous-generation engine: server-side widget-DAG graphs whose nodes reference
              widgets by id. They execute in the{" "}
              <Link to="/builder/run" className="text-brand hover:text-brand-hover">legacy runner</Link>.
              New deterministic workflows belong on the{" "}
              <Link to="/canvas" className="text-brand hover:text-brand-hover">Canvas</Link>.
            </p>
          </div>
          <Link
            to="/builder/new"
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-3 py-1.5 text-sm font-medium shadow-glow hover:bg-brand-hover transition-colors"
          >
            <Plus className="h-4 w-4" /> New workflow
          </Link>
        </div>

        {loading && <div className="text-sm text-text-muted">Loading…</div>}

        {!loading && workflows.length === 0 && (
          <div className="rounded-2xl border border-dashed border-border bg-bg-surface/40 p-10 text-center">
            <WorkflowIcon className="h-8 w-8 text-text-muted mx-auto mb-3" />
            <p className="text-sm text-text-secondary">No workflows yet.</p>
            <Link to="/builder/new" className="inline-block mt-3 text-sm text-brand hover:text-brand-hover">
              Create your first workflow →
            </Link>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {workflows.map((w) => (
            <div
              key={w.id}
              className="group rounded-xl border border-border-subtle bg-bg-surface p-4 hover:border-border-strong transition-colors"
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <div className="font-medium text-text-primary truncate">{w.id}</div>
                  {w.description && (
                    <p className="text-xs text-text-secondary mt-1 line-clamp-2">{w.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <Link
                    to={`/builder/${w.id}`}
                    className="rounded p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-muted"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Link>
                  <button
                    onClick={() => remove(w.id)}
                    className="rounded p-1.5 text-text-muted hover:text-rose-400 hover:bg-bg-muted"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

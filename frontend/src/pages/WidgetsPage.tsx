import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Boxes, Plus, Trash2, Pencil } from "lucide-react";
import { api, WidgetListItem } from "../lib/api";

export function WidgetsPage() {
  const [widgets, setWidgets] = useState<WidgetListItem[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = () => {
    setLoading(true);
    api.listWidgets().then(setWidgets).finally(() => setLoading(false));
  };

  useEffect(() => { refresh(); }, []);

  const remove = async (id: string) => {
    if (!confirm(`Delete widget "${id}"?`)) return;
    await api.deleteWidget(id);
    refresh();
  };

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="mx-auto max-w-5xl px-6 py-10">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-xl font-semibold text-text-primary flex items-center gap-2">
              <Boxes className="h-5 w-5 text-brand" /> Widgets
            </h1>
            <p className="text-sm text-text-secondary mt-1">
              Design once, reference anywhere: a <Link to="/canvas" className="text-brand hover:text-brand-hover">Canvas</Link> step
              renders one with <code className="text-brand/90">widget: &lt;id&gt;</code>; steps without
              one get an auto-widget.
            </p>
          </div>
          <Link
            to="/widgets/new"
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand text-brand-fg px-3 py-1.5 text-sm font-medium shadow-glow hover:bg-brand-hover transition-colors"
          >
            <Plus className="h-4 w-4" /> New widget
          </Link>
        </div>

        {loading && <div className="text-sm text-text-muted">Loading…</div>}

        {!loading && widgets.length === 0 && (
          <div className="rounded-2xl border border-dashed border-border bg-bg-surface/40 p-10 text-center">
            <Boxes className="h-8 w-8 text-text-muted mx-auto mb-3" />
            <p className="text-sm text-text-secondary">No widgets yet.</p>
            <Link to="/widgets/new" className="inline-block mt-3 text-sm text-brand hover:text-brand-hover">
              Create your first widget →
            </Link>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {widgets.map((w) => (
            <div
              key={w.id}
              className="relative rounded-xl border border-border-subtle bg-bg-surface p-4 hover:border-border-strong transition-colors"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  {/* The title link's ::after covers the card, so the whole card opens the editor. */}
                  <Link
                    to={`/widgets/${w.id}`}
                    className="block rounded font-medium text-text-primary focus-ring after:absolute after:inset-0 after:rounded-xl"
                  >
                    <span className="block truncate">{w.id}</span>
                  </Link>
                  <div className="text-xs text-text-muted">v{w.version}</div>
                </div>
                <div className="relative z-10 flex shrink-0 items-center gap-1">
                  <Link
                    to={`/widgets/${w.id}`}
                    aria-label={`Edit ${w.id}`}
                    className="rounded p-1.5 text-text-muted hover:text-text-primary hover:bg-bg-muted"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Link>
                  <button
                    onClick={() => remove(w.id)}
                    aria-label={`Delete ${w.id}`}
                    className="rounded p-1.5 text-text-muted hover:text-rose-700 dark:hover:text-rose-300 hover:bg-bg-muted"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              {w.description && (
                <p className="mt-2 text-xs text-text-secondary line-clamp-2">{w.description}</p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

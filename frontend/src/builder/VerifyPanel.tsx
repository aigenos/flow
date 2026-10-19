import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle, Check, ChevronRight, Loader2, Minus, Play, Plus, ShieldCheck, Trash2,
} from "lucide-react";
import {
  api,
  type Guarantee,
  type GuaranteeReport,
  type Scenario,
  type ScenarioResult,
  type ScenarioRun,
  type TraceEntry,
} from "../lib/api";
import { cn } from "../lib/cn";
import { Button } from "../components/ui/button";
import { ICONS, THEME, type WfNodeData } from "./nodes";

/**
 * "Is this flow sound?" — the two answers that exist, side by side.
 *
 * Guarantees are what the fail-closed loader proved on the last save; they
 * cost nothing to show because the check already ran. Scenarios are replays
 * against a TestHost — no model, so they are fast, offline, and the trace they
 * produce is the evidence that the runtime (not the LLM) chose the path.
 */

const OUTCOME_STYLE: Record<TraceEntry["outcome"], string> = {
  ran: "text-text-secondary",
  passed: "text-emerald-600 dark:text-emerald-400",
  failed: "text-rose-600 dark:text-rose-400",
  paused: "text-amber-600 dark:text-amber-400",
  skipped: "text-text-muted",
};

/** One replayed step, styled to match its node on the canvas. */
function TraceRow({ entry }: { entry: TraceEntry }) {
  const verb = entry.verb as WfNodeData["nodeType"];
  const Icon = ICONS[verb];
  const theme = THEME[verb] ?? THEME.say;
  const dimmed = entry.outcome === "skipped";

  return (
    <li className={cn("flex items-start gap-2 py-1", dimmed && "opacity-60")}>
      <span className="w-4 shrink-0 pt-0.5 text-right font-mono text-[11px] tabular-nums text-text-muted">
        {entry.step}
      </span>
      <span
        className={cn(
          "mt-px inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border",
          theme.chip,
        )}
      >
        {Icon && <Icon className="h-2.5 w-2.5" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className={cn("font-mono text-[11px] font-semibold uppercase", theme.text)}>
          {entry.verb}
        </span>
        {entry.detail && (
          <span className="ml-1.5 break-words font-mono text-[11px] text-text-muted">
            {entry.detail}
          </span>
        )}
      </span>
      <span className={cn("shrink-0 text-[11px]", OUTCOME_STYLE[entry.outcome])}>
        {entry.outcome}
        {entry.guard && <span className="text-text-muted"> · {entry.guard}</span>}
      </span>
    </li>
  );
}

function GuaranteeRow({ item }: { item: Guarantee }) {
  const holds = item.status === "holds";
  return (
    <li className="flex items-start gap-2 py-1.5">
      <span
        className={cn(
          "mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
          holds
            ? "border-emerald-600/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            : "border-border-subtle bg-bg-muted text-text-muted",
        )}
      >
        {holds ? <Check className="h-2.5 w-2.5" /> : <Minus className="h-2.5 w-2.5" />}
      </span>
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            "block text-[12px] font-medium",
            holds ? "text-text-primary" : "text-text-muted",
          )}
        >
          {item.title}
          {item.kind === "runtime" && (
            <span className="ml-1.5 rounded border border-border-subtle px-1 py-px text-[10px] font-normal text-text-muted">
              runtime
            </span>
          )}
        </span>
        <span className="mt-0.5 block text-[11px] leading-relaxed text-text-muted">
          {item.detail}
        </span>
      </span>
    </li>
  );
}

function ResultRow({ result }: { result: ScenarioResult }) {
  const [open, setOpen] = useState(result.status !== "passed");
  const tone =
    result.status === "passed"
      ? "border-emerald-600/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
      : "border-rose-600/30 bg-rose-500/10 text-rose-700 dark:text-rose-400";

  return (
    <li className="rounded-lg border border-border-subtle">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 shrink-0 text-text-muted transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="min-w-0 flex-1 truncate text-[12px] font-medium text-text-primary">
          {result.name}
        </span>
        <span className={cn("shrink-0 rounded-full border px-1.5 py-px text-[10px] font-medium", tone)}>
          {result.status}
        </span>
      </button>

      {open && (
        <div className="border-t border-border-subtle px-2.5 py-2">
          {result.failures.length > 0 && (
            <ul className="mb-2 space-y-1">
              {result.failures.map((f, i) => (
                <li
                  key={i}
                  className="flex items-start gap-1.5 text-[11px] leading-relaxed text-rose-600 dark:text-rose-400"
                >
                  <AlertCircle className="mt-[2px] h-2.5 w-2.5 shrink-0" />
                  <span className="break-words">{f}</span>
                </li>
              ))}
            </ul>
          )}

          {result.trace.length > 0 ? (
            <>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-text-muted">
                  Trace
                </span>
                {/* The determinism check: both replays took the same path. */}
                <span
                  className={cn(
                    "text-[10px]",
                    result.deterministic
                      ? "text-emerald-600 dark:text-emerald-400"
                      : "text-rose-600 dark:text-rose-400",
                  )}
                >
                  {result.deterministic ? "identical on replay" : "diverged on replay"}
                </span>
              </div>
              <ul className="rounded-md bg-bg-muted/40 px-1.5 py-1">
                {result.trace.map((e, i) => (
                  <TraceRow key={i} entry={e} />
                ))}
              </ul>
            </>
          ) : (
            <p className="text-[11px] text-text-muted">No steps ran.</p>
          )}
        </div>
      )}
    </li>
  );
}

/** Compact editor for one scenario: what the user said, what it set, what to expect. */
function ScenarioEditor({
  scenario,
  fields,
  onChange,
  onDelete,
}: {
  scenario: Scenario;
  fields: string[];
  onChange: (next: Scenario) => void;
  onDelete: () => void;
}) {
  const turn = scenario.turns[0] ?? { user: "", emits: [] };
  const patchTurn = (next: Partial<typeof turn>) =>
    onChange({ ...scenario, turns: [{ ...turn, ...next }] });

  const setEmit = (i: number, field: string, value: string) => {
    const emits = [...turn.emits];
    emits[i] = { type: "SetField", field, value };
    patchTurn({ emits });
  };

  return (
    <div className="space-y-2 rounded-lg border border-border-subtle bg-bg-muted/30 p-2.5">
      <div className="flex items-center gap-1.5">
        <input
          value={scenario.name ?? ""}
          onChange={(e) => onChange({ ...scenario, name: e.target.value })}
          placeholder="What this scenario proves"
          className="field flex-1"
        />
        <button
          onClick={onDelete}
          title="Delete scenario"
          className="rounded-md p-1 text-text-muted transition-colors hover:bg-rose-500/10 hover:text-rose-500"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>

      <label className="block">
        <span className="text-[11px] uppercase tracking-wider text-text-muted">user says</span>
        <textarea
          value={turn.user}
          rows={2}
          onChange={(e) => patchTurn({ user: e.target.value })}
          placeholder="book redwood tomorrow at 3pm"
          className="field mt-0.5 resize-y"
        />
      </label>

      <div>
        <span className="text-[11px] uppercase tracking-wider text-text-muted">
          interpreted as
        </span>
        <p className="mb-1 mt-0.5 text-[10px] leading-relaxed text-text-muted">
          The fields the model would extract. Pinning them here is what removes it from the replay.
        </p>
        <div className="space-y-1">
          {turn.emits.map((emit, i) => (
            <div key={i} className="flex items-center gap-1">
              <select
                value={String(emit.field ?? "")}
                onChange={(e) => setEmit(i, e.target.value, String(emit.value ?? ""))}
                className="field w-[45%]"
              >
                <option value="">— field —</option>
                {fields.map((f) => (
                  <option key={f} value={f}>{f}</option>
                ))}
              </select>
              <input
                value={String(emit.value ?? "")}
                onChange={(e) => setEmit(i, String(emit.field ?? ""), e.target.value)}
                placeholder="value"
                className="field flex-1"
              />
              <button
                onClick={() => patchTurn({ emits: turn.emits.filter((_, j) => j !== i) })}
                className="rounded p-1 text-text-muted transition-colors hover:text-rose-500"
                title="Remove"
              >
                <Minus className="h-3 w-3" />
              </button>
            </div>
          ))}
          <button
            onClick={() =>
              patchTurn({ emits: [...turn.emits, { type: "SetField", field: fields[0] ?? "", value: "" }] })
            }
            className="flex w-full items-center justify-center gap-1 rounded-md border border-dashed border-border py-1 text-[11px] text-text-secondary transition-colors hover:border-brand/50 hover:text-brand"
          >
            <Plus className="h-3 w-3" /> field
          </button>
        </div>
      </div>

      <label className="block">
        <span className="text-[11px] uppercase tracking-wider text-text-muted">
          then it should be waiting on
        </span>
        <select
          value={scenario.expect?.awaiting_field ?? ""}
          onChange={(e) =>
            onChange({
              ...scenario,
              expect: {
                ...scenario.expect,
                awaiting_field: e.target.value || undefined,
              },
            })
          }
          className="field mt-0.5"
        >
          <option value="">— don&rsquo;t check —</option>
          {fields.map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-[12px] text-text-secondary">
        <input
          type="checkbox"
          checked={scenario.expect?.done === true}
          onChange={(e) =>
            onChange({
              ...scenario,
              expect: { ...scenario.expect, done: e.target.checked || undefined },
            })
          }
          className="accent-[rgb(var(--brand))]"
        />
        the flow should have finished
      </label>
    </div>
  );
}

export function VerifyPanel({ skill, fields }: { skill: string; fields: string[] }) {
  const [report, setReport] = useState<GuaranteeReport | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [run, setRun] = useState<ScenarioRun | null>(null);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.guarantees(skill).then(setReport).catch(() => setReport(null));
    api.listScenarios(skill).then(setScenarios).catch(() => setScenarios([]));
    setRun(null);
  }, [skill]);

  useEffect(load, [load]);

  const runAll = async () => {
    setRunning(true);
    setError(null);
    try {
      setRun(await api.runScenarios(skill));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const persist = async (next: Scenario[]) => {
    setScenarios(next);
    setSaving(true);
    setError(null);
    try {
      await api.saveScenarios(skill, next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const addScenario = () =>
    persist([
      ...(scenarios ?? []),
      {
        id: `scenario-${(scenarios?.length ?? 0) + 1}`,
        name: "",
        turns: [{ user: "", emits: [] }],
        expect: {},
      },
    ]).then(() => setEditing(true));

  return (
    <div className="space-y-4 p-3.5">
      {/* ---------- guarantees ---------- */}
      <section>
        <div className="flex items-center justify-between">
          <h3 className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
            <ShieldCheck className="h-3 w-3" /> Guarantees
          </h3>
          {report && (
            <span className="font-mono text-[11px] tabular-nums text-text-muted">
              {report.holds}/{report.checked}
            </span>
          )}
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-text-muted">
          Proven by the fail-closed loader when this skill last saved. A dash means the
          property has nothing to bite on yet — not that it failed.
        </p>
        {report ? (
          <ul className="mt-2">
            {report.properties.map((p) => (
              <GuaranteeRow key={p.id} item={p} />
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-[11px] text-text-muted">No report available.</p>
        )}
      </section>

      {/* ---------- scenarios ---------- */}
      <section className="border-t border-border-subtle pt-3.5">
        <div className="flex items-center justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
            Scenarios
          </h3>
          <div className="flex items-center gap-1">
            {scenarios !== null && scenarios.length > 0 && (
              <button
                onClick={() => setEditing((v) => !v)}
                className="rounded-md px-1.5 py-0.5 text-[11px] text-text-muted transition-colors hover:text-text-primary"
              >
                {editing ? "done" : "edit"}
              </button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={runAll}
              disabled={running || !scenarios?.length}
              title="Replay every scenario — no model runs"
            >
              {running ? <Loader2 className="animate-spin" /> : <Play />} Run
            </Button>
          </div>
        </div>
        <p className="mt-1 text-[11px] leading-relaxed text-text-muted">
          A scripted conversation replayed against the runtime with the interpretation pinned.
          No model runs, so a pass means the <em>flow</em> is what decided.
        </p>

        {run && (
          <div
            className={cn(
              "mt-2 rounded-lg border px-2.5 py-1.5 text-[12px] font-medium",
              run.failed === 0
                ? "border-emerald-600/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                : "border-rose-600/30 bg-rose-500/10 text-rose-700 dark:text-rose-400",
            )}
          >
            {run.passed}/{run.total} passing
          </div>
        )}

        {scenarios === null && (
          <p className="mt-2 text-[11px] text-text-muted">Loading…</p>
        )}

        {scenarios?.length === 0 && !editing && (
          <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
            No scenarios yet. Add one to lock in the path this flow should take.
          </p>
        )}

        {run && !editing && (
          <ul className="mt-2 space-y-1.5">
            {run.results.map((r) => (
              <ResultRow key={r.id} result={r} />
            ))}
          </ul>
        )}

        {!run && !editing && scenarios && scenarios.length > 0 && (
          <ul className="mt-2 space-y-1">
            {scenarios.map((s) => (
              <li
                key={s.id}
                className="truncate rounded-lg border border-border-subtle px-2.5 py-1.5 text-[12px] text-text-secondary"
              >
                {s.name || s.id}
              </li>
            ))}
          </ul>
        )}

        {editing && scenarios && (
          <div className="mt-2 space-y-2">
            {scenarios.map((s, i) => (
              <ScenarioEditor
                key={s.id}
                scenario={s}
                fields={fields}
                onChange={(next) =>
                  setScenarios(scenarios.map((x, j) => (j === i ? next : x)))
                }
                onDelete={() => persist(scenarios.filter((_, j) => j !== i))}
              />
            ))}
            <Button
              size="sm"
              variant="outline"
              className="w-full"
              disabled={saving}
              onClick={() => persist(scenarios)}
            >
              {saving ? <Loader2 className="animate-spin" /> : <Check />} Save scenarios
            </Button>
          </div>
        )}

        <button
          onClick={addScenario}
          className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-lg border border-dashed border-border py-1.5 text-[12px] text-text-secondary transition-colors hover:border-brand/50 hover:text-brand"
        >
          <Plus className="h-3 w-3" /> Add scenario
        </button>

        {error && <p className="mt-2 text-[11px] text-rose-500">{error}</p>}
      </section>
    </div>
  );
}

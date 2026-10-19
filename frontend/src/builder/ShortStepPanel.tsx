/**
 * The inspector for one short-form step (ask / say / do / confirm / chat), and
 * for the header of a block (a condition, or a repeat).
 *
 * Nothing here needs syntax: a tool or another skill is picked from the
 * library, an answer's kind is chosen from a list, and a tool's missing inputs
 * are one click away from being asked. What is typed is a question, a
 * sentence, or a condition such as `amount >= 1000`.
 */
import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Check, CircleDashed, CornerLeftUp, ExternalLink, Plus, Trash2, X } from "lucide-react";
import { cn } from "../lib/cn";
import { ICONS, THEME, type WfNodeData } from "./nodes";
import type { SkillStep } from "./skillflow";
import {
  asksOf, blockOf, isOptionalQuestion, isSkillTarget, paletteKindOf, repeatOf, rulesOf, toolsOf,
  withoutOptional, type Block, type FieldInfo, type PaletteKind, type ToolInfo,
} from "./steps";
import type { Issue } from "./validate";

/**
 * What the canvas's left rail offers. Each entry is one of the five kinds of
 * step with an option set, or a block, so the file stays the same small
 * language however a skill is drawn.
 */
export const PALETTE: Array<{ kind: PaletteKind; label: string; icon: WfNodeData["nodeType"]; blurb: string }> = [
  { kind: "ask", label: "Ask", icon: "ask", blurb: "Get an answer and keep it under a name. The user sees a typed input: a number box, a list to pick from, a masked box for a code." },
  { kind: "say", label: "Say", icon: "say", blurb: "Tell the user something. {{name}} fills in an answer or a tool's result; a result that is data is shown as a card." },
  { kind: "widget", label: "Widget", icon: "widget", blurb: "Show a widget you designed, with data from the flow. To collect answers with one, choose it on an Ask." },
  { kind: "tool", label: "Tool", icon: "tool", blurb: "Run a tool from the library. One that changes something needs a Confirm or an Approval above it, and runs at most once." },
  { kind: "link", label: "Link", icon: "link", blurb: "Hand the user to another skill. It asks its own questions; when it finishes, this flow carries on." },
  { kind: "confirm", label: "Confirm", icon: "confirm", blurb: "The user's yes, before anything changes. \"No\" stops the flow: nothing below it runs." },
  { kind: "approval", label: "Approval", icon: "approval", blurb: "Someone else's yes: a manager, finance. The flow waits for them; neither the user nor the model can answer for them." },
  { kind: "condition", label: "Condition", icon: "condition", blurb: "Only when…: the steps indented under it run only if a condition holds." },
  { kind: "repeat", label: "Repeat", icon: "repeat", blurb: "Ask the questions under it once per item (each traveller, each line item), collecting a list." },
  { kind: "chat", label: "Chat", icon: "chat", blurb: "The last step: answer the user's questions with lookups only. Nothing can change there." },
];

/** The kinds of step, as the palette names them (kept for older imports). */
export const SHORT_VERB_INFO = PALETTE.map(({ kind, label, blurb }) => ({ verb: kind, label, blurb }));

const ANSWER_KINDS: Array<{ value: string; label: string }> = [
  { value: "auto", label: "Work it out for me" },
  { value: "text", label: "Text" },
  { value: "number", label: "A number" },
  { value: "whole number", label: "A whole number" },
  { value: "yes/no", label: "Yes or no" },
  { value: "secret", label: "A secret (masked, never logged)" },
  { value: "choices", label: "One of a fixed list" },
  { value: "list", label: "A pick from a tool's list" },
];

const SHAPES: Array<{ label: string; pattern: string }> = [
  { label: "6 digits", pattern: "^[0-9]{6}$" },
  { label: "4 digits", pattern: "^[0-9]{4}$" },
  { label: "digits only", pattern: "^[0-9]+$" },
  { label: "an email address", pattern: "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$" },
  { label: "letters, digits and dashes", pattern: "^[A-Za-z0-9-]+$" },
];

const FIELD_KIND: Record<string, string> = {
  string: "text", number: "a number", integer: "a whole number", boolean: "yes or no", enum: "one of a list",
};

type Props = {
  step: SkillStep;
  index?: number;
  issues: Issue[];
  tools: ToolInfo[];
  /** The other skills of this agent (a `do:` may run one of them). */
  skills: string[];
  catalog: string[];
  /** Answers asked by the steps above this one. */
  above: string[];
  /** Is there a Confirm step above this one? */
  confirmed: boolean;
  /** What the loader made of this step's question, when the last check passed. */
  fields: Record<string, FieldInfo>;
  onPatch: (patch: SkillStep) => void;
  /** Put a new step directly above this one, keeping this one selected. */
  onInsertAbove: (step: SkillStep) => void;
  onOpenWidget: (id: string) => void;
  confirmLeave: () => boolean;
  /** The block this step is inside, if any. */
  block?: Block;
  /** Move this step out of its block, to just after it. */
  onLeaveBlock?: () => void;
};

export function ShortStepPanel(props: Props) {
  const { step, index, issues, block } = props;
  const kind = paletteKindOf(step, { tools: new Map(props.tools.map((t) => [t.name, t])), skills: new Set(props.skills) });
  const info = PALETTE.find((i) => i.kind === kind) ?? PALETTE[0];
  const Icon = ICONS[info.icon];
  const theme = THEME[info.icon];
  const repeat = repeatOf(block);
  return (
    <div>
      <div className="px-4 pt-4 pb-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <span className={cn("inline-flex h-7 w-7 items-center justify-center rounded-md border", theme.chip)}>
            <Icon className="h-4 w-4" />
          </span>
          <span className={cn("text-xs font-bold uppercase tracking-[0.14em]", theme.text)}>{info.label}</span>
          {index !== undefined && (
            <span className="ml-auto text-[11px] text-text-muted tabular-nums">step {index}</span>
          )}
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-text-muted">{info.blurb}</p>
        {block && (
          <div className="mt-2.5 flex items-center gap-2 rounded-md border border-border-subtle bg-bg-muted/50 px-2.5 py-1.5 text-[11px] text-text-secondary">
            <span className="flex-1">
              {repeat
                ? <>Inside a repeat: asked once per item of <span className="font-mono">{repeat[0]}</span>.</>
                : <>Inside a condition: runs only when <span className="font-mono">{block.when || "…"}</span>.</>}
            </span>
            {props.onLeaveBlock && (
              <button type="button" onClick={props.onLeaveBlock}
                      title="Move this step out of the block, to just after it"
                      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border-subtle px-1.5 py-0.5 hover:text-text-primary hover:border-border">
                <CornerLeftUp className="h-3 w-3" /> Take it out
              </button>
            )}
          </div>
        )}
      </div>

      {issues.length > 0 && (
        <div className="border-b border-border-subtle px-4 py-3 space-y-1.5">
          {issues.map((issue, i) => (
            <div key={i} className={cn(
              "flex items-start gap-1.5 text-[12px] leading-relaxed",
              issue.level === "error" ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400",
            )}>
              <AlertCircle className="mt-[3px] h-3 w-3 shrink-0" />
              <span>{issue.message}</span>
            </div>
          ))}
        </div>
      )}

      <div className="p-4 space-y-3">
        {kind === "ask" && <AskFields {...props} />}
        {(kind === "say" || kind === "widget") && <SayFields {...props} />}
        {(kind === "tool" || kind === "link") && <DoFields {...props} />}
        {(kind === "confirm" || kind === "approval") && <ConfirmFields {...props} />}
        {kind === "chat" && <ChatFields {...props} />}

        <Section title="Only sometimes">
          <Condition label="Run this step only when" value={step.when} names={props.above} tools={props.tools}
                     onChange={(x) => props.onPatch({ when: x || undefined })} />
        </Section>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- ask
function AskFields({ step, tools, catalog, above, fields, onPatch, onOpenWidget, confirmLeave }: Props) {
  const asks = asksOf(step);
  const single = asks.length <= 1;
  const [name, question] = asks[0] ?? ["", ""];
  const kind = step.from ? "list" : Array.isArray(step.choices) ? "choices" : step.type ? String(step.type) : "auto";
  const lookups = tools.filter((t) => !t.changes_something);
  const [fromTool, ...fromRest] = String(step.from ?? "").split(".");
  const fromPath = fromRest.join(".");
  const worked = fields[name];
  const textual = kind === "auto" || kind === "text" || kind === "secret";

  const setAsks = (next: Array<[string, string]>) =>
    onPatch({ ask: Object.fromEntries(next.map(([n, q]) => [n, q])) });
  const setKind = (next: string) => {
    const cleared = { type: undefined, choices: undefined, from: undefined } as SkillStep;
    if (next === "choices") onPatch({ ...cleared, choices: ["first", "second"], pattern: undefined });
    else if (next === "list") onPatch({ ...cleared, from: lookups[0] ? `${lookups[0].name}.rows` : "", pattern: undefined });
    else if (next === "auto") onPatch(cleared);
    else onPatch({ ...cleared, type: next, ...(next === "text" || next === "secret" ? {} : { pattern: undefined }) });
  };

  return (
    <>
      {asks.map(([n, q], i) => (
        <div key={i} className={cn("space-y-2", !single && "rounded-lg border border-border-subtle p-2.5")}>
          <Area label={single ? "Question" : `Question ${i + 1}`} value={q}
                onChange={(x) => setAsks(asks.map((pair, k) => (k === i ? [pair[0], x] : pair)))} />
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Text label="Keep the answer as" value={n} mono
                    onChange={(x) => setAsks(asks.map((pair, k) => (k === i ? [nameOf(x), pair[1]] : pair)))}
                    hint={i === 0 ? "A short name. Later steps and tools use it: {{name}}, or a tool input of the same name." : undefined} />
            </div>
            {!single && (
              <button type="button" title="Remove this question"
                      onClick={() => setAsks(asks.filter((_, k) => k !== i))}
                      className="mb-0.5 rounded-md p-1.5 text-text-muted hover:text-rose-500 hover:bg-rose-500/10">
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>
      ))}

      {single && (
        <>
          <Select label="The answer is" value={kind} onChange={setKind}
                  options={ANSWER_KINDS
                    .filter((k) => k.value !== "list" || lookups.length > 0 || kind === "list")
                    .map((k) => [k.value, k.label])}
                  hint={kind === "auto"
                    ? worked
                      ? `Right now it is ${FIELD_KIND[worked.type] ?? worked.type}${worked.secret ? ", and a secret" : ""}: taken from the tool that receives it, or from how the conditions use it.`
                      : "Taken from the tool that receives it (amount: float is a number) or from how a condition uses it. Text if nothing says otherwise."
                    : undefined} />
          {kind === "choices" && (
            <Text label="Choices, separated by commas" value={(step.choices ?? []).join(", ")}
                  onChange={(x) => onPatch({ choices: x.split(",").map((c) => c.trim()).filter(Boolean) })} />
          )}
          {kind === "list" && (
            <div className="grid grid-cols-2 gap-2">
              <Select label="List from the tool" value={fromTool}
                      options={[...new Set([fromTool, ...lookups.map((t) => t.name)])].filter(Boolean).map((t) => [t, t])}
                      onChange={(x) => onPatch({ from: fromPath ? `${x}.${fromPath}` : x })} />
              <Text label="The rows are under" value={fromPath} mono ph="rows"
                    onChange={(x) => onPatch({ from: x ? `${fromTool}.${x}` : fromTool })} />
              <p className="col-span-2 -mt-1 text-[11px] leading-relaxed text-text-muted">
                The tool runs, its rows are shown as a list, and the user's pick is the answer.
                Only a tool that looks something up can fill a list.
              </p>
            </div>
          )}
          {textual && (
            <>
              <Select label="Its shape" value={step.pattern ? (SHAPES.find((s) => s.pattern === step.pattern)?.pattern ?? "custom") : ""}
                      options={[["", "Anything"], ...SHAPES.map((s) => [s.pattern, s.label] as [string, string]), ["custom", "My own pattern…"]]}
                      onChange={(x) => onPatch({ pattern: x === "custom" ? (step.pattern || "^.+$") : x || undefined })} />
              {step.pattern && !SHAPES.some((s) => s.pattern === step.pattern) && (
                <Text label="Pattern (a regular expression the whole answer must match)" value={step.pattern} mono
                      onChange={(x) => onPatch({ pattern: x || undefined })} />
              )}
            </>
          )}
          {(kind === "auto" || kind === "text") && !step.pattern && (
            <label className="flex items-center gap-2 text-[12px] text-text-secondary">
              <input type="checkbox" checked={isOptionalQuestion(question)}
                     onChange={(e) => setAsks([[name, e.target.checked ? `${withoutOptional(question)} (optional)` : withoutOptional(question)]])}
                     className="h-3.5 w-3.5 rounded border-border accent-brand" />
              May be left blank (the question says “(optional)”)
            </label>
          )}
          {worked?.user_only && (
            <p className="flex items-start gap-1.5 rounded-md border border-emerald-500/25 bg-emerald-500/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-emerald-700 dark:text-emerald-400">
              <Check className="mt-0.5 h-3 w-3 shrink-0" />
              Only the user can give this answer. It decides something (a rule or a condition reads it), so a value the user did not type or pick is dropped.
            </p>
          )}
        </>
      )}

      <Section title="What the answer must satisfy">
        <Rules step={step} names={[...above, ...asks.map(([n]) => n)]} tools={tools} onPatch={onPatch} />
      </Section>

      <Section title="How it looks">
        <label className="block">
          <span className="text-[11px] uppercase tracking-wider text-text-muted">A widget you designed (optional)</span>
          <select value={step.widget ?? ""} onChange={(e) => onPatch({ widget: e.target.value || undefined })}
                  className={INPUT}>
            <option value="">The built-in input for this kind of answer</option>
            {[...new Set([step.widget, ...catalog])].filter(Boolean).map((id) => (
              <option key={id} value={id}>{id}</option>
            ))}
          </select>
        </label>
        {step.widget ? (
          <button onClick={() => onOpenWidget(step.widget)}
                  className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-brand/30 bg-brand-subtle px-2.5 py-1.5 text-[12px] font-medium text-brand hover:bg-brand/20 transition-colors focus-ring">
            <ExternalLink className="h-3 w-3" /> Open “{step.widget}” in the designer
          </button>
        ) : (
          <span className="mt-1 block text-[11px] text-text-muted">
            A number is a number box, a list is a select, a secret is masked.{" "}
            <Link to="/widgets/new" onClick={(e) => { if (!(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) && !confirmLeave()) e.preventDefault(); }}
                  className="text-brand hover:text-brand-hover">Design a widget →</Link>
          </span>
        )}
      </Section>

      {single ? (
        <button type="button" onClick={() => setAsks([...asks, [`answer_${asks.length + 1}`, "…?"]])}
                className="inline-flex items-center gap-1 text-[11px] text-text-muted hover:text-text-primary">
          <Plus className="h-3 w-3" /> ask a second question in this same step
        </button>
      ) : (
        <button type="button" onClick={() => setAsks([...asks, [`answer_${asks.length + 1}`, "…?"]])}
                className="inline-flex items-center gap-1 text-[11px] text-text-muted hover:text-text-primary">
          <Plus className="h-3 w-3" /> another question
        </button>
      )}
    </>
  );
}

/** A usable name from whatever was typed: `Order ID` -> `order_id`. */
function nameOf(typed: string): string {
  return typed.replace(/[^A-Za-z0-9_]+/g, "_").replace(/^([0-9])/, "_$1");
}

function Rules({ step, names, tools, onPatch }: {
  step: SkillStep; names: string[]; tools: ToolInfo[]; onPatch: (p: SkillStep) => void;
}) {
  // Kept here while being typed: a half-written rule has no place in the file yet.
  const [rows, setRows] = useState<Array<[string, string]>>(() => rulesOf(step));
  const saved = JSON.stringify(rulesOf(step));
  useEffect(() => {
    setRows((current) => (JSON.stringify(current.filter(([c]) => c.trim())) === saved ? current : JSON.parse(saved)));
  }, [saved]);

  const commit = (next: Array<[string, string]>) => {
    setRows(next);
    const complete = next.filter(([c]) => c.trim());
    onPatch({ check: complete.length ? Object.fromEntries(complete.map(([c, m]) => [c.trim(), m])) : undefined });
  };
  return (
    <div className="space-y-2">
      {rows.length === 0 && (
        <p className="text-[11px] leading-relaxed text-text-muted">
          No rules. Add one to refuse an answer and ask again, with your own sentence.
        </p>
      )}
      {rows.map(([cond, message], i) => (
        <div key={i} className="rounded-lg border border-border-subtle p-2.5 space-y-2">
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Condition label="It must be true that" value={cond} names={names} tools={tools}
                         onChange={(x) => commit(rows.map((r, k) => (k === i ? [x, r[1]] : r)))} />
            </div>
            <button type="button" title="Remove this rule" onClick={() => commit(rows.filter((_, k) => k !== i))}
                    className="mb-0.5 rounded-md p-1.5 text-text-muted hover:text-rose-500 hover:bg-rose-500/10">
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
          <Text label="Otherwise say this, and ask again" value={message}
                onChange={(x) => commit(rows.map((r, k) => (k === i ? [r[0], x] : r)))} />
        </div>
      ))}
      <button type="button" onClick={() => setRows([...rows, ["", ""]])}
              className="inline-flex items-center gap-1 rounded-md border border-border-subtle px-2 py-1 text-[11px] font-medium text-text-secondary hover:text-text-primary hover:border-border">
        <Plus className="h-3 w-3" /> Add a rule
      </button>
    </div>
  );
}

// --------------------------------------------------------------------------- confirm
function ConfirmFields({ step, onPatch }: Props) {
  const raw = step.confirm;
  const named = raw && typeof raw === "object" ? Object.keys(raw)[0] : null;
  const question = named ? String(raw[named] ?? "") : String(raw ?? "");
  const setQuestion = (text: string) => onPatch({ confirm: named ? { [named]: text } : text });
  const someoneElse = typeof step.by === "string";
  return (
    <>
      <Area label="The question" value={question} onChange={setQuestion}
            hint="The engine adds what a yes will run, and with which inputs. You do not have to write that." />
      <Select label="Who says yes?" value={someoneElse ? "other" : "user"}
              options={[["user", "The user in this conversation"], ["other", "Someone else (the flow waits for them)"]]}
              onChange={(x) => onPatch({ by: x === "other" ? (step.by || "manager") : undefined })} />
      {someoneElse && (
        <>
          <Text label="Who" value={step.by} ph="manager" onChange={(x) => onPatch({ by: x || "manager" })}
                hint="A role or a name. The request appears on the Approvals page under it." />
          <p className="rounded-md border border-amber-500/30 bg-amber-500/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            The flow stops here until they answer. Neither the user in the conversation nor the model can answer for
            them, and the tool is held to the inputs they were shown.{" "}
            <Link to="/approvals" className="underline">Open Approvals →</Link>
          </p>
        </>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- say
function SayFields({ step, above, tools, catalog, onPatch, onOpenWidget }: Props) {
  const text = String(step.say ?? "");
  const whole = /^\s*\{\{\s*[\w.]+\s*\}\}\s*$/.test(text);
  const shown = "widget" in step;
  const data: Record<string, unknown> = step.with && typeof step.with === "object" ? step.with : {};
  return (
    <>
      {shown && (
        <>
          <label className="block">
            <span className="text-[11px] uppercase tracking-wider text-text-muted">The widget</span>
            <select value={step.widget ?? ""} className={INPUT}
                    onChange={(e) => onPatch({ widget: e.target.value })}>
              <option value="">— choose a widget you designed —</option>
              {[...new Set([step.widget, ...catalog])].filter(Boolean).map((id) => (
                <option key={id} value={id}>{id}</option>
              ))}
            </select>
            <span className="mt-1 block text-[11px] leading-relaxed text-text-muted">
              Shown in the chat with the data below, under the sentence. Nothing waits on it.{" "}
              <Link to="/widgets/new" className="text-brand hover:text-brand-hover">Design a widget →</Link>
            </span>
          </label>
          {step.widget && (
            <button onClick={() => onOpenWidget(step.widget)}
                    className="inline-flex items-center gap-1.5 rounded-lg border border-brand/30 bg-brand-subtle px-2.5 py-1.5 text-[12px] font-medium text-brand hover:bg-brand/20 transition-colors focus-ring">
              <ExternalLink className="h-3 w-3" /> Open “{step.widget}” in the designer
            </button>
          )}
          <Section title="Its data">
            <DataRows value={data} names={above}
                      onChange={(next) => onPatch({ with: Object.keys(next).length ? next : undefined })} />
          </Section>
        </>
      )}
      <Area label={shown ? "Said above the widget" : "What to say"} value={text} rows={shown ? 2 : 3}
            onChange={(x) => onPatch({ say: x })} />
      {above.length > 0 && (
        <div className="flex flex-wrap items-center gap-1">
          <span className="text-[11px] text-text-muted">Insert an answer:</span>
          {above.map((n) => (
            <button key={n} type="button" onClick={() => onPatch({ say: `${text}${text && !text.endsWith(" ") ? " " : ""}{{${n}}}` })}
                    className="rounded-md border border-border-subtle bg-bg-muted/70 px-1.5 py-px font-mono text-[11px] text-text-secondary hover:text-text-primary hover:border-border">
              {n}
            </button>
          ))}
        </div>
      )}
      <p className="text-[11px] leading-relaxed text-text-muted">
        A tool's result is read by the tool's name: <code className="font-mono">{"{{"}{tools.find((t) => !t.changes_something)?.name ?? "find_order"}.status{"}}"}</code>.
        The lookup runs when this line needs it. A line that is only a result, such as <code className="font-mono">{"{{quote.summary}}"}</code>, is shown as a card.
      </p>
      {whole && !shown && (
        <Text label="Card title (optional)" value={step.title} onChange={(x) => onPatch({ title: x || undefined })} />
      )}
    </>
  );
}

/** `with:` for a designed widget: each value it binds, as a name and what fills it. */
function DataRows({ value, names, onChange }: {
  value: Record<string, unknown>; names: string[]; onChange: (next: Record<string, unknown>) => void;
}) {
  const rows = Object.entries(value);
  const set = (i: number, key: string, v: string) => {
    const next = rows.map(([k, old], j) => (j === i ? [key, v] : [k, old]) as [string, unknown]);
    onChange(Object.fromEntries(next.filter(([k]) => k)));
  };
  return (
    <div className="space-y-1.5">
      {rows.length === 0 && (
        <p className="text-[11px] leading-relaxed text-text-muted">
          A widget shows what it is given: add a value for each name it uses, such as{" "}
          <code className="font-mono">total</code> from <code className="font-mono">{"{{quote.total}}"}</code>.
        </p>
      )}
      {rows.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input value={k} placeholder="name" spellCheck={false}
                 onChange={(e) => set(i, e.target.value.replace(/[^A-Za-z0-9_]/g, "_"), String(v ?? ""))}
                 className={cn(INPUT, "mt-0 w-[38%] font-mono")} />
          <input value={String(v ?? "")} placeholder={names[0] ? `{{${names[0]}}}` : "{{answer}}"} spellCheck={false}
                 onChange={(e) => set(i, k, e.target.value)} className={cn(INPUT, "mt-0 flex-1 font-mono")} />
          <button type="button" title="Remove" onClick={() => onChange(Object.fromEntries(rows.filter((_, j) => j !== i)))}
                  className="rounded-md p-1.5 text-text-muted hover:text-rose-500 hover:bg-rose-500/10">
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onChange({ ...value, [`value_${rows.length + 1}`]: "" })}
              className="inline-flex items-center gap-1 rounded-md border border-border-subtle px-2 py-1 text-[11px] font-medium text-text-secondary hover:text-text-primary hover:border-border">
        <Plus className="h-3 w-3" /> Add a value
      </button>
    </div>
  );
}

// --------------------------------------------------------------------------- chat
function ChatFields({ step, tools, above, onPatch }: Props) {
  const lookups = tools.filter((t) => !t.changes_something);
  const chosen = toolsOf(step);
  const toggle = (name: string) =>
    onPatch({ tools: chosen.includes(name) ? chosen.filter((t) => t !== name) : [...chosen, name] });
  const fixed = [...new Set(chosen.flatMap((n) => (tools.find((t) => t.name === n)?.inputs ?? []).map((i) => i.name)))]
    .filter((n) => above.includes(n));
  return (
    <>
      <Area label="What it says first" value={String(step.chat ?? "")} onChange={(x) => onPatch({ chat: x })}
            hint="Fixed text. Then the user asks anything, and the model answers with the lookups below." />
      <Section title="The lookups it may use">
        {lookups.length === 0 ? (
          <p className="text-[11px] leading-relaxed text-text-muted">
            No tool in the library only looks something up yet.{" "}
            <Link to="/tools" className="text-brand hover:text-brand-hover">Open the Tools page →</Link>
          </p>
        ) : (
          <div className="space-y-1">
            {lookups.map((t) => (
              <label key={t.name} className="flex items-start gap-2 rounded-md px-1 py-0.5 text-[12px] hover:bg-bg-muted/50">
                <input type="checkbox" checked={chosen.includes(t.name)} onChange={() => toggle(t.name)}
                       className="mt-0.5 h-3.5 w-3.5 rounded border-border accent-brand" />
                <span className="min-w-0">
                  <span className="font-mono text-text-primary">{t.name}</span>
                  {t.description && <span className="block text-[11px] text-text-muted">{t.description}</span>}
                </span>
              </label>
            ))}
          </div>
        )}
        {fixed.length > 0 && (
          <p className="mt-2 flex items-start gap-1.5 rounded-md border border-emerald-500/25 bg-emerald-500/[0.06] px-2.5 py-2 text-[11px] leading-relaxed text-emerald-700 dark:text-emerald-400">
            <Check className="mt-0.5 h-3 w-3 shrink-0" />
            Fixed to the answers above, so the model cannot change them: {fixed.join(", ")}.
          </p>
        )}
        <p className="mt-2 text-[11px] leading-relaxed text-text-muted">
          In a chat the model chooses which tool to call, so only tools that look something up can be listed.
          A chat is the last step of a skill: the conversation stays in it until the user moves on.
        </p>
      </Section>
    </>
  );
}

// --------------------------------------------------------------------------- a block's header
export function BlockPanel({ step, index, issues, tools, above, onPatchBlock, onRemoveBlock }: {
  step: SkillStep;
  index?: number;
  issues: Issue[];
  tools: ToolInfo[];
  above: string[];
  onPatchBlock: (patch: Partial<Block>) => void;
  /** Drop the header; its steps stay, no longer conditional or repeated. */
  onRemoveBlock: () => void;
}) {
  const block = blockOf(step)!;
  const repeat = repeatOf(block);
  const info = PALETTE.find((i) => i.kind === (repeat ? "repeat" : "condition"))!;
  const Icon = ICONS[info.icon];
  const theme = THEME[info.icon];
  return (
    <div>
      <div className="px-4 pt-4 pb-3 border-b border-border-subtle">
        <div className="flex items-center gap-2">
          <span className={cn("inline-flex h-7 w-7 items-center justify-center rounded-md border", theme.chip)}>
            <Icon className="h-4 w-4" />
          </span>
          <span className={cn("text-xs font-bold uppercase tracking-[0.14em]", theme.text)}>{info.label}</span>
          {index !== undefined && <span className="ml-auto text-[11px] text-text-muted tabular-nums">step {index}</span>}
        </div>
        <p className="mt-2 text-[12px] leading-relaxed text-text-muted">{info.blurb}</p>
      </div>

      {issues.length > 0 && (
        <div className="border-b border-border-subtle px-4 py-3 space-y-1.5">
          {issues.map((issue, i) => (
            <div key={i} className={cn(
              "flex items-start gap-1.5 text-[12px] leading-relaxed",
              issue.level === "error" ? "text-rose-600 dark:text-rose-400" : "text-amber-600 dark:text-amber-400",
            )}>
              <AlertCircle className="mt-[3px] h-3 w-3 shrink-0" />
              <span>{issue.message}</span>
            </div>
          ))}
        </div>
      )}

      <div className="p-4 space-y-3">
        {repeat ? (
          <>
            <Text label="Name of the list" value={repeat[0]} mono
                  onChange={(x) => onPatchBlock({ repeat: { [nameOf(x) || "items"]: repeat[1] } })}
                  hint={`Steps after the repeat, and the tools they run, read the list by this name: {{${repeat[0]}}}, or a tool input called ${repeat[0]}.`} />
            <Area label="The question that starts another item" value={repeat[1]}
                  onChange={(x) => onPatchBlock({ repeat: { [repeat[0]]: x } })}
                  hint="A yes asks the questions under it again for the next item; a no ends the list. Only the user can answer it." />
            <p className="rounded-md border border-border-subtle bg-bg-muted/50 px-2.5 py-2 text-[11px] leading-relaxed text-text-muted">
              Only Ask steps go inside a repeat. Each item's answers become one record of the list, and the
              confirmation after it shows the whole list. Run the tool once, after the repeat, and give it the list.
            </p>
            <Section title="Only sometimes">
              <Condition label="Repeat only when (optional)" value={block.when} names={above} tools={tools}
                         onChange={(x) => onPatchBlock({ when: x || undefined })} />
            </Section>
          </>
        ) : (
          <>
            <Condition label="Run the steps under it only when" value={block.when} names={above} tools={tools}
                       onChange={(x) => onPatchBlock({ when: x })} />
            <p className="rounded-md border border-border-subtle bg-bg-muted/50 px-2.5 py-2 text-[11px] leading-relaxed text-text-muted">
              Every step indented under this one runs only if the condition holds. It is checked again on every
              turn, so a corrected answer can switch the branch on or off.
            </p>
          </>
        )}
        <button type="button" onClick={onRemoveBlock}
                className="inline-flex items-center gap-1 rounded-md border border-border-subtle px-2 py-1 text-[11px] font-medium text-text-secondary hover:text-rose-600 hover:border-rose-500/40">
          <Trash2 className="h-3 w-3" /> Remove the {repeat ? "repeat" : "condition"} (keep its steps)
        </button>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- do
function DoFields({ step, tools, skills, above, confirmed, onPatch, onInsertAbove }: Props) {
  const target = String(step.do ?? "");
  const tool = tools.find((t) => t.name === target);
  const isSkill = !tool && target !== "" && isSkillTarget(target, skills);
  const bound: Record<string, unknown> = step.with && typeof step.with === "object" ? step.with : {};
  const lookups = tools.filter((t) => !t.changes_something);
  const writes = tools.filter((t) => t.changes_something);
  const unknown = target && !tool && !isSkill;

  const setBound = (name: string, value: string | undefined) => {
    const next = { ...bound };
    if (value === undefined) delete next[name];
    else next[name] = value;
    onPatch({ with: Object.keys(next).length ? next : undefined });
  };

  return (
    <>
      <label className="block">
        <span className="text-[11px] uppercase tracking-wider text-text-muted">Run</span>
        <select value={target} className={INPUT}
                onChange={(e) => onPatch({ do: e.target.value, with: undefined, as: undefined, else: undefined })}>
          {!target && <option value="">— choose a tool or a skill —</option>}
          {unknown && <option value={target}>{target} (not in the library)</option>}
          {lookups.length > 0 && (
            <optgroup label="Tools that look something up">
              {lookups.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </optgroup>
          )}
          {writes.length > 0 && (
            <optgroup label="Tools that change something">
              {writes.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
            </optgroup>
          )}
          {skills.length > 0 && (
            <optgroup label="Another skill">
              {skills.map((s) => <option key={s} value={s}>{s}</option>)}
            </optgroup>
          )}
        </select>
        <span className="mt-1 block text-[11px] text-text-muted">
          Tools are Python functions in the library.{" "}
          <Link to="/tools" className="text-brand hover:text-brand-hover">Open the Tools page →</Link>
        </span>
      </label>

      {tool && (
        <>
          {tool.description && <p className="text-[12px] leading-relaxed text-text-secondary">{tool.description}</p>}
          <p className={cn(
            "rounded-md border px-2.5 py-2 text-[11px] leading-relaxed",
            tool.changes_something
              ? "border-amber-500/30 bg-amber-500/[0.07] text-amber-700 dark:text-amber-400"
              : "border-border-subtle bg-bg-muted/50 text-text-muted",
          )}>
            {tool.changes_something
              ? "This tool changes something. It needs a Confirm step above it, runs at most once per conversation, and is written to the audit trail."
              : "This tool only looks something up. You rarely need it as a step: mention its result in a condition or a sentence and it runs when needed."}
          </p>
          {tool.changes_something && !confirmed && (
            <button type="button" onClick={() => onInsertAbove({ confirm: "Shall I go ahead?" })}
                    className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-400 hover:bg-amber-500/20">
              <Plus className="h-3 w-3" /> Add a Confirm step above this
            </button>
          )}

          <Section title="What it needs">
            {tool.takes_everything ? (
              <p className="text-[11px] leading-relaxed text-text-muted">
                It receives every answer asked above it{tool.source === "built in" ? " (never a secret, never the yes)" : ""}.
              </p>
            ) : tool.inputs.length === 0 ? (
              <p className="text-[11px] text-text-muted">Nothing.</p>
            ) : (
              <div className="space-y-1.5">
                {tool.inputs.map((input) => {
                  const passed = input.name in bound;
                  const asked = above.includes(input.name);
                  return (
                    <div key={input.name} className="rounded-lg border border-border-subtle px-2.5 py-2">
                      <div className="flex items-center gap-2">
                        {passed || asked ? (
                          <Check className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                        ) : input.required ? (
                          <AlertCircle className="h-3.5 w-3.5 shrink-0 text-rose-500" />
                        ) : (
                          <CircleDashed className="h-3.5 w-3.5 shrink-0 text-text-muted" />
                        )}
                        <span className="font-mono text-[12px] text-text-primary">{input.name}</span>
                        <span className="text-[11px] text-text-muted">
                          {input.type === "choice" ? "one of a list" : input.type}{input.secret ? ", secret" : ""}
                        </span>
                        <span className="ml-auto text-[11px] text-text-muted">
                          {passed ? "passed below" : asked ? "the answer of that name" : input.required ? "not asked yet" : "optional"}
                        </span>
                      </div>
                      {passed ? (
                        <div className="mt-1.5 flex items-center gap-1.5">
                          <input value={String(bound[input.name] ?? "")} placeholder="{{another_answer}} or a fixed value"
                                 onChange={(e) => setBound(input.name, e.target.value)}
                                 className={cn(INPUT, "mt-0 font-mono")} />
                          <button type="button" title="Stop passing a value" onClick={() => setBound(input.name, undefined)}
                                  className="rounded-md p-1.5 text-text-muted hover:text-rose-500 hover:bg-rose-500/10">
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ) : !asked && (
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          <button type="button"
                                  onClick={() => onInsertAbove({
                                    ask: { [input.name]: input.description || `What is the ${input.name.replace(/_/g, " ")}?` },
                                  })}
                                  className="inline-flex items-center gap-1 rounded-md border border-brand/40 bg-brand-subtle px-2 py-1 text-[11px] font-medium text-brand hover:bg-brand/20">
                            <Plus className="h-3 w-3" /> Ask the user for it
                          </button>
                          <button type="button" onClick={() => setBound(input.name, above[0] ? `{{${above[0]}}}` : "")}
                                  className="rounded-md border border-border-subtle px-2 py-1 text-[11px] font-medium text-text-secondary hover:text-text-primary hover:border-border">
                            Pass a value instead
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </Section>

          <Section title="Its result">
            <Text label="Read the result by this name (optional)" value={step.as} mono ph={tool.name}
                  onChange={(x) => onPatch({ as: nameOf(x) || undefined })}
                  hint={`Later steps read it as {{${step.as || tool.name}.something}}. Give it another name to run the same tool twice.`} />
          </Section>
        </>
      )}

      {isSkill && (
        <>
          <p className="rounded-md border border-border-subtle bg-bg-muted/50 px-2.5 py-2 text-[11px] leading-relaxed text-text-muted">
            Hands the user to <span className="font-mono">{target}</span>. It asks its own questions and keeps its own
            rules; when it finishes, this flow carries on at the next step. If it is cancelled or the user says no in
            it, this flow stops here.
          </p>
          <Area label="If it did not finish, say" value={step.else} onChange={(x) => onPatch({ else: x || undefined })} />
        </>
      )}

      {unknown && (
        <p className="rounded-md border border-rose-500/30 bg-rose-500/[0.07] px-2.5 py-2 text-[11px] leading-relaxed text-rose-700 dark:text-rose-400">
          “{target}” is not a tool in the library or a skill of this agent. Choose one above, or write the tool on the Tools page.
        </p>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- small pieces
const INPUT =
  "mt-0.5 w-full rounded-md border border-border bg-bg-muted px-2 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand transition-colors";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="pt-1">
      <div className="mb-2 border-t border-border-subtle pt-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
        {title}
      </div>
      {children}
    </div>
  );
}

function Text({ label, value, onChange, ph, hint, mono }: {
  label: string; value?: string; onChange: (v: string) => void; ph?: string; hint?: string; mono?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <input type="text" value={value ?? ""} placeholder={ph} onChange={(e) => onChange(e.target.value)}
             className={cn(INPUT, mono && "font-mono")} />
      {hint && <span className="mt-1 block text-[11px] leading-relaxed text-text-muted">{hint}</span>}
    </label>
  );
}

function Area({ label, value, onChange, rows = 2, hint }: {
  label: string; value?: string; onChange: (v: string) => void; rows?: number; hint?: string;
}) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <textarea value={value ?? ""} rows={rows} onChange={(e) => onChange(e.target.value)} className={cn(INPUT, "resize-y")} />
      {hint && <span className="mt-1 block text-[11px] leading-relaxed text-text-muted">{hint}</span>}
    </label>
  );
}

function Select({ label, value, options, onChange, hint }: {
  label: string; value: string; options: Array<[string, string]>; onChange: (v: string) => void; hint?: string;
}) {
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} className={INPUT}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
      {hint && <span className="mt-1 block text-[11px] leading-relaxed text-text-muted">{hint}</span>}
    </label>
  );
}

/** A condition: `amount >= 1000`, `plan == 'gold'`, `quote_payment.needs_code`. */
function Condition({ label, value, names, tools, onChange }: {
  label: string; value?: string; names: string[]; tools: ToolInfo[]; onChange: (v: string) => void;
}) {
  const id = useId();
  const lookups = tools.filter((t) => !t.changes_something && !t.takes_everything).map((t) => `${t.name}.`);
  const example = names[0] ? `${names[0]} == 'something'` : "amount >= 1000";
  return (
    <label className="block">
      <span className="text-[11px] uppercase tracking-wider text-text-muted">{label}</span>
      <input type="text" value={value ?? ""} list={id} placeholder={example} spellCheck={false}
             onChange={(e) => onChange(e.target.value)} className={cn(INPUT, "font-mono")} />
      <datalist id={id}>
        {[...names, ...lookups].map((n) => <option key={n} value={n} />)}
      </datalist>
      <span className="mt-1 block text-[11px] leading-relaxed text-text-muted">
        An answer's name, a comparison, or a tool's result:{" "}
        <code className="font-mono">amount &gt;= 1000</code> ·{" "}
        <code className="font-mono">plan == 'gold'</code> ·{" "}
        <code className="font-mono">find_order.found</code>. Join with <code className="font-mono">and</code> /{" "}
        <code className="font-mono">or</code> / <code className="font-mono">not</code>.
      </span>
    </label>
  );
}

import { useEffect, useState, useRef, useCallback, useId } from "react";
import type { FC, FocusEvent, FormEvent, HTMLAttributes, ReactNode } from "react";
import { CheckCircle2, AlertTriangle, Info, XCircle } from "lucide-react";
import { cn } from "../../lib/cn";
import { useFlowRendererConfig, validateFieldAt } from "../FlowConfig";
import { rootAttrs, textStyleOf } from "../styleProps";
import type { SpecNode } from "../../agui/types";

export type SubmitFn = (payload: unknown) => void;

export type PrimitiveProps = {
  node: SpecNode;
  onSubmit: SubmitFn;
  disabled?: boolean;
  renderChildren: (children?: SpecNode[]) => ReactNode;
};

// Turn a field key ("from_account") into a readable accessible name
// ("From Account"). Used only when there is no visible label — and never the
// placeholder, which must not be used as a naming mechanism (a11y guidance).
function humanize(name: string): string {
  return name
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// Sync `aria-invalid` with the native `:user-invalid` pseudo-class, which only
// matches AFTER the user has interacted — so screen readers don't announce
// "invalid" the instant a required field is focused.
// (modern-web-guidance → accessible-error-announcement)
function useUserInvalid() {
  const [invalid, setInvalid] = useState<boolean | undefined>(undefined);
  const check = (el: HTMLInputElement | HTMLSelectElement) => {
    try {
      setInvalid(el.matches(":user-invalid"));
    } catch {
      /* :user-invalid unsupported (older engines) — leave unset */
    }
  };
  return {
    "aria-invalid": invalid,
    onBlur: (e: FocusEvent<HTMLElement>) =>
      check(e.currentTarget as HTMLInputElement | HTMLSelectElement),
    onInput: (e: FormEvent<HTMLElement>) => {
      if (invalid !== undefined) check(e.currentTarget as HTMLInputElement | HTMLSelectElement);
    },
  } as const;
}

const toneStyles = {
  default: "border-border bg-bg-elevated",
  info: "border-sky-500/30 bg-sky-500/[0.05]",
  warning: "border-amber-500/30 bg-amber-500/[0.05]",
  danger: "border-rose-500/30 bg-rose-500/[0.05]",
  success: "border-emerald-500/30 bg-emerald-500/[0.05]",
} as const;

type Tone = keyof typeof toneStyles;

export function Card({ node, renderChildren }: PrimitiveProps) {
  const p = node.props ?? {};
  const tone = (p.tone as Tone) || "default";
  return (
    <div
      {...rootAttrs(node)}
      className={cn(
        "rounded-2xl border p-5 shadow-card animate-fade-up widget-enter",
        toneStyles[tone],
      )}
    >
      {Boolean(p.title || p.subtitle) && (
        <div className="mb-4">
          {Boolean(p.title) && <h3 className="text-base font-semibold text-text-primary">{String(p.title)}</h3>}
          {Boolean(p.subtitle) && <p className="mt-0.5 text-sm text-text-secondary">{String(p.subtitle)}</p>}
        </div>
      )}
      <div className="space-y-3">{renderChildren(node.children)}</div>
    </div>
  );
}

export function TextNode({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const weight = p.weight === "bold" ? "font-semibold" : "font-normal";
  const muted = p.muted ? "text-text-secondary" : "text-text-primary";
  // `value` is the canonical key (widget catalog + visual designer). `text` and
  // `content` are accepted because generators in the wild emit them, and a text
  // node that silently renders empty is how a confirmation card ended up asking
  // "are you sure?" with nothing on screen saying what.
  const value = p.value ?? p.text ?? p.content ?? "";
  return <p {...rootAttrs(node)} className={cn("text-sm leading-relaxed", weight, muted)}>{String(value)}</p>;
}

export function Row({ node, renderChildren }: PrimitiveProps) {
  const p = node.props ?? {};
  const justify = {
    start: "justify-start", center: "justify-center", end: "justify-end", between: "justify-between",
  }[(p.justify as string) || "start"] || "justify-start";
  const gap = { sm: "gap-2", md: "gap-3", lg: "gap-4" }[(p.gap as string) || "md"] || "gap-3";
  // `aig-row` is the container-query hook: rows stack vertically when their
  // container is narrow (see lib.css / index.css), so they never overflow a
  // small embed.
  return <div {...rootAttrs(node)} className={cn("aig-row flex items-center", justify, gap)}>{renderChildren(node.children)}</div>;
}

export function Column({ node, renderChildren }: PrimitiveProps) {
  const p = node.props ?? {};
  const gap = { sm: "gap-2", md: "gap-3", lg: "gap-4" }[(p.gap as string) || "md"] || "gap-3";
  return <div {...rootAttrs(node)} className={cn("flex flex-col", gap)}>{renderChildren(node.children)}</div>;
}

export function Divider({ node }: PrimitiveProps) {
  return <div {...rootAttrs(node)} className="h-px w-full bg-border-subtle" />;
}

export function Alert({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const tone = (p.tone as Tone) || "info";
  const Icon = tone === "danger" ? XCircle : tone === "warning" ? AlertTriangle : tone === "success" ? CheckCircle2 : Info;
  // Announce to assistive tech: danger/warning interrupt (assertive), the rest
  // are polite status updates.
  const assertive = tone === "danger" || tone === "warning";
  return (
    <div
      // role="alert" implies aria-live=assertive and role="status" implies
      // polite — so we don't set aria-live too (no redundant ARIA).
      role={assertive ? "alert" : "status"}
      {...rootAttrs(node)}
      className={cn("flex gap-3 rounded-xl border p-3 text-sm", toneStyles[tone])}
    >
      <Icon aria-hidden="true" className="h-4 w-4 shrink-0 mt-0.5 opacity-80" />
      <span className="text-text-primary">{String(p.message ?? "")}</span>
    </div>
  );
}

export function Badge({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  return (
    <span {...rootAttrs(node)} className="inline-flex items-center rounded-full bg-bg-muted px-2.5 py-0.5 text-xs font-medium text-text-secondary border border-border-subtle">
      {String(p.label ?? "")}
    </span>
  );
}

export function Button({ node, onSubmit, disabled }: PrimitiveProps) {
  const p = node.props ?? {};
  const variant = (p.variant as string) || "secondary";
  const styles = {
    primary: "bg-brand text-brand-fg hover:bg-brand-hover shadow-glow",
    secondary: "bg-bg-muted text-text-primary border border-border hover:border-border-strong",
    ghost: "text-text-secondary hover:text-text-primary hover:bg-bg-muted",
    danger: "bg-rose-500 text-white hover:bg-rose-600",
  }[variant] || "";

  return (
    <button
      {...rootAttrs(node)}
      type={p.action === "submit" ? "submit" : "button"}
      disabled={disabled}
      onClick={(e) => {
        if (p.action === "submit" && !p.formField) {
          e.preventDefault();
          onSubmit(p.payload ?? {});
        }
      }}
      className={cn(
        "inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium transition-colors duration-150",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        "focus:outline-none focus:ring-2 focus:ring-brand/40",
        styles,
      )}
    >
      {String(p.label ?? "Submit")}
    </button>
  );
}

/** Coerce a spec prop to a number attribute value, dropping anything unusable. */
function numAttr(v: unknown): number | undefined {
  if (typeof v === "number") return Number.isFinite(v) ? v : undefined;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
}

export function Input({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const id = useId();
  const name = String(p.name ?? "value");
  const label = p.label ? String(p.label) : undefined;
  const config = useFlowRendererConfig();
  const skill = p.skill ? String(p.skill) : config.skill;
  // Live validation is OPT-IN. Embedded in someone else's app there is no
  // aigenos-flow server at a guessable path, so with no configured endpoint we
  // never fire a request (and never show a tick we cannot justify).
  const validateEndpoint =
    typeof p.validateEndpoint === "string" && p.validateEndpoint
      ? p.validateEndpoint
      : typeof config.validateEndpoint === "string" && config.validateEndpoint
        ? config.validateEndpoint
        : null;
  const validity = useUserInvalid();

  const [validationState, setValidationState] = useState<"idle" | "validating" | "valid" | "invalid">("idle");
  const [errorMsg, setErrorMsg] = useState<string | undefined>(undefined);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Never leave a pending debounce behind on unmount.
  useEffect(() => () => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
  }, []);

  const runValidation = useCallback(
    (value: string) => {
      if (!validateEndpoint) return;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (!value) {
        setValidationState("idle");
        setErrorMsg(undefined);
        return;
      }
      setValidationState("validating");
      debounceRef.current = setTimeout(() => {
        validateFieldAt(validateEndpoint, name, value, skill)
          .then((result) => {
            if (result.valid) {
              setValidationState("valid");
              setErrorMsg(undefined);
            } else {
              setValidationState("invalid");
              setErrorMsg(result.error);
            }
          })
          .catch(() => {
            setValidationState("idle");
            setErrorMsg(undefined);
          });
      }, 300);
    },
    [name, skill, validateEndpoint],
  );

  const validationClass =
    validationState === "valid" ? "field-valid" :
    validationState === "invalid" ? "field-invalid" :
    validationState === "validating" ? "field-validating" : "";

  return (
    <div {...rootAttrs(node)} className="block">
      {label && (
        // Explicit for/id association (preferred over implicit nesting).
        <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-text-secondary">{label}</label>
      )}
      <div className="relative">
        <input
          id={id}
          style={textStyleOf(p)}
          type={(p.type as string) || "text"}
          name={name}
          required={Boolean(p.required)}
          placeholder={p.placeholder as string}
          defaultValue={p.defaultValue as string}
          // Carry the field's declared schema into the DOM, so the widget the
          // engine generated enforces the same bounds the FieldSpec declares.
          min={numAttr(p.min)}
          max={numAttr(p.max)}
          // "any" lifts the browser's default grid for a number input (step 1,
          // counted from `min`), which rejects 2600 when `min` is 0.01.
          step={p.step === "any" ? "any" : numAttr(p.step)}
          minLength={numAttr(p.minLength)}
          maxLength={numAttr(p.maxLength)}
          pattern={typeof p.pattern === "string" ? p.pattern : undefined}
          inputMode={p.inputMode as HTMLAttributes<HTMLInputElement>["inputMode"]}
          autoComplete={typeof p.autoComplete === "string" ? p.autoComplete : undefined}
          // Accessible name = visible label, else a humanized field name — never
          // the placeholder.
          aria-label={label ? undefined : humanize(name)}
          {...validity}
          onBlur={(e) => {
            validity.onBlur(e);
            runValidation(e.currentTarget.value);
          }}
          className={cn(
            "w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary",
            "placeholder:text-text-muted",
            "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand",
            "transition-colors",
            validationClass,
          )}
        />
        {validationState === "valid" && <span className="field-valid-icon absolute right-3 top-1/2 -translate-y-1/2" />}
      </div>
      {validationState === "invalid" && errorMsg && (
        <div className="field-error-msg" role="alert">{errorMsg}</div>
      )}
    </div>
  );
}

export function Select({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const options = asList<{ value: string; label: string }>(p.options);
  const id = useId();
  const name = String(p.name ?? "value");
  const label = p.label ? String(p.label) : undefined;
  const validity = useUserInvalid();
  return (
    <div {...rootAttrs(node)} className="block">
      {label && (
        <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-text-secondary">{label}</label>
      )}
      <select
        id={id}
        style={textStyleOf(p)}
        name={name}
        required={Boolean(p.required)}
        defaultValue={p.defaultValue as string}
        aria-label={label ? undefined : humanize(name)}
        {...validity}
        className={cn(
          "w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary",
          "focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand",
        )}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

export function Form({ node, onSubmit, renderChildren, disabled }: PrimitiveProps) {
  const p = node.props ?? {};
  const submitLabel = (p.submitLabel as string) || "Submit";
  const [submitting, setSubmitting] = useState(false);

  // Once the widget is externally resolved (next bot message has rendered),
  // drop the local "…" state so the button settles into its done look.
  useEffect(() => {
    if (disabled) setSubmitting(false);
  }, [disabled]);

  function handle(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (disabled) return;
    setSubmitting(true);
    const fd = new FormData(e.currentTarget);
    const payload: Record<string, unknown> = {};
    fd.forEach((v, k) => { payload[k] = v; });
    onSubmit(payload);
  }

  const showLabel = disabled ? "✓ Submitted" : submitting ? "…" : submitLabel;

  return (
    <form {...rootAttrs(node)} onSubmit={handle} className="space-y-3">
      {renderChildren(node.children)}
      <div className="flex justify-end pt-1">
        <button
          type="submit"
          disabled={submitting || disabled}
          aria-busy={submitting}
          className={cn(
            "inline-flex items-center justify-center rounded-lg px-4 py-2 text-sm font-medium",
            "bg-brand text-brand-fg hover:bg-brand-hover shadow-glow transition-colors duration-150",
            "disabled:opacity-50 focus-ring",
            !submitting && !disabled && "form-submit-btn",
          )}
        >
          {showLabel}
        </button>
      </div>
    </form>
  );
}

export function Image({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  return (
    <img
      {...rootAttrs(node)}
      src={String(p.src ?? "")}
      alt={String(p.alt ?? "")}
      className="rounded-lg border border-border-subtle max-h-64 object-cover"
    />
  );
}

export function Loading({ node }: PrimitiveProps) {
  return (
    <div
      role="status"
      aria-busy="true"
      {...rootAttrs(node)}
      className="shimmer-surface h-16 rounded-xl border border-border-subtle"
    >
      <span className="visually-hidden">Loading…</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Rich, data-bound primitives. Each one, on selection, writes hidden inputs for
// the form fields it fills (props.fields maps formFieldName -> source key in the
// chosen option/row), so the enclosing <form> collects them via FormData exactly
// like a plain input. A declarative `widget:` step that saves [client_id,
// client_full_name] pairs with an autosuggest whose fields map those two.
// ---------------------------------------------------------------------------

type Dict = Record<string, unknown>;

/** List props may arrive as an UNRESOLVED binding string (`"{{rows}}"`) — the
 *  widget designer renders specs raw, and a runtime host may skip resolution.
 *  Never crash on that: coerce to a list, and let callers surface the binding. */
function asList<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

/** The binding expression if the value is exactly one, else null. */
function bindingOf(v: unknown): string | null {
  return typeof v === "string" && /^\{\{[\s\S]+\}\}$/.test(v.trim()) ? v.trim() : null;
}

/** Muted designer/runtime hint for a data slot that is bound but unresolved. */
function BoundHint({ binding, what }: { binding: string; what: string }) {
  return (
    <div className="rounded-md border border-dashed border-border px-3 py-2.5 text-xs text-text-muted italic">
      {what} bound to <code className="not-italic font-mono text-text-secondary">{binding}</code> — filled
      from the flow at runtime.
    </div>
  );
}

function HiddenFields({ fields, source }: { fields: Record<string, string>; source: Dict }) {
  return (
    <>
      {Object.entries(fields).map(([formField, key]) => (
        <input key={formField} type="hidden" name={formField} value={String(source[key] ?? "")} />
      ))}
    </>
  );
}

export function Autosuggest({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const options = asList<Dict>(p.options);
  const optionsBinding = bindingOf(p.options);
  const display = String(p.display ?? "label");
  const fields = (p.fields as Record<string, string>) ?? {};
  const label = p.label ? String(p.label) : undefined;
  const id = useId();
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Dict | null>(null);
  const q = query.toLowerCase();
  const matches = picked ? [] : options.filter((o) => String(o[display] ?? "").toLowerCase().includes(q));
  return (
    <div {...rootAttrs(node)} className="block">
      {label && <label htmlFor={id} className="mb-1.5 block text-xs font-medium text-text-secondary">{label}</label>}
      <input
        id={id}
        style={textStyleOf(p)}
        autoComplete="off"
        value={picked ? String(picked[display] ?? "") : query}
        onChange={(e) => { setPicked(null); setQuery(e.target.value); }}
        placeholder={String(p.placeholder ?? "Type to search…")}
        className={cn(
          "w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm text-text-primary",
          "placeholder:text-text-muted focus:outline-none focus:ring-2 focus:ring-brand/40 focus:border-brand",
        )}
      />
      {optionsBinding && (
        <div className="mt-1"><BoundHint binding={optionsBinding} what="Options" /></div>
      )}
      {picked && <HiddenFields fields={fields} source={picked} />}
      {!picked && query && matches.length > 0 && (
        <ul className="mt-1 max-h-44 overflow-auto rounded-lg border border-border bg-bg-elevated text-sm shadow-card">
          {matches.map((o, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => { setPicked(o); setQuery(""); }}
                className="block w-full px-3 py-2 text-left text-text-primary hover:bg-bg-muted"
              >
                {String(o[display] ?? "")}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function DataTable({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const rows = asList<Dict>(p.rows);
  const rowsBinding = bindingOf(p.rows);
  const columns = asList<{ key: string; label: string }>(p.columns);
  const fields = (p.fields as Record<string, string>) ?? {};
  // A table with no field to fill is a display: no radio column, no row click,
  // so a cost breakdown or a `show:` list never looks like something to pick.
  const selectable = Object.keys(fields).length > 0;
  const [sel, setSel] = useState<number | null>(null);
  if (rowsBinding && rows.length === 0) {
    return (
      <div {...rootAttrs(node)}>
        <BoundHint binding={rowsBinding} what="Rows" />
      </div>
    );
  }
  return (
    <div {...rootAttrs(node)} className="overflow-auto rounded-lg border border-border-subtle">
      <table style={textStyleOf(p)} className="w-full text-sm">
        <thead>
          <tr className="border-b border-border-subtle text-text-secondary">
            {selectable && <th className="w-8 px-2 py-2" />}
            {columns.map((c) => (
              <th key={c.key} className="px-3 py-2 text-left font-medium">{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={i}
              onClick={selectable ? () => setSel(i) : undefined}
              className={cn(
                "border-b border-border-subtle/60",
                selectable && "cursor-pointer hover:bg-bg-muted",
                sel === i && "bg-brand/10",
              )}
            >
              {selectable && (
                <td className="px-2 py-2">
                  {/* required: Continue with no row picked must not post an empty answer. */}
                  <input type="radio" name="__row" required checked={sel === i} onChange={() => setSel(i)} aria-label={`Select row ${i + 1}`} />
                </td>
              )}
              {columns.map((c) => (
                <td key={c.key} className="px-3 py-2 text-text-primary">{String(r[c.key] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {sel !== null && rows[sel] && <HiddenFields fields={fields} source={rows[sel]} />}
    </div>
  );
}

type TimelineEvent = { title?: string; time?: string; description?: string };

/**
 * Accept the `{items, dateKey}` shape as well as the canonical `events` list.
 * Row generators describe a dated table that way, and reading only `events`
 * rendered an empty <ol>.
 */
function timelineEvents(p: Record<string, unknown>): TimelineEvent[] {
  const direct = asList<TimelineEvent>(p.events);
  if (direct.length) return direct;
  const items = asList<Dict>(p.items);
  if (!items.length) return [];
  const dateKey = typeof p.dateKey === "string" ? p.dateKey : undefined;
  return items.map((row) => {
    const keys = Object.keys(row).filter((k) => k !== dateKey);
    const scalar = keys.filter((k) => {
      const v = row[k];
      return v === null || ["string", "number", "boolean"].includes(typeof v);
    });
    const str = (k?: string) => (k === undefined || row[k] == null ? undefined : String(row[k]));
    return {
      time: dateKey ? str(dateKey) : undefined,
      title: str(scalar[0]),
      description: str(scalar[1]),
    };
  });
}

export function Timeline({ node }: PrimitiveProps) {
  const p = node.props ?? {};
  const events = timelineEvents(p);
  const eventsBinding = bindingOf(p.events) ?? bindingOf(p.items);
  if (eventsBinding && events.length === 0) {
    return (
      <div {...rootAttrs(node)}>
        <BoundHint binding={eventsBinding} what="Events" />
      </div>
    );
  }
  return (
    <ol {...rootAttrs(node)} className="relative ml-3 border-l border-border-subtle">
      {events.map((ev, i) => (
        <li key={i} className="mb-4 ml-4">
          <span className="absolute -left-1.5 mt-1 h-3 w-3 rounded-full bg-brand shadow-glow" />
          {ev.time && <div className="text-xs text-text-muted">{ev.time}</div>}
          {ev.title && <div className="text-sm font-medium text-text-primary">{ev.title}</div>}
          {ev.description && <p className="text-sm text-text-secondary">{ev.description}</p>}
        </li>
      ))}
    </ol>
  );
}

export const REGISTRY: Record<string, FC<PrimitiveProps>> = {
  card: Card,
  text: TextNode,
  row: Row,
  column: Column,
  divider: Divider,
  alert: Alert,
  badge: Badge,
  button: Button,
  input: Input,
  select: Select,
  form: Form,
  image: Image,
  loading: Loading,
  // rich, data-bound widgets
  autosuggest: Autosuggest,
  datatable: DataTable,
  timeline: Timeline,
};

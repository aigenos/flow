import { Handle, Position, NodeProps } from "reactflow";
import {
  AlertCircle, MessageSquare, Boxes, Wrench, GitBranch, CircleStop, Play,
  HelpCircle, Table2, LayoutDashboard, ShieldCheck, MessagesSquare, Code2, Link2,
  CheckCircle2, UserCheck, GitFork, Repeat,
} from "lucide-react";
import { cn } from "../lib/cn";

export type WfNodeData = {
  label: string;
  nodeType:
    // legacy widget-workflow engine nodes
    | "message" | "widget" | "tool_call" | "branch" | "end" | "start"
    // SKILL.md flow verbs (the nine step kinds; "widget" is in the line above)
    | "ask" | "say" | "do" | "pick" | "show" | "check" | "chat" | "link"
    // the short form's own kinds: a yes, and how the palette's entries look
    | "confirm" | "tool" | "approval"
    // block headers: the steps indented under them run only if / once per item
    | "condition" | "repeat"
    // read-only outline of an imperative (@flow) skill
    | "code";
  widget?: string;
  tool?: string;
  preview?: string;
  isStart?: boolean;
  /** skill-flow mode: the raw SKILL.md step this node represents */
  step?: Record<string, any>;
  /** small annotations: when / when_missing guards, save fields, … */
  badges?: string[];
  /** 1-based position in the executed flow (linear order). */
  index?: number;
  /** worst preflight finding on this step — see builder/validate.ts */
  issue?: "error" | "warn";
  /** The word shown as the node's kind when it differs from `nodeType` (a
   *  short-form `do:` that runs another skill is drawn as a link but is a "do"). */
  kindLabel?: string;
};

export const ICONS = {
  message: MessageSquare,
  widget: Boxes,
  tool_call: Wrench,
  branch: GitBranch,
  end: CircleStop,
  start: Play,
  ask: HelpCircle,
  say: MessageSquare,
  do: Wrench,
  pick: Table2,
  show: LayoutDashboard,
  check: ShieldCheck,
  chat: MessagesSquare,
  link: Link2,
  confirm: CheckCircle2,
  code: Code2,
  tool: Wrench,
  approval: UserCheck,
  condition: GitFork,
  repeat: Repeat,
};

/** Per-verb visual identity: icon chip, accent hairline, selection ring.
 *  Explicit class literals (Tailwind can't see composed names), and each hue
 *  is stated twice: a 600/700 shade that holds up on the paper-white light
 *  theme, and the lighter 400 for the charcoal dark theme. */
export const THEME: Record<WfNodeData["nodeType"], { chip: string; bar: string; text: string }> = {
  ask: { chip: "bg-sky-500/10 text-sky-700 border-sky-600/25 dark:bg-sky-500/15 dark:text-sky-400 dark:border-sky-500/30", bar: "from-sky-500/70", text: "text-sky-700 dark:text-sky-400" },
  say: { chip: "bg-slate-500/10 text-slate-600 border-slate-500/25 dark:bg-slate-400/15 dark:text-slate-300 dark:border-slate-400/30", bar: "from-slate-400/60", text: "text-slate-600 dark:text-slate-300" },
  do: { chip: "bg-amber-500/10 text-amber-700 border-amber-600/25 dark:bg-amber-500/15 dark:text-amber-400 dark:border-amber-500/30", bar: "from-amber-500/70", text: "text-amber-700 dark:text-amber-400" },
  pick: { chip: "bg-violet-500/10 text-violet-700 border-violet-600/25 dark:bg-violet-500/15 dark:text-violet-400 dark:border-violet-500/30", bar: "from-violet-500/70", text: "text-violet-700 dark:text-violet-400" },
  show: { chip: "bg-cyan-500/10 text-cyan-700 border-cyan-600/25 dark:bg-cyan-500/15 dark:text-cyan-400 dark:border-cyan-500/30", bar: "from-cyan-500/70", text: "text-cyan-700 dark:text-cyan-400" },
  check: { chip: "bg-emerald-500/10 text-emerald-700 border-emerald-600/25 dark:bg-emerald-500/15 dark:text-emerald-400 dark:border-emerald-500/30", bar: "from-emerald-500/70", text: "text-emerald-700 dark:text-emerald-400" },
  chat: { chip: "bg-fuchsia-500/10 text-fuchsia-700 border-fuchsia-600/25 dark:bg-fuchsia-500/15 dark:text-fuchsia-400 dark:border-fuchsia-500/30", bar: "from-fuchsia-500/70", text: "text-fuchsia-700 dark:text-fuchsia-400" },
  widget: { chip: "bg-violet-500/10 text-violet-700 border-violet-600/25 dark:bg-violet-500/15 dark:text-violet-400 dark:border-violet-500/30", bar: "from-violet-500/70", text: "text-violet-700 dark:text-violet-400" },
  link: { chip: "bg-indigo-500/10 text-indigo-700 border-indigo-600/25 dark:bg-indigo-500/15 dark:text-indigo-400 dark:border-indigo-500/30", bar: "from-indigo-500/70", text: "text-indigo-700 dark:text-indigo-400" },
  confirm: { chip: "bg-emerald-500/10 text-emerald-700 border-emerald-600/25 dark:bg-emerald-500/15 dark:text-emerald-400 dark:border-emerald-500/30", bar: "from-emerald-500/70", text: "text-emerald-700 dark:text-emerald-400" },
  code: { chip: "bg-slate-500/10 text-slate-600 border-slate-500/25 dark:bg-slate-500/15 dark:text-slate-300 dark:border-slate-500/30", bar: "from-slate-500/60", text: "text-slate-600 dark:text-slate-300" },
  tool: { chip: "bg-amber-500/10 text-amber-700 border-amber-600/25 dark:bg-amber-500/15 dark:text-amber-400 dark:border-amber-500/30", bar: "from-amber-500/70", text: "text-amber-700 dark:text-amber-400" },
  approval: { chip: "bg-rose-500/10 text-rose-700 border-rose-600/25 dark:bg-rose-500/15 dark:text-rose-400 dark:border-rose-500/30", bar: "from-rose-500/70", text: "text-rose-700 dark:text-rose-400" },
  condition: { chip: "bg-orange-500/10 text-orange-700 border-orange-600/25 dark:bg-orange-500/15 dark:text-orange-400 dark:border-orange-500/30", bar: "from-orange-500/70", text: "text-orange-700 dark:text-orange-400" },
  repeat: { chip: "bg-teal-500/10 text-teal-700 border-teal-600/25 dark:bg-teal-500/15 dark:text-teal-400 dark:border-teal-500/30", bar: "from-teal-500/70", text: "text-teal-700 dark:text-teal-400" },
  message: { chip: "bg-sky-500/10 text-sky-700 border-sky-600/25 dark:bg-sky-500/15 dark:text-sky-400 dark:border-sky-500/30", bar: "from-sky-500/70", text: "text-sky-700 dark:text-sky-400" },
  tool_call: { chip: "bg-amber-500/10 text-amber-700 border-amber-600/25 dark:bg-amber-500/15 dark:text-amber-400 dark:border-amber-500/30", bar: "from-amber-500/70", text: "text-amber-700 dark:text-amber-400" },
  branch: { chip: "bg-emerald-500/10 text-emerald-700 border-emerald-600/25 dark:bg-emerald-500/15 dark:text-emerald-400 dark:border-emerald-500/30", bar: "from-emerald-500/70", text: "text-emerald-700 dark:text-emerald-400" },
  end: { chip: "bg-rose-500/10 text-rose-700 border-rose-600/25 dark:bg-rose-500/15 dark:text-rose-400 dark:border-rose-500/30", bar: "from-rose-500/70", text: "text-rose-700 dark:text-rose-400" },
  start: { chip: "bg-slate-500/10 text-slate-600 border-slate-500/25 dark:bg-slate-500/15 dark:text-slate-300 dark:border-slate-500/30", bar: "from-slate-500/60", text: "text-slate-600 dark:text-slate-300" },
};

export function WfNode({ data, selected }: NodeProps<WfNodeData>) {
  const Icon = ICONS[data.nodeType] ?? MessageSquare;
  const theme = THEME[data.nodeType] ?? THEME.say;
  // A block's header: the steps indented under it belong to it.
  const header = data.nodeType === "condition" || data.nodeType === "repeat";
  return (
    <div
      className={cn(
        "group/node relative rounded-xl border border-border-subtle w-[260px]",
        header && "border-dashed",
        "bg-bg-surface/85 backdrop-blur-md shadow-card overflow-hidden",
        "transition-all duration-150 hover:border-border hover:shadow-glow/60 hover:-translate-y-px",
        selected && "ring-2 ring-brand/70 shadow-glow border-transparent",
      )}
    >
      {/* verb-tinted hairline across the top */}
      <div className={cn("h-[2px] w-full bg-gradient-to-r to-transparent", theme.bar)} />

      {data.nodeType !== "start" && (
        <Handle
          type="target"
          position={Position.Top}
          className="!w-2.5 !h-2.5 !bg-bg-elevated !border-2 !border-text-muted/60 !-top-[5px] transition-colors hover:!border-brand"
        />
      )}

      <div className="px-3 pt-2 pb-2.5">
        <div className="flex items-center gap-2">
          <span className={cn("inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md border", theme.chip)}>
            <Icon className="h-3.5 w-3.5" />
          </span>
          <span className={cn("text-[11px] font-bold uppercase tracking-[0.14em]", theme.text)}>
            {data.kindLabel ?? data.nodeType}
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            {data.issue && (
              <span
                title={data.issue === "error" ? "This step won't save yet" : "Worth a second look"}
                className={cn(
                  "inline-flex h-4 w-4 items-center justify-center rounded-full border",
                  data.issue === "error"
                    ? "border-rose-500/40 bg-rose-500/15 text-rose-400"
                    : "border-amber-500/40 bg-amber-500/15 text-amber-400",
                )}
              >
                <AlertCircle className="h-2.5 w-2.5" />
              </span>
            )}
            {data.index !== undefined && (
              <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-bg-muted border border-border-subtle px-1 text-[10px] font-semibold text-text-muted tabular-nums">
                {data.index}
              </span>
            )}
          </span>
        </div>

        <div className="mt-1.5 text-[13px] font-medium leading-snug text-text-primary line-clamp-2">
          {data.label}
        </div>

        {data.widget && (
          <div className="mt-1 text-[11px] text-text-secondary font-mono truncate">widget: {data.widget}</div>
        )}
        {data.tool && (
          <div className="mt-1 text-[11px] text-text-secondary font-mono truncate">tool: {data.tool}</div>
        )}
        {data.preview && (
          <div className="mt-1 text-[11px] text-text-muted line-clamp-2 leading-relaxed">{data.preview}</div>
        )}

        {(data.badges?.length ?? 0) > 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {data.badges!.map((b) => (
              <span
                key={b}
                className="inline-flex items-center rounded-md border border-border-subtle bg-bg-muted/80 px-1.5 py-px text-[10px] font-medium text-text-secondary"
              >
                {b}
              </span>
            ))}
          </div>
        )}
      </div>

      {data.nodeType !== "end" && (
        <Handle
          type="source"
          position={Position.Bottom}
          className="!w-2.5 !h-2.5 !bg-brand !border-2 !border-bg !-bottom-[5px] shadow-glow transition-transform hover:!scale-125"
        />
      )}
    </div>
  );
}

export const NODE_TYPES = { wf: WfNode };

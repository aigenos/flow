import {
  MessageSquare, Boxes, Wrench, GitBranch, CircleStop,
  HelpCircle, Table2, LayoutDashboard, ShieldCheck, MessagesSquare, Link2,
} from "lucide-react";
import type { WfNodeData } from "./nodes";

type Item = { type: WfNodeData["nodeType"]; label: string; icon: any };

type Props = {
  onAdd: (type: WfNodeData["nodeType"]) => void;
  /** Palette override — the skill-flow canvas passes the step kinds. */
  items?: Item[];
};

/** Legacy widget-workflow engine palette (default). */
const ENGINE_ITEMS: Item[] = [
  { type: "message", label: "Message", icon: MessageSquare },
  { type: "widget", label: "Widget", icon: Boxes },
  { type: "tool_call", label: "Tool call", icon: Wrench },
  { type: "branch", label: "Branch", icon: GitBranch },
  { type: "end", label: "End", icon: CircleStop },
];

/** SKILL.md flow palette — the whole authoring vocabulary. */
export const SKILL_FLOW_ITEMS: Item[] = [
  { type: "ask", label: "Ask", icon: HelpCircle },
  { type: "say", label: "Say", icon: MessageSquare },
  { type: "do", label: "Do", icon: Wrench },
  { type: "pick", label: "Pick", icon: Table2 },
  { type: "show", label: "Show", icon: LayoutDashboard },
  { type: "check", label: "Check", icon: ShieldCheck },
  { type: "chat", label: "Chat", icon: MessagesSquare },
  { type: "link", label: "Link", icon: Link2 },
];

export function Toolbar({ onAdd, items }: Props) {
  return (
    <div className="flex items-center gap-1.5 p-2 rounded-xl border border-border-subtle bg-bg-surface">
      {(items ?? ENGINE_ITEMS).map(({ type, label, icon: Icon }) => (
        <button
          key={type}
          onClick={() => onAdd(type)}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-muted transition-colors"
        >
          <Icon className="h-3 w-3" />
          {label}
        </button>
      ))}
    </div>
  );
}

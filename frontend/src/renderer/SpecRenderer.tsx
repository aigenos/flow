import type { SpecNode } from "../agui/types";
import { REGISTRY } from "./primitives";

type Props = {
  spec: SpecNode;
  onSubmit: (payload: unknown) => void;
  disabled?: boolean;
};

export function SpecRenderer({ spec, onSubmit, disabled }: Props) {
  // `aig-widget` establishes a CSS container-query context (container-type:
  // inline-size) so embedded widgets adapt to their host container width
  // rather than the viewport — the whole point of an embeddable renderer.
  return (
    <div className="aig-widget widget-enter">
      <Node node={spec} onSubmit={onSubmit} disabled={disabled} />
    </div>
  );
}

function Node({ node, onSubmit, disabled }: { node: SpecNode; onSubmit: (p: unknown) => void; disabled?: boolean }) {
  const Component = REGISTRY[node.type];
  if (!Component) {
    return (
      <div className="rounded-md border border-rose-500/40 bg-rose-500/10 p-2 text-xs text-rose-700 dark:text-rose-300">
        Unknown primitive: <code>{node.type}</code>
      </div>
    );
  }
  return (
    <Component
      node={node}
      onSubmit={onSubmit}
      disabled={disabled}
      renderChildren={(children) =>
        (children ?? []).map((c, i) => <Node key={i} node={c} onSubmit={onSubmit} disabled={disabled} />)
      }
    />
  );
}

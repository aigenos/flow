import type { SpecNode } from "../agui/types";

/** Anything the user must act on. A card that only shows data has none. */
const INTERACTIVE_TYPES = new Set(["input", "select", "autosuggest", "datatable", "form", "button"]);

/** True when a widget wants an answer; false for one that only displays something. */
export function asksForInput(spec: SpecNode | undefined): boolean {
  let found = false;
  const walk = (node: SpecNode | undefined) => {
    if (!node || found) return;
    // A table is an input only when a row can be picked (it has `fields`).
    // `show:` of a list renders the same primitive with nothing to answer.
    const picks = node.type !== "datatable" || Object.keys((node.props?.fields as object) ?? {}).length > 0;
    if (INTERACTIVE_TYPES.has(node.type) && picks) found = true;
    else node.children?.forEach(walk);
  };
  walk(spec);
  return found;
}

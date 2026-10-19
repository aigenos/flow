/** Helpers for manipulating the spec tree by node id. Each node carries an
 *  internal `_id` for selection + dnd; this is stripped on save. */
import type { SpecNode } from "../agui/types";

export type TreeNode = SpecNode & { _id: string };

let counter = 0;
export function newId(prefix = "n"): string {
  counter += 1;
  return `${prefix}_${Date.now().toString(36)}_${counter}`;
}

export function withIds(node: SpecNode): TreeNode {
  return {
    _id: newId(),
    type: node.type,
    props: node.props ?? {},
    children: (node.children ?? []).map(withIds),
  };
}

export function stripIds(node: TreeNode): SpecNode {
  const out: SpecNode = { type: node.type };
  if (node.props && Object.keys(node.props).length) out.props = node.props;
  if (node.children && node.children.length) out.children = node.children.map((c) => stripIds(c as TreeNode));
  return out;
}

export function findNode(root: TreeNode, id: string): TreeNode | null {
  if (root._id === id) return root;
  for (const c of root.children ?? []) {
    const m = findNode(c as TreeNode, id);
    if (m) return m;
  }
  return null;
}

export function findParent(root: TreeNode, id: string): TreeNode | null {
  for (const c of root.children ?? []) {
    if ((c as TreeNode)._id === id) return root;
    const m = findParent(c as TreeNode, id);
    if (m) return m;
  }
  return null;
}

export function updateNode(root: TreeNode, id: string, updater: (n: TreeNode) => TreeNode): TreeNode {
  if (root._id === id) return updater(root);
  return {
    ...root,
    children: (root.children ?? []).map((c) => updateNode(c as TreeNode, id, updater)),
  };
}

export function removeNode(root: TreeNode, id: string): TreeNode {
  return {
    ...root,
    children: (root.children ?? [])
      .filter((c) => (c as TreeNode)._id !== id)
      .map((c) => removeNode(c as TreeNode, id)),
  };
}

export function addChild(root: TreeNode, parentId: string, child: TreeNode): TreeNode {
  if (root._id === parentId) {
    return { ...root, children: [...(root.children ?? []), child] };
  }
  return {
    ...root,
    children: (root.children ?? []).map((c) => addChild(c as TreeNode, parentId, child)),
  };
}

export function moveChild(root: TreeNode, id: string, parentId: string, index: number): TreeNode {
  const node = findNode(root, id);
  if (!node) return root;
  let r = removeNode(root, id);
  r = insertAt(r, parentId, node, index);
  return r;
}

export function insertAt(root: TreeNode, parentId: string, child: TreeNode, index: number): TreeNode {
  if (root._id === parentId) {
    const next = [...(root.children ?? [])];
    next.splice(index, 0, child);
    return { ...root, children: next };
  }
  return {
    ...root,
    children: (root.children ?? []).map((c) => insertAt(c as TreeNode, parentId, child, index)),
  };
}

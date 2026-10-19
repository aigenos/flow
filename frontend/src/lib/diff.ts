/**
 * Minimal line diff — enough to answer "what changed between these two saves?"
 *
 * Classic LCS over lines. SKILL.md files are hundreds of lines at most, so the
 * O(n·m) table is fine; `MAX_LINES` is a guard against someone restoring a
 * pathological blob rather than a real expectation.
 */

export type DiffLine = {
  kind: "same" | "add" | "del";
  text: string;
  /** 1-based line number in the before/after text, when the line exists there. */
  a?: number;
  b?: number;
};

const MAX_LINES = 4000;

export function diffLines(before: string, after: string): DiffLine[] {
  const A = before.split("\n");
  const B = after.split("\n");

  if (A.length > MAX_LINES || B.length > MAX_LINES) {
    // Too big to diff meaningfully in a popover — report it as a wholesale change.
    return [
      { kind: "del", text: `${A.length} lines`, a: 1 },
      { kind: "add", text: `${B.length} lines`, b: 1 },
    ];
  }

  // lcs[i][j] = length of the longest common subsequence of A[i:] and B[j:]
  const lcs: number[][] = Array.from({ length: A.length + 1 }, () =>
    new Array<number>(B.length + 1).fill(0),
  );
  for (let i = A.length - 1; i >= 0; i--) {
    for (let j = B.length - 1; j >= 0; j--) {
      lcs[i][j] = A[i] === B[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) {
      out.push({ kind: "same", text: A[i], a: i + 1, b: j + 1 });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: "del", text: A[i], a: i + 1 });
      i++;
    } else {
      out.push({ kind: "add", text: B[j], b: j + 1 });
      j++;
    }
  }
  for (; i < A.length; i++) out.push({ kind: "del", text: A[i], a: i + 1 });
  for (; j < B.length; j++) out.push({ kind: "add", text: B[j], b: j + 1 });

  return out;
}

/**
 * Drop long runs of unchanged lines, keeping `context` lines either side of
 * each change. Returns hunks so the caller can render a gap marker between them.
 */
export function hunks(lines: DiffLine[], context = 2): DiffLine[][] {
  const changed = lines
    .map((l, idx) => (l.kind === "same" ? -1 : idx))
    .filter((idx) => idx >= 0);
  if (changed.length === 0) return [];

  const keep = new Set<number>();
  for (const idx of changed) {
    for (let k = idx - context; k <= idx + context; k++) {
      if (k >= 0 && k < lines.length) keep.add(k);
    }
  }

  const out: DiffLine[][] = [];
  let current: DiffLine[] = [];
  let prev = -2;
  for (const idx of [...keep].sort((x, y) => x - y)) {
    if (idx !== prev + 1 && current.length) {
      out.push(current);
      current = [];
    }
    current.push(lines[idx]);
    prev = idx;
  }
  if (current.length) out.push(current);
  return out;
}

export function countChanges(lines: DiffLine[]): { added: number; removed: number } {
  return {
    added: lines.filter((l) => l.kind === "add").length,
    removed: lines.filter((l) => l.kind === "del").length,
  };
}

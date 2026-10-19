import { useCallback, useSyncExternalStore } from "react";

/**
 * Role mode. "workspace" = no-code surface for PMs/ops; "studio" = the
 * developer surface (code, canvas, protocol). Persisted so a user lands back
 * in the role they chose.
 */
export type Mode = "workspace" | "studio";

const STORAGE_KEY = "aigenos-flow-mode";

function readInitial(): Mode {
  try {
    // `?mode=studio|workspace` deep-links into a role (docs, captures, shared
    // links) and persists like a manual switch.
    const q = new URLSearchParams(window.location.search).get("mode");
    if (q === "workspace" || q === "studio") {
      localStorage.setItem(STORAGE_KEY, q);
      return q;
    }
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "workspace" || v === "studio") return v;
  } catch {
    /* ignore */
  }
  return "workspace";
}

let currentMode: Mode = readInitial();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function setGlobalMode(mode: Mode) {
  if (mode === currentMode) return;
  currentMode = mode;
  try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* ignore */ }
  for (const listener of listeners) listener();
}

export function useMode() {
  const mode = useSyncExternalStore<Mode>(
    subscribe,
    () => currentMode,
    () => "workspace",
  );
  const setMode = useCallback((m: Mode) => setGlobalMode(m), []);
  return { mode, setMode };
}

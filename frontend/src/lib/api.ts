import { validateFieldAt, type FieldValidation } from "../renderer/FlowConfig";

const API = "/api";

export type WidgetListItem = { id: string; version: string; description: string };
export type WorkflowListItem = { id: string; description: string };

/** A saved snapshot in the workspace store (SQLite, versioned per artifact). */
export type Revision = { version: number; label: string | null; created_at: string; size: number };
export type RevisionKind = "skill" | "widget" | "workflow";
export type ActivityEvent = {
  kind: RevisionKind;
  ref: string;
  action: string;
  detail: string | null;
  created_at: string;
};

/**
 * Turn a failed response into a sentence a person can read.
 *
 * Web servers answer with HTML error pages, so echoing the raw body puts
 * `501 <!DOCTYPE HTML><html>…` in front of the user. Prefer the API's own
 * `detail`, never surface markup, and say plainly when the backend simply
 * isn't there — the common case when the UI is served without it.
 */
async function errorMessage(r: Response): Promise<string> {
  let body = "";
  try {
    body = (await r.text()).trim();
  } catch {
    /* body unreadable — fall back to the status */
  }

  if (body.startsWith("{")) {
    try {
      const parsed = JSON.parse(body) as { detail?: unknown; message?: unknown };
      const detail = Array.isArray(parsed.detail)
        ? (parsed.detail[0] as { msg?: string } | undefined)?.msg
        : parsed.detail ?? parsed.message;
      if (typeof detail === "string" && detail) return detail;
    } catch {
      /* not JSON after all */
    }
  }

  if (!body || body.startsWith("<")) {
    return r.status === 404 || r.status === 405 || r.status === 501
      ? "Can't reach the aigenos/flow server — is the backend running?"
      : `The server returned ${r.status}.`;
  }
  return body.length > 200 ? `${body.slice(0, 200)}…` : body;
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error(await errorMessage(r));
  return r.json() as Promise<T>;
}

const UNREACHABLE = "Can't reach the aigenos/flow server — is the backend running on port 8000?";

/**
 * The agent's loaded skills. Rejects with a message a person can act on: the
 * server's own reason when it gave one (a missing provider package answers 503
 * with the `pip install` line to run), and "can't reach" only when nothing
 * answered.
 */
export async function listSkills<T = { name: string; description: string }>(): Promise<T[]> {
  let r: Response;
  try {
    r = await fetch(`${API}/flow/skills`);
  } catch {
    throw new Error(UNREACHABLE);
  }
  if (!r.ok) {
    const body = (await r.json().catch(() => null)) as { detail?: unknown; install?: unknown } | null;
    if (typeof body?.detail === "string" && body.detail) {
      throw new Error(typeof body.install === "string" ? `${body.detail}\n${body.install}` : body.detail);
    }
    throw new Error(UNREACHABLE);
  }
  const data = (await r.json()) as { skills?: T[] } | T[];
  return Array.isArray(data) ? data : Array.isArray(data?.skills) ? data.skills : [];
}

export const api = {
  // widgets
  listWidgets: () => fetch(`${API}/widgets`).then((r) => j<{ widgets: WidgetListItem[] }>(r)).then((x) => x.widgets),
  getWidget: (id: string) => fetch(`${API}/widgets/${id}`).then((r) => j<any>(r)),
  saveWidget: (id: string, body: any) =>
    fetch(`${API}/widgets/${id}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<any>(r)),
  deleteWidget: (id: string) => fetch(`${API}/widgets/${id}`, { method: "DELETE" }).then((r) => j<any>(r)),

  // AI generation
  aiProviders: () =>
    fetch(`${API}/ai/providers`).then((r) =>
      j<{
        anthropic: { available: boolean; model: string };
        ollama: { available: boolean; model: string; host: string; installed_models: string[] };
        active: string | null;
      }>(r),
    ),
  generateWidget: (prompt: string, suggestedId?: string) =>
    fetch(`${API}/ai/generate-widget`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ prompt, suggested_id: suggestedId }),
    }).then(async (r) => {
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      return r.json() as Promise<{ widget: any; provider: string; model: string }>;
    }),

  // workflows
  listWorkflows: () => fetch(`${API}/workflows`).then((r) => j<{ workflows: WorkflowListItem[] }>(r)).then((x) => x.workflows),
  getWorkflow: (id: string) => fetch(`${API}/workflows/${id}`).then((r) => j<any>(r)),
  saveWorkflow: (id: string, body: any) =>
    fetch(`${API}/workflows/${id}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<any>(r)),
  deleteWorkflow: (id: string) => fetch(`${API}/workflows/${id}`, { method: "DELETE" }).then((r) => j<any>(r)),

  // workspace store — version history + restore + activity (SQLite-backed)
  listRevisions: (kind: RevisionKind, ref: string) =>
    fetch(`${API}/store/revisions/${kind}/${encodeURIComponent(ref)}`)
      .then((r) => j<{ revisions: Revision[] }>(r))
      .then((x) => x.revisions),
  getRevision: (kind: RevisionKind, ref: string, version: number) =>
    fetch(`${API}/store/revisions/${kind}/${encodeURIComponent(ref)}/${version}`).then((r) =>
      j<{ version: number; label: string | null; created_at: string; body: any }>(r),
    ),
  restoreWidget: (id: string, version: number) =>
    fetch(`${API}/widgets/${encodeURIComponent(id)}/restore/${version}`, { method: "POST" }).then(
      (r) => j<{ status: string; version: number }>(r),
    ),
  restoreSkill: (name: string, version: number) =>
    fetch(`${API}/flow/skills/${encodeURIComponent(name)}/restore/${version}`, {
      method: "POST",
    }).then((r) => j<any>(r)),
  activity: (limit = 30) =>
    fetch(`${API}/store/activity?limit=${limit}`)
      .then((r) => j<{ events: ActivityEvent[] }>(r))
      .then((x) => x.events),

  // verification — what the loader proved, and what the scenarios replay
  guarantees: (skill: string) =>
    fetch(`${API}/flow/skills/${encodeURIComponent(skill)}/guarantees`).then((r) =>
      j<GuaranteeReport>(r),
    ),
  listScenarios: (skill: string) =>
    fetch(`${API}/flow/skills/${encodeURIComponent(skill)}/scenarios`)
      .then((r) => j<{ scenarios: Scenario[] }>(r))
      .then((x) => x.scenarios),
  saveScenarios: (skill: string, scenarios: Scenario[]) =>
    fetch(`${API}/flow/skills/${encodeURIComponent(skill)}/scenarios`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ scenarios }),
    }).then((r) => j<{ saved: number; version: number }>(r)),
  runScenarios: (skill: string) =>
    fetch(`${API}/flow/skills/${encodeURIComponent(skill)}/scenarios/run`, {
      method: "POST",
    }).then((r) => j<ScenarioRun>(r)),

  // gallery — the curated skills shipped with the repo
  gallery: () =>
    fetch(`${API}/flow/gallery`)
      .then((r) => j<{ templates: GalleryTemplate[] }>(r))
      .then((x) => x.templates),
  installTemplate: (name: string) =>
    fetch(`${API}/flow/gallery/${encodeURIComponent(name)}/install`, { method: "POST" }).then(
      (r) => j<{ name: string; version: number }>(r),
    ),
};

// ---------- verification types ----------

/** One property the fail-closed loader checks. `n/a` = nothing to bite on. */
export type Guarantee = {
  id: string;
  title: string;
  status: "holds" | "n/a";
  detail: string;
  kind: "load" | "runtime";
};
export type GuaranteeReport = { properties: Guarantee[]; holds: number; checked: number };

/** One recorded interpretation command — what the LLM "would" have returned. */
export type Emit = { type: string; field?: string; value?: unknown; [k: string]: unknown };
export type ScenarioTurn = { user: string; emits: Emit[] };
export type ScenarioExpect = {
  fields?: Record<string, unknown>;
  results?: Record<string, unknown>;
  awaiting_field?: string | null;
  done?: boolean;
  last_assistant_contains?: string;
};
export type Scenario = {
  id: string;
  name?: string;
  turns: ScenarioTurn[];
  expect?: ScenarioExpect;
};

/** One step outcome from a replay — see backend/aigenos_flow/trace.py. */
export type TraceEntry = {
  step: number;
  verb: string;
  outcome: "ran" | "skipped" | "paused" | "passed" | "failed";
  detail?: string;
  guard?: string;
};
export type ScenarioResult = {
  id: string;
  name: string;
  status: "passed" | "failed" | "error";
  failures: string[];
  trace: TraceEntry[];
  deterministic: boolean;
  fields?: Record<string, unknown>;
  messages?: string[];
};
export type ScenarioRun = {
  skill: string;
  results: ScenarioResult[];
  total: number;
  passed: number;
  failed: number;
};

export type GalleryTemplate = {
  name: string;
  description: string;
  mode: string;
  steps: number;
  guarantees: number;
  installed: boolean;
};

/**
 * Studio-side field validation. The Studio app IS the aigenos-flow server, so
 * the same-origin path is correct here. The published renderer must not assume
 * it — it calls `validateFieldAt` with an endpoint the host configured via
 * <FlowConfigProvider>.
 */
export async function validateField(
  field: string,
  value: any,
  skill?: string,
  endpoint = "/api/flow/validate-field",
): Promise<FieldValidation> {
  return validateFieldAt(endpoint, field, value, skill);
}

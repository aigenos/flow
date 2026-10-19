import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, Navigate } from "react-router-dom";
import {
  AlertTriangle, CheckCircle2, Database, KeyRound, Plus, RefreshCw,
  Save, ShieldCheck, Trash2, Unplug,
} from "lucide-react";
import { useMode } from "../hooks/useMode";
import { AppDialog, AppDialogTitle } from "../components/AppDialog";

const MonacoEditor = lazy(() => import("@monaco-editor/react"));
const TOKEN_KEY = "aigenos-connector-admin-token";

type Connection = {
  id: string; provider: "postgresql" | "mongodb"; name: string; slug: string;
  access_level: "read" | "read_write"; credential_mode: string;
  credential_configured: boolean; enabled: boolean;
};
type Parameter = {
  type: "string" | "integer" | "number" | "boolean" | "object" | "array";
  required?: boolean; sensitive?: boolean; description?: string; pattern?: string;
};
type Operation = {
  id: string; connection_id: string; name: string; display_name: string;
  tool_name: string; kind: string; mode: "read" | "write";
  definition: Record<string, unknown>; parameters: Record<string, Parameter>;
  timeout_seconds: number; row_limit: number; result_limit_bytes: number;
  enabled: boolean; revision: number; dependencies?: Array<Record<string, string>>;
};
type Tab = "schema" | "operations" | "audit" | "settings";

function initialToken() {
  try { return sessionStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
}

export function ConnectorsPage() {
  const { mode } = useMode();
  const [token, setToken] = useState(initialToken);
  const [draftToken, setDraftToken] = useState("");
  const [connections, setConnections] = useState<Connection[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [tab, setTab] = useState<Tab>("operations");
  const [schema, setSchema] = useState<unknown>(null);
  const [audits, setAudits] = useState<Array<Record<string, unknown>>>([]);
  const [showConnectionForm, setShowConnectionForm] = useState(false);
  const [editingOperation, setEditingOperation] = useState<Operation | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // The server answered 503: it has no FG_CONNECTOR_ADMIN_TOKEN, so no token unlocks it.
  const [disabled, setDisabled] = useState(false);

  const request = useCallback(async <T,>(path: string, init?: RequestInit): Promise<T> => {
    const response = await fetch(`/api/flow/connectors${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        "x-connector-admin-token": token,
        ...(init?.headers ?? {}),
      },
    });
    if (response.status === 401) {
      try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignored */ }
      setToken("");
      throw new Error("The admin token expired or was rejected.");
    }
    if (response.status === 503) {
      try { sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignored */ }
      setToken("");
      setDisabled(true);
      throw new Error("Connectors are disabled on this server.");
    }
    if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
    return response.json() as Promise<T>;
  }, [token]);

  const loadConnections = useCallback(async () => {
    if (!token) return;
    const result = await request<{ connections: Connection[] }>("");
    setConnections(result.connections);
    setSelectedId((current) => current ?? result.connections[0]?.id ?? null);
  }, [request, token]);

  const loadOperations = useCallback(async (connectionId: string) => {
    const result = await request<{ operations: Operation[] }>(`/${connectionId}/operations`);
    setOperations(result.operations);
  }, [request]);

  useEffect(() => {
    loadConnections().catch((cause) => setError(String(cause)));
  }, [loadConnections]);

  useEffect(() => {
    if (!selectedId || !token) { setOperations([]); return; }
    loadOperations(selectedId).catch((cause) => setError(String(cause)));
  }, [loadOperations, selectedId, token]);

  const selected = useMemo(
    () => connections.find((connection) => connection.id === selectedId) ?? null,
    [connections, selectedId],
  );

  const activateToken = () => {
    const value = draftToken.trim();
    if (!value) return;
    try { sessionStorage.setItem(TOKEN_KEY, value); } catch { /* ignored */ }
    setDisabled(false);
    setError(null);
    setToken(value);
    setDraftToken("");
  };

  const loadTab = async (next: Tab) => {
    setTab(next);
    if (!selectedId) return;
    setBusy(true); setError(null);
    try {
      if (next === "schema") setSchema(await request(`/${selectedId}/schema`));
      if (next === "audit") {
        const result = await request<{ audits: Array<Record<string, unknown>> }>(`/audits?connection_id=${selectedId}`);
        setAudits(result.audits);
      }
    } catch (cause) { setError(String(cause)); } finally { setBusy(false); }
  };

  if (mode !== "studio") return <Navigate to="/workspace" replace />;

  if (!token) {
    return (
      <div className="h-full grid place-items-center p-6">
        <div className="glass-card w-full max-w-md p-6">
          <KeyRound className="h-8 w-8 text-brand mb-4" />
          <h1 className="text-xl font-semibold">
            {disabled ? "Connectors are disabled on this server" : "Unlock connector management"}
          </h1>
          <p className="mt-2 text-sm text-text-secondary">
            Connectors are off unless the server sets FG_CONNECTOR_ADMIN_TOKEN. Start the backend with that variable and FG_SKILL_WRITES=local, then enter the same value here.{" "}
            <Link to="/docs/connectors" className="text-brand hover:text-brand-hover">Managed connectors guide</Link>
          </p>
          <p className="mt-2 text-xs text-text-muted">
            The token stays in this browser tab&apos;s session storage and is never persisted by the app.
          </p>
          <input
            autoFocus type="password" value={draftToken}
            onChange={(event) => setDraftToken(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") activateToken(); }}
            placeholder="Admin token"
            className="mt-5 w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm focus-ring"
          />
          <button onClick={activateToken} className="mt-3 w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-fg hover:bg-brand-hover">
            Continue
          </button>
          {error && !disabled ? <p className="mt-3 text-xs text-rose-700 dark:text-rose-300">{error}</p> : null}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex min-h-0">
      <aside className="w-72 shrink-0 border-r border-border-subtle bg-bg-surface/70 p-4 overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="font-semibold">Connectors</h1>
            <p className="text-[12px] text-text-muted">Managed database tools</p>
          </div>
          <button title="New connection" onClick={() => setShowConnectionForm(true)} className="rounded-lg border border-border p-2 hover:bg-bg-muted">
            <Plus className="h-4 w-4" />
          </button>
        </div>
        <div className="space-y-2">
          {connections.map((connection) => (
            <button
              key={connection.id} onClick={() => { setSelectedId(connection.id); setTab("operations"); }}
              className={`w-full rounded-xl border p-3 text-left transition-colors ${selectedId === connection.id ? "border-brand/50 bg-brand/10" : "border-border-subtle hover:bg-bg-muted"}`}
            >
              <div className="flex items-center gap-2">
                <Database className="h-4 w-4 text-brand" />
                <span className="text-sm font-medium truncate">{connection.name}</span>
                <span className={`ml-auto h-2 w-2 rounded-full ${connection.enabled ? "bg-emerald-400" : "bg-text-muted"}`} />
              </div>
              <div className="mt-1 text-[11px] uppercase tracking-wider text-text-muted">{connection.provider} · {connection.access_level}</div>
            </button>
          ))}
          {connections.length === 0 ? <p className="text-xs text-text-muted py-6 text-center">No connections yet.</p> : null}
        </div>
      </aside>

      <main className="flex-1 min-w-0 flex flex-col">
        {error ? (
          <div className="m-4 mb-0 flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-700 dark:text-rose-300">
            <AlertTriangle className="h-4 w-4 shrink-0" /><span className="whitespace-pre-wrap">{error}</span>
          </div>
        ) : null}
        {selected ? (
          <>
            <header className="px-6 pt-5 border-b border-border-subtle">
              <div className="flex items-center gap-3">
                <div className="rounded-xl bg-brand/10 p-2.5"><Database className="h-5 w-5 text-brand" /></div>
                <div>
                  <h2 className="font-semibold">{selected.name}</h2>
                  <p className="text-xs text-text-muted">Secrets configured · values never returned by the API</p>
                </div>
                <button
                  disabled={busy} onClick={async () => {
                    setBusy(true); setError(null);
                    try { await request(`/${selected.id}/test`, { method: "POST", body: "{}" }); }
                    catch (cause) { setError(String(cause)); } finally { setBusy(false); }
                  }}
                  className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-bg-muted"
                ><Unplug className="h-3.5 w-3.5" /> Test connection</button>
              </div>
              <nav className="mt-5 flex gap-5">
                {(["schema", "operations", "audit", "settings"] as Tab[]).map((item) => (
                  <button key={item} onClick={() => loadTab(item)} className={`border-b-2 pb-2.5 text-xs capitalize ${tab === item ? "border-brand text-text-primary" : "border-transparent text-text-muted"}`}>
                    {item}
                  </button>
                ))}
              </nav>
            </header>
            <section className="flex-1 min-h-0 overflow-auto p-6">
              {tab === "operations" ? (
                <OperationsPanel
                  connection={selected} operations={operations} request={request}
                  onRefresh={() => loadOperations(selected.id)}
                  onEdit={setEditingOperation} onError={setError}
                />
              ) : null}
              {tab === "schema" ? <div className="space-y-3"><div className="flex items-center gap-2"><p className="text-xs text-text-muted">Metadata only; row values are never returned.</p>{selected.provider === "mongodb" ? <button onClick={async () => { try { setSchema(await request(`/${selected.id}/schema?infer_fields=true`)); } catch (cause) { setError(String(cause)); } }} className="rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-bg-muted">Explicitly infer field names/types</button> : null}</div><JsonPanel value={schema ?? { hint: "Select Schema again to load target metadata." }} /></div> : null}
              {tab === "audit" ? <AuditPanel audits={audits} /> : null}
              {tab === "settings" ? (
                <SettingsPanel connection={selected} request={request} onRefresh={loadConnections} onError={setError} />
              ) : null}
            </section>
          </>
        ) : (
          <div className="flex-1 grid place-items-center text-sm text-text-muted">Create a connection to expose named, typed operations.</div>
        )}
      </main>

      <ConnectionDialog open={showConnectionForm} request={request} onClose={() => setShowConnectionForm(false)} onCreated={async () => { setShowConnectionForm(false); await loadConnections(); }} onError={setError} />
      {selected ? (
        <OperationDialog
          open={editingOperation !== undefined}
          connection={selected} operation={editingOperation ?? null} request={request}
          onClose={() => setEditingOperation(undefined)}
          onSaved={async () => { setEditingOperation(undefined); await loadOperations(selected.id); }}
          onError={setError}
        />
      ) : null}
    </div>
  );
}

type Requester = <T>(path: string, init?: RequestInit) => Promise<T>;

function OperationsPanel({ connection, operations, request, onRefresh, onEdit, onError }: {
  connection: Connection; operations: Operation[]; request: Requester; onRefresh: () => Promise<void>;
  onEdit: (operation: Operation | null) => void; onError: (message: string | null) => void;
}) {
  const remove = async (operation: Operation) => {
    if (!window.confirm(`Delete ${operation.display_name}?`)) return;
    try { await request(`/operations/${operation.id}`, { method: "DELETE" }); await onRefresh(); }
    catch (cause) { onError(String(cause)); }
  };
  return (
    <div className="max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <div><h3 className="text-sm font-medium">Named operations</h3><p className="text-xs text-text-muted">Stable tools; display-label changes do not break skills.</p></div>
        <button onClick={() => onEdit(null)} className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs font-medium text-brand-fg"><Plus className="h-3.5 w-3.5" /> New operation</button>
      </div>
      <div className="space-y-2">
        {operations.map((operation) => (
          <div key={operation.id} className="rounded-xl border border-border-subtle bg-bg-surface p-4 flex items-center gap-4">
            <div className={`rounded-lg p-2 ${operation.mode === "write" ? "bg-amber-500/10 text-amber-400" : "bg-emerald-500/10 text-emerald-400"}`}><ShieldCheck className="h-4 w-4" /></div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2"><span className="text-sm font-medium">{operation.display_name}</span><span className="rounded bg-bg-muted px-1.5 py-0.5 text-[10px] uppercase">{operation.mode}</span></div>
              <code className="text-[12px] text-brand">{operation.tool_name}</code>
              {operation.dependencies?.length ? <p className="text-[11px] text-text-muted">Used by {operation.dependencies.map((item) => item.skill_name).join(", ")}</p> : null}
            </div>
            <button onClick={() => onEdit(operation)} className="rounded-lg border border-border px-2.5 py-1.5 text-xs hover:bg-bg-muted">Edit</button>
            <button onClick={() => remove(operation)} className="rounded-lg p-2 text-rose-400 hover:bg-rose-500/10"><Trash2 className="h-4 w-4" /></button>
          </div>
        ))}
        {operations.length === 0 ? <p className="rounded-xl border border-dashed border-border p-8 text-center text-xs text-text-muted">Define a capped read or explicitly confirmed write operation.</p> : null}
      </div>
    </div>
  );
}

function ConnectionDialog({ open, request, onClose, onCreated, onError }: { open: boolean; request: Requester; onClose: () => void; onCreated: () => void; onError: (message: string) => void }) {
  const [provider, setProvider] = useState("postgresql"); const [name, setName] = useState("");
  const [credential, setCredential] = useState(""); const [access, setAccess] = useState("read"); const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try { await request("", { method: "POST", body: JSON.stringify({ provider, name, credential, access_level: access }) }); await onCreated(); }
    catch (cause) { onError(String(cause)); } finally { setCredential(""); setSaving(false); }
  };
  return <Dialog open={open} title="New database connection" onClose={onClose}>
    <div className="space-y-3">
      <Label text="Provider"><select value={provider} onChange={(event) => setProvider(event.target.value)} className="field"><option value="postgresql">PostgreSQL</option><option value="mongodb">MongoDB</option></select></Label>
      <Label text="Display name"><input className="field" value={name} onChange={(event) => setName(event.target.value)} placeholder="Billing" /></Label>
      <Label text="Credential URI (write-only)"><input className="field font-mono" type="password" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder={provider === "postgresql" ? "postgresql://…?sslmode=verify-full or env:BILLING_DB_URI" : "mongodb+srv://… or env:CRM_MONGO_URI"} /></Label>
      <Label text="Access"><select className="field" value={access} onChange={(event) => setAccess(event.target.value)}><option value="read">Read only</option><option value="read_write">Read and write</option></select></Label>
      <button disabled={saving || !name || !credential} onClick={save} className="w-full rounded-lg bg-brand p-2 text-sm text-brand-fg disabled:opacity-50">{saving ? "Saving…" : "Create connection"}</button>
    </div>
  </Dialog>;
}

function OperationDialog({ open, connection, operation, request, onClose, onSaved, onError }: {
  open: boolean; connection: Connection; operation: Operation | null; request: Requester; onClose: () => void; onSaved: () => void; onError: (message: string) => void;
}) {
  const postgres = connection.provider === "postgresql";
  const [name, setName] = useState(operation?.name ?? "");
  const [displayName, setDisplayName] = useState(operation?.display_name ?? "");
  const [mode, setMode] = useState<"read" | "write">(operation?.mode ?? "read");
  const defaultKind = postgres ? "select" : "find_many";
  const [kind, setKind] = useState(operation?.kind ?? defaultKind);
  const [definitionText, setDefinitionText] = useState(() => postgres
    ? String(operation?.definition.sql ?? "SELECT * FROM public.table_name WHERE id = %(id)s")
    : JSON.stringify(operation?.definition ?? { collection: "customers", filter: { id: "{{id}}" } }, null, 2));
  const [parametersText, setParametersText] = useState(JSON.stringify(operation?.parameters ?? { id: { type: "string", required: true } }, null, 2));
  const [testParameters, setTestParameters] = useState("{}");
  const [feedback, setFeedback] = useState<unknown>(null); const [saving, setSaving] = useState(false);
  const body = () => ({
    name, display_name: displayName || name, kind, mode,
    definition: postgres ? { sql: definitionText } : JSON.parse(definitionText),
    parameters: JSON.parse(parametersText), timeout_seconds: 10, row_limit: 100,
    result_limit_bytes: 1_048_576, enabled: operation?.enabled ?? true,
  });
  const validate = async (executeRead: boolean) => {
    try {
      if (!operation) {
        setFeedback(await request(`/${connection.id}/operations/validate`, { method: "POST", body: JSON.stringify(body()) }));
      } else if (executeRead) {
        setFeedback(await request(`/operations/${operation.id}/test`, { method: "POST", body: JSON.stringify({ parameters: JSON.parse(testParameters) }) }));
      } else setFeedback(await request(`/operations/${operation.id}/validate`, { method: "POST", body: "{}" }));
    } catch (cause) { onError(String(cause)); }
  };
  const save = async () => {
    setSaving(true);
    try {
      const path = operation ? `/operations/${operation.id}` : `/${connection.id}/operations`;
      await request(path, { method: operation ? "PUT" : "POST", body: JSON.stringify(body()) }); await onSaved();
    } catch (cause) { onError(String(cause)); } finally { setSaving(false); }
  };
  const kinds = postgres ? ["select", "insert", "update", "delete"] : ["find_one", "find_many", "aggregate", "insert_one", "update_one", "update_many", "delete_one", "delete_many"];
  return <Dialog open={open} title={operation ? `Edit ${operation.display_name}` : "New managed operation"} onClose={onClose} wide>
    <div className="grid grid-cols-2 gap-3">
      <Label text="Stable name"><input className="field font-mono" value={name} onChange={(event) => setName(event.target.value)} /></Label>
      <Label text="Display name"><input className="field" value={displayName} onChange={(event) => setDisplayName(event.target.value)} /></Label>
      <Label text="Kind"><select className="field" value={kind} onChange={(event) => { const next = event.target.value; setKind(next); setMode(["select", "find_one", "find_many", "aggregate"].includes(next) ? "read" : "write"); }}>{kinds.map((item) => <option key={item}>{item}</option>)}</select></Label>
      <Label text="Safety mode"><input className="field" readOnly value={mode} /></Label>
    </div>
    <div className="mt-3"><div className="text-[11px] uppercase text-text-muted mb-1">{postgres ? "Parameterized SQL" : "JSON / EJSON template"}</div>
      <Suspense fallback={<div className="h-56 rounded-lg bg-bg-muted animate-pulse" />}>
        <MonacoEditor height="230px" language={postgres ? "sql" : "json"} value={definitionText} onChange={(value) => setDefinitionText(value ?? "")} theme="vs-dark" options={{ minimap: { enabled: false }, fontSize: 12, scrollBeyondLastLine: false, automaticLayout: true }} />
      </Suspense>
    </div>
    <div className="mt-3 grid grid-cols-2 gap-3">
      <Label text="Typed parameters (JSON)"><textarea rows={6} className="field font-mono" value={parametersText} onChange={(event) => setParametersText(event.target.value)} /></Label>
      <Label text="Preview parameter values (JSON)"><textarea rows={6} className="field font-mono" value={testParameters} onChange={(event) => setTestParameters(event.target.value)} /></Label>
    </div>
    {feedback ? <pre className="mt-3 max-h-32 overflow-auto rounded-lg bg-bg-muted p-2 text-[11px]">{JSON.stringify(feedback, null, 2)}</pre> : null}
    <div className="mt-4 flex justify-end gap-2">
      <button onClick={() => validate(false)} className="rounded-lg border border-border px-3 py-2 text-xs">Validate</button>
      {operation ? <button onClick={() => validate(true)} className="rounded-lg border border-border px-3 py-2 text-xs">{mode === "read" ? "Run capped preview" : "Validate write only"}</button> : null}
      <button disabled={saving} onClick={save} className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-xs text-brand-fg"><Save className="h-3.5 w-3.5" /> Save</button>
    </div>
  </Dialog>;
}

function SettingsPanel({ connection, request, onRefresh, onError }: { connection: Connection; request: Requester; onRefresh: () => Promise<void>; onError: (message: string) => void }) {
  const [credential, setCredential] = useState("");
  const rotate = async () => { try { await request(`/${connection.id}`, { method: "PUT", body: JSON.stringify({ credential }) }); setCredential(""); await onRefresh(); } catch (cause) { onError(String(cause)); } };
  return <div className="max-w-xl space-y-5">
    <div className="rounded-xl border border-border-subtle p-4"><h3 className="text-sm font-medium">Rotate credential</h3><p className="mt-1 text-xs text-text-muted">The existing URI is intentionally not retrievable. Saving closes the provider pool.</p><input type="password" className="field mt-3" value={credential} onChange={(event) => setCredential(event.target.value)} placeholder="New URI or env:VARIABLE" /><button disabled={!credential} onClick={rotate} className="mt-2 rounded-lg bg-brand px-3 py-2 text-xs text-brand-fg disabled:opacity-50">Rotate and invalidate pool</button></div>
    <div className="rounded-xl border border-border-subtle p-4"><div className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-emerald-400" /><span className="text-sm">Credential configured</span></div><p className="mt-1 text-xs text-text-muted">Mode: {connection.credential_mode}. TLS verification remains enabled by default.</p></div>
  </div>;
}

function AuditPanel({ audits }: { audits: Array<Record<string, unknown>> }) {
  return <div className="max-w-6xl overflow-x-auto"><table className="w-full text-left text-xs"><thead className="text-text-muted"><tr>{["Started", "Tool", "Skill / action", "Status", "Duration", "Rows / affected", "Parameters"].map((head) => <th key={head} className="border-b border-border p-2 font-medium">{head}</th>)}</tr></thead><tbody>{audits.map((audit) => <tr key={String(audit.execution_id)} className="align-top"><td className="border-b border-border-subtle p-2 whitespace-nowrap">{String(audit.started_at)}</td><td className="border-b border-border-subtle p-2 font-mono text-brand">{String(audit.tool_name)}</td><td className="border-b border-border-subtle p-2">{String(audit.skill_name ?? "admin")} / {String(audit.action_name ?? "preview")}</td><td className="border-b border-border-subtle p-2">{String(audit.status)}</td><td className="border-b border-border-subtle p-2">{Number(audit.duration_ms).toFixed(1)}ms</td><td className="border-b border-border-subtle p-2">{String(audit.row_count)} / {String(audit.affected_count)}</td><td className="border-b border-border-subtle p-2"><code>{JSON.stringify(audit.parameters)}</code></td></tr>)}</tbody></table>{audits.length === 0 ? <p className="p-8 text-center text-text-muted">No executions recorded.</p> : null}</div>;
}

function JsonPanel({ value }: { value: unknown }) { return <pre className="max-w-5xl overflow-auto rounded-xl border border-border-subtle bg-bg-surface p-4 text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>; }

function Dialog({ open, title, onClose, wide, children }: { open: boolean; title: string; onClose: () => void; wide?: boolean; children: ReactNode }) {
  return <AppDialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }} className={`max-h-[92dvh] overflow-auto p-5 ${wide ? "max-w-4xl" : "max-w-md"}`}><div className="mb-4 flex items-center justify-between"><AppDialogTitle className="font-semibold">{title}</AppDialogTitle><button onClick={onClose} className="text-xs text-text-muted hover:text-text-primary">Close</button></div>{children}</AppDialog>;
}
function Label({ text, children }: { text: string; children: ReactNode }) { return <label className="block"><span className="mb-1 block text-[11px] uppercase tracking-wider text-text-muted">{text}</span>{children}</label>; }

import { useEffect, useRef, useState } from "react";

// Voice input: dictate into the message box with the browser's own speech
// recognition (Chrome, Edge, Safari). It only ever fills the box — you still
// press Send — and the button is hidden where the browser has no recognizer.
function useDictation(onText) {
  const [listening, setListening] = useState(false);
  const rec = useRef(null);
  const Recognition =
    typeof window !== "undefined" && (window.SpeechRecognition || window.webkitSpeechRecognition);

  const toggle = () => {
    if (rec.current) return rec.current.stop();
    const r = new Recognition();
    r.lang = navigator.language || "en-US";
    r.interimResults = true;
    r.onresult = (e) =>
      onText(Array.from(e.results).map((x) => x[0].transcript).join("").trim());
    r.onend = () => { rec.current = null; setListening(false); };
    r.onerror = r.onend;
    rec.current = r;
    r.start();
    setListening(true);
  };
  return { supported: Boolean(Recognition), listening, toggle };
}

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// One typed widget, generated from the flow's pending field. The form IS the
// flow's schema — enum -> dropdown, boolean -> toggle, number -> number input.
function Widget({ widget, onSubmit, disabled }) {
  const [value, setValue] = useState(
    widget.kind === "toggle" ? "true" : widget.kind === "select" ? (widget.options?.[0] ?? "") : ""
  );
  return (
    <div className="widget glass">
      <label className="widget-label">{widget.label}</label>
      {(widget.runs || []).map((run) => (
        <div key={run.title} className="runs">
          <div className="runs-head">Saying yes runs this, once: <b>{run.title}</b></div>
          {(run.inputs || []).map((item) => (
            <div key={item.label} className="runs-row"><span>{item.label}</span><span>{item.shown}</span></div>
          ))}
        </div>
      ))}
      {widget.kind === "select" && (
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          {(widget.options || []).map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
      )}
      {widget.kind === "toggle" && (
        <select value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="true">Yes</option>
          <option value="false">No</option>
        </select>
      )}
      {widget.kind === "number" && (
        <input type="number" value={value} min={widget.min ?? undefined}
          max={widget.max ?? undefined} onChange={(e) => setValue(e.target.value)} />
      )}
      {widget.kind === "text" && (
        <input type="text" value={value} placeholder="Type your answer…"
          onChange={(e) => setValue(e.target.value)} />
      )}
      <button className="btn-primary" disabled={disabled}
        onClick={() => onSubmit(widget.name, value)}>Submit</button>
      <div className="gate-note">Validated in code · the form is the flow’s schema</div>
    </div>
  );
}

function Chat() {
  const [messages, setMessages] = useState([
    { role: "bot", text: "Hi! Tell me what you'd like to do — try “file an expense”." },
  ]);
  const [widget, setWidget] = useState(null);
  const [convo, setConvo] = useState(null);
  const [input, setInput] = useState("");
  const voice = useDictation(setInput);
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, widget]);

  function apply(resp) {
    setConvo(resp.state);
    setWidget(resp.widget || null);
    if (resp.messages?.length) {
      setMessages((m) => [...m, ...resp.messages.map((t) => ({ role: "bot", text: t }))]);
    }
  }

  async function send(text) {
    if (!text.trim() || busy) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setInput("");
    setBusy(true);
    try { apply(await api("/api/chat", { message: text, state: convo })); }
    catch (e) { setMessages((m) => [...m, { role: "bot", text: "⚠ " + e.message }]); }
    finally { setBusy(false); }
  }

  async function submitWidget(name, value) {
    setMessages((m) => [...m, { role: "user", text: `${name}: ${value}` }]);
    setWidget(null);
    setBusy(true);
    try { apply(await api("/api/submit", { fields: { [name]: value }, state: convo })); }
    catch (e) { setMessages((m) => [...m, { role: "bot", text: "⚠ " + e.message }]); }
    finally { setBusy(false); }
  }

  return (
    <div className="chat">
      <div className="messages">
        {messages.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>{m.text}</div>
        ))}
        {widget && <Widget widget={widget} onSubmit={submitWidget} disabled={busy} />}
        {busy && <div className="bubble bot typing">…</div>}
        <div ref={endRef} />
      </div>
      <div className="composer glass">
        <input value={input} placeholder="Message…" disabled={busy}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send(input)} />
        {voice.supported && (
          <button className={`btn-mic ${voice.listening ? "listening" : ""}`} disabled={busy}
            aria-pressed={voice.listening} title={voice.listening ? "Stop" : "Speak your message"}
            aria-label={voice.listening ? "Stop voice input" : "Speak your message"}
            onClick={voice.toggle}>{voice.listening ? "■" : "🎤"}</button>
        )}
        <button className="btn-primary" disabled={busy} onClick={() => send(input)}>Send</button>
      </div>
    </div>
  );
}

// The flow view. NOT a mockup: it renders GET /api/flow/outline, which the
// server derives from your own server/skills/ — the same questions, conditions
// and tools the engine will run. Read-only by design (SKILL.md is the thing
// you edit), and it works with no LLM key.
const VERBS = ["ask", "say", "do", "confirm", "pick", "show", "check", "chat", "widget", "code"];
const LABELS = {
  ask: "ASK", say: "SAY", do: "DO", confirm: "CONFIRM", pick: "PICK", show: "SHOW",
  check: "CHECK", chat: "CHAT", widget: "WIDGET", code: "CODE",
};

// A `when:` with its own `steps:` is a block. Show each step in it, under that condition.
function flatten(steps, when) {
  return (steps || []).flatMap((step) => {
    const guard = [when, step.when].filter(Boolean).join(" and ") || undefined;
    return Array.isArray(step.steps) ? flatten(step.steps, guard) : [{ ...step, when: guard }];
  });
}

function Step({ step }) {
  const verb = VERBS.find((v) => v in step) ?? Object.keys(step)[0];
  const raw = step[verb];
  // `ask: { amount: "How much?" }` keeps the answer under a name.
  const named = raw && typeof raw === "object" && !Array.isArray(raw) ? Object.entries(raw)[0] : null;
  const title = named ? named[1] : typeof raw === "string" ? raw : JSON.stringify(raw);
  const guard = step.when;
  const rules = step.check && typeof step.check === "object" ? Object.keys(step.check) : [];
  return (
    <div className={`node ${guard ? "gate" : ""}`}>
      <div className="node-lab">
        {LABELS[verb] ?? verb.toUpperCase()}
        {guard && <span className="tag">gated</span>}
      </div>
      <div className="node-ttl">{String(title)}</div>
      {named && verb === "ask" && <div className="node-meta">kept as <code>{named[0]}</code></div>}
      {step.choices && <div className="node-meta">one of: {step.choices.join(", ")}</div>}
      {rules.map((rule) => <div key={rule} className="node-meta">must hold: <code>{rule}</code></div>)}
      {guard && <div className="node-meta">only when <code>{guard}</code></div>}
    </div>
  );
}

function FlowView() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    fetch("/api/flow/outline")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`/api/flow/outline -> ${r.status}`))))
      .then(setData)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="banner glass">⚠ {error}</div>;
  if (!data) return <div className="bubble bot typing">…</div>;
  const skills = data.skills || [];
  if (!skills.length) {
    return (
      <div className="flow-note glass">
        <h3>No skills yet</h3>
        <p className="dim">
          Add <code>server/skills/&lt;name&gt;/SKILL.md</code> with a <code>steps:</code> list
          and restart the server.
        </p>
      </div>
    );
  }

  return (
    <div className="flowview">
      <div className="flow-canvas glass">
        {skills.map((s) => {
          const steps = flatten(s.steps);
          return (
            <div key={s.name} className="skill-outline">
              <div className="canvas-head">{s.name} · {steps.length} steps</div>
              {steps.map((step, i) => (
                <div key={i}>
                  <Step step={step} />
                  {i < steps.length - 1 && <div className="conn" />}
                </div>
              ))}
              {!steps.length && (
                <div className="node-meta">{s.note || "no steps found — does SKILL.md have a steps: list?"}</div>
              )}
            </div>
          );
        })}
      </div>
      <div className="flow-note glass">
        <h3>Read from your skill file</h3>
        <p>
          Every box above comes from <code>server/skills/</code>: the questions,
          the conditions, and the tools behind them. Change the file and
          restart the server: this view changes with it.
        </p>
        <p className="dim">
          A gated step runs only when its condition holds. That is a line in
          the file that the engine runs, not an instruction the model is asked
          to remember.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("chat");
  const [health, setHealth] = useState(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then(setHealth)
      .catch((e) => setHealth({ ok: false, llm_error: `cannot reach the server (${e.message})` }));
  }, []);

  return (
    <div className="app">
      <header className="topbar glass">
        <div className="brand"><span className="logo-dot" /> __APP_NAME__</div>
        <nav className="tabs">
          <button className={tab === "chat" ? "on" : ""} onClick={() => setTab("chat")}>Chat</button>
          <button className={tab === "flow" ? "on" : ""} onClick={() => setTab("flow")}>Flow</button>
        </nav>
        <span className="badge">Deterministic ✓</span>
      </header>
      {health && !health.ok && (
        <div className="banner glass">
          <strong>Chat is not ready.</strong> {health.llm_error || "no model configured."}
          {" "}The rest of the UI works — reload once it is fixed.
        </div>
      )}
      {health && Object.keys(health.skill_errors || {}).length > 0 && (
        <div className="banner glass">
          <strong>A skill did not load</strong> and is unreachable:
          <ul>
            {Object.entries(health.skill_errors).map(([name, why]) => (
              <li key={name}><code>{name}</code> — {why}</li>
            ))}
          </ul>
        </div>
      )}
      <main>{tab === "chat" ? <Chat /> : <FlowView />}</main>
    </div>
  );
}

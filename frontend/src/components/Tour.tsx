import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft, ArrowRight, X } from "lucide-react";
import { useMode } from "../hooks/useMode";

/**
 * Guided tour. Zero dependencies: a dimmed backdrop, an optional
 * spotlight on a `[data-tour="..."]` anchor, and a positioned card. Steps can
 * change routes; the card waits a beat for the page to render.
 *
 * Never starts on its own. It runs when something dispatches
 * `window.dispatchEvent(new Event("aigenos:tour"))` — the landing's tour link
 * and the Overview's tour button do exactly that.
 */

const SEEN_KEY = "aigenos_tour_done";

type Step = {
  route: string;
  anchor?: string; // data-tour attribute value to spotlight
  title: string;
  body: string;
};

const STEPS: Step[] = [
  {
    route: "/map",
    title: "Welcome to aigenos / flow",
    body: "Deterministic workflows with an agentic feel: the LLM understands your users; plain rules decide what actually happens. This 60-second tour shows where everything lives.",
  },
  {
    route: "/map",
    anchor: "map-cards",
    title: "The Overview",
    body: "This is the Studio's home. Each card says who a page is for and when to use it. Come back here whenever you're lost.",
  },
  {
    route: "/canvas",
    anchor: "canvas-palette",
    title: "Canvas — build the workflow",
    body: "Ask, Say, Widget, Tool, Link, Confirm, Approval, Condition, Repeat, Chat: open a skill, add steps from the left rail and edit each one in the inspector. The skill is checked as you edit, and one that does not load is not saved: the message is on the step it is about and says what to change.",
  },
  {
    route: "/widgets",
    title: "Widgets — design each step's UI",
    body: "Open a widget to style it: click any element and set typography, colors and layout. A Canvas step renders a widget by id (widget: your_widget); a step without one gets an auto-widget.",
  },
  {
    route: "/tools",
    title: "Tools — what a step can run",
    body: "Every function your skills can name: what it needs, whether it changes anything, and which skills use it. Try one with your own values. A developer writes tools here; a Do step then names one.",
  },
  {
    route: "/flow",
    title: "Flow — try what you built",
    body: "The real product experience: chat, grids, forms, timelines. Ask a side question mid-workflow and you'll get a real answer — then the flow brings you right back to where you were.",
  },
  {
    route: "/chat",
    title: "Playground — inspect the wire",
    body: "Run any of your skills over AG-UI, A2UI or MCP-UI and type to it like a user. Every frame the server sends is shown under the chat: exactly what your own frontend would receive.",
  },
  {
    route: "/workspace",
    title: "Workspace — the simple view",
    body: "Your skills as a clean task UI: run one, or chat and let the agent pick.",
  },
  {
    route: "/map",
    title: "That's the loop",
    body: "Build a skill on the Canvas, then run it in the Workspace or chat with the agent on the Flow page. Restart this tour from the Overview any time.",
  },
];

type Rect = { top: number; left: number; width: number; height: number };

export function Tour() {
  const [step, setStep] = useState<number | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;

  const { setMode } = useMode();

  // The tour walks the Studio tabs, so switch to Studio mode to show them.
  const start = useCallback(() => {
    setMode("studio");
    setStep(0);
  }, [setMode]);

  // Runs on request only — nothing auto-starts. `?tour=0` (headless captures,
  // CI, embeds) still records the tour as seen; no code path starts from it.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("tour") === "0") {
      try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* ignore */ }
    }
    const handler = () => start();
    window.addEventListener("aigenos:tour", handler);
    return () => window.removeEventListener("aigenos:tour", handler);
  }, [start]);

  // On step change: navigate, then locate the anchor after render.
  useEffect(() => {
    if (step === null) return;
    const s = STEPS[step];
    if (window.location.pathname !== s.route) navigateRef.current(s.route);
    setRect(null);
    const t = window.setTimeout(() => {
      if (!s.anchor) return;
      const el = document.querySelector(`[data-tour="${s.anchor}"]`);
      if (el) {
        const r = el.getBoundingClientRect();
        setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
      }
    }, 350);
    return () => window.clearTimeout(t);
  }, [step]);

  const finish = useCallback(() => {
    try { localStorage.setItem(SEEN_KEY, "1"); } catch { /* ignore */ }
    setStep(null);
  }, []);

  // Escape closes.
  useEffect(() => {
    if (step === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step, finish]);

  if (step === null) return null;
  const s = STEPS[step];
  const last = step === STEPS.length - 1;

  // Card placement: under the spotlight when there is one, else centered.
  const cardStyle: React.CSSProperties = rect
    ? {
        position: "fixed",
        top: Math.min(rect.top + rect.height + 12, window.innerHeight - 220),
        left: Math.min(Math.max(rect.left, 16), window.innerWidth - 400),
        zIndex: 62,
      }
    : {
        position: "fixed",
        top: "50%",
        left: "50%",
        transform: "translate(-50%, -50%)",
        zIndex: 62,
      };

  return (
    <div data-tour-overlay className="fixed inset-0 z-[60]">
      {/* backdrop; the spotlight is a hole punched with a huge box-shadow */}
      {rect ? (
        <div
          className="fixed rounded-xl ring-2 ring-brand/80 transition-all duration-200"
          style={{
            top: rect.top - 4,
            left: rect.left - 4,
            width: rect.width + 8,
            height: rect.height + 8,
            boxShadow: "0 0 0 9999px rgb(0 0 0 / 0.55)",
            zIndex: 61,
            pointerEvents: "none",
          }}
        />
      ) : (
        <div className="fixed inset-0 bg-black/55" style={{ zIndex: 61 }} />
      )}

      <div
        style={cardStyle}
        className="w-[380px] max-w-[calc(100vw-32px)] rounded-xl border border-border-subtle bg-bg-elevated shadow-card p-4"
      >
        <div className="flex items-start gap-2">
          <div className="flex-1">
            <div className="text-[11px] uppercase tracking-wider text-text-muted">
              {step + 1} / {STEPS.length}
            </div>
            <h3 className="mt-0.5 text-sm font-semibold text-text-primary">{s.title}</h3>
          </div>
          <button
            onClick={finish}
            aria-label="Skip tour"
            className="rounded-md p-1 text-text-muted hover:text-text-primary hover:bg-bg-muted"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
        <p className="mt-2 text-xs leading-relaxed text-text-secondary">{s.body}</p>
        <div className="mt-3 flex items-center gap-2">
          <div className="flex gap-1">
            {STEPS.map((_, i) => (
              <span
                key={i}
                className={`h-1 rounded-full transition-all ${i === step ? "w-4 bg-brand" : "w-1.5 bg-bg-muted"}`}
              />
            ))}
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            {step > 0 && (
              <button
                onClick={() => setStep(step - 1)}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-muted"
              >
                <ArrowLeft className="h-3 w-3" /> Back
              </button>
            )}
            <button
              onClick={() => (last ? finish() : setStep(step + 1))}
              className="inline-flex items-center gap-1 rounded-lg bg-brand text-brand-fg px-3 py-1 text-xs font-medium hover:bg-brand-hover"
            >
              {last ? "Done" : "Next"} {!last && <ArrowRight className="h-3 w-3" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

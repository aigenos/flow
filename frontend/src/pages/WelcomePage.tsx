import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight, BookOpen, Boxes, Database, GitBranch, Github, History, Inbox, LayoutGrid,
  Library, MessageSquare, Play, ScrollText, ShieldCheck, Sparkles,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { useMode } from "../hooks/useMode";
import { IS_PUBLIC_SITE, QUICKSTART_PATH } from "../lib/publicSite";
import { useTheme } from "../hooks/useTheme";

export const WELCOME_SEEN_KEY = "aigenos-welcome-seen";

function markSeen() {
  try { localStorage.setItem(WELCOME_SEEN_KEY, "1"); } catch { /* ignore */ }
}

/** The five kinds of step, and the block that repeats them: the whole of what a flow can do. */
const KINDS = ["ask", "say", "do", "confirm", "chat", "repeat"];
/** A real skill, step by step (the transfer at the top of the README). */
const CHAIN = ["ask", "ask", "ask", "say", "confirm", "do", "say"];
const MARQUEE = [
  ...KINDS, "when", "check",
  "AG-UI", "A2UI", "MCP-UI", "MCP tools", "LangGraph", "Anthropic", "OpenAI", "Gemini", "Ollama",
];

/** One node per step of that skill. The pulse visits each node once per
 *  cycle; the step count, the row height and the cycle length together decide
 *  where it lands, so they travel as one value. The stylesheet's own timing
 *  was written for a different length, so the step count and duration are set
 *  inline from these. */
const CHAIN_ROWS = CHAIN.length;
const CHAIN_ROW = 68;
const CHAIN_STEP_S = 0.9;

/**
 * The background mark: a flow drawn at a distance — a spine, a node per verb,
 * and a pulse that steps between them. Two of these flank the hero with no
 * offset between them, so both runs execute the same schedule at the same
 * time. Decorative only.
 */
function FlowChain({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={`wel-chain absolute ${className}`}
      style={{
        height: CHAIN_ROWS * CHAIN_ROW,
        ["--wel-chain-travel" as string]: `${CHAIN_ROWS * CHAIN_ROW}px`,
      }}
    >
      <span className="wel-chain-spine" />
      {CHAIN.map((_step, i) => (
        <span key={i} className="wel-chain-node" style={{ top: i * CHAIN_ROW + CHAIN_ROW / 2 }} />
      ))}
      <span
        className="wel-chain-pulse"
        style={{
          top: CHAIN_ROW / 2,
          animationDuration: `${CHAIN_ROWS * CHAIN_STEP_S}s`,
          animationTimingFunction: `steps(${CHAIN_ROWS}, end)`,
        }}
      />
    </div>
  );
}

/** Fades its children up the first time they scroll into view. Reduced-motion
 *  users get the resting state immediately — the CSS handles that half. */
function Reveal({ children, delay = 0, className = "" }: {
  children: React.ReactNode; delay?: number; className?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el || shown) return;
    const io = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        setShown(true);
        io.disconnect();
      },
      { rootMargin: "0px 0px -10% 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [shown]);

  return (
    <div
      ref={ref}
      className={`wel-reveal ${className}`}
      data-shown={shown || undefined}
      style={delay ? { transitionDelay: `${delay}ms` } : undefined}
    >
      {children}
    </div>
  );
}

/** Theme-matched Studio screenshot in a browser frame. */
function Shot({ base, url, alt }: { base: string; url: string; alt: string }) {
  const { theme } = useTheme();
  return (
    <div className="wel-halo">
    <div className="wel-frame">
      <div className="flex items-center gap-2 border-b border-border-subtle bg-bg-elevated/80 px-4 py-2.5">
        <span className="flex gap-1.5">
          <i className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <i className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <i className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        </span>
        <span className="mx-auto truncate rounded-md border border-border-subtle bg-bg-muted/70 px-3 py-0.5 font-mono text-[11px] text-text-muted">
          {url}
        </span>
      </div>
      <img
        src={`/shots/${base}-${theme}.png`}
        alt={alt}
        width={1440}
        height={900}
        className="block w-full select-none"
        draggable={false}
        loading="lazy"
      />
    </div>
    </div>
  );
}

/** A capability, as a hairline-topped column: icon, term, one sentence.
 *  Rules instead of boxes — the page gets its structure from alignment. */
function Feature({ icon: Icon, term, children }: {
  icon: React.ComponentType<{ className?: string }>; term: string; children: React.ReactNode;
}) {
  return (
    <div className="border-t border-border-subtle pt-4">
      <Icon className="h-4 w-4 text-brand" />
      <h3 className="mt-3 text-[15px] font-semibold tracking-tight text-text-primary">{term}</h3>
      <p className="mt-1.5 text-[15px] leading-relaxed text-text-secondary">{children}</p>
    </div>
  );
}

/** Editorial section head: numeral + kicker, title left, lede set to the
 *  right on its baseline. Asymmetry is what keeps the page from reading as a
 *  stack of identical centered blocks. */
function SectionHead({ n, kicker, title, lede }: {
  n: string; kicker: string; title: React.ReactNode; lede: string;
}) {
  return (
    <div className="grid gap-x-12 gap-y-5 border-t border-border pt-8 md:grid-cols-[1fr_minmax(0,25rem)]">
      <div>
        <div className="flex items-baseline gap-3">
          <span className="font-mono text-[13px] tabular-nums text-text-muted">{n}</span>
          <span className="text-[13px] font-semibold uppercase tracking-[0.18em] text-brand">{kicker}</span>
        </div>
        <h2 className="mt-4 max-w-[14ch] text-[2.5rem] font-semibold leading-[1.06] tracking-[-0.025em] sm:text-5xl">
          {title}
        </h2>
      </div>
      <p className="text-[15px] leading-relaxed text-text-secondary md:self-end md:pb-1.5">{lede}</p>
    </div>
  );
}

/**
 * First-run landing — the product page, inside the product. Shown once at `/`
 * until the user picks a door; every CTA marks it seen, and the Overview's
 * footer link brings it back. Full-bleed: the app Nav is hidden here.
 */
export function WelcomePage() {
  const navigate = useNavigate();
  const { setMode } = useMode();
  const { theme } = useTheme();
  // The page scrolls in its own container, so the sticky header has to watch
  // that element rather than the window.
  const [scrolled, setScrolled] = useState(false);

  const enterStudio = (to: string, tour = false) => {
    markSeen();
    // On aigenos.io the Studio has no backend behind it, so "build" means
    // "here is how to run it yourself" — the quickstart, not an empty shell.
    if (IS_PUBLIC_SITE) return navigate(QUICKSTART_PATH);
    setMode("studio");
    navigate(to);
    if (tour) setTimeout(() => window.dispatchEvent(new Event("aigenos:tour")), 350);
  };
  const enterWorkspace = () => {
    markSeen();
    if (IS_PUBLIC_SITE) return navigate(QUICKSTART_PATH);
    setMode("workspace");
    navigate("/workspace");
  };

  return (
    <div
      className="relative h-full overflow-y-auto overflow-x-hidden scrollbar-thin"
      onScroll={(e) => setScrolled(e.currentTarget.scrollTop > 12)}
    >
      {/* backdrop: canvas grid, one soft wash, and the flow mark flanking the
          hero — hidden below xl, where there is no margin to put it in */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-[70rem]">
        <div className="wel-grid-bg absolute inset-0" />
        <div className="wel-blob -top-32 left-1/2 h-[36rem] w-[52rem] -translate-x-1/2 bg-brand/[0.07]" />
        <div className="wel-blob wel-blob-b top-44 left-[calc(50%+6rem)] h-[28rem] w-[42rem] bg-[#bf833e]/[0.06]" />
        <FlowChain className="hidden xl:block top-[8rem] left-[calc(50%_-_34rem)]" />
        <FlowChain className="hidden xl:block top-[8rem] left-[calc(50%_+_34rem)]" />
      </div>

      {/* top bar — gains its material only once the page has moved */}
      <header
        className={`sticky top-0 z-20 border-b transition-colors duration-300 ${
          scrolled ? "border-border-subtle nav-material" : "border-transparent"
        }`}
      >
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between px-6 py-4">
          <div className="flex items-center gap-2">
            <img
              src={theme === "dark" ? "/aigenos-icon-dark.png" : "/aigenos-icon-light.png"}
              alt="" className="h-7 w-7 select-none" draggable={false}
            />
            <span className="text-[15px] font-semibold tracking-tight text-text-primary">
              aigenos <span className="text-text-muted">flow</span>
            </span>
          </div>
          <div className="flex items-center gap-1">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/docs"><BookOpen /> Docs</Link>
            </Button>
            <Button variant="ghost" size="sm" asChild>
              <a href="https://github.com/aigenos/flow" rel="noopener" target="_blank">
                <Github /> GitHub
              </a>
            </Button>
          </div>
        </div>
      </header>

      {/* hero */}
      <section className="relative z-10 mx-auto w-full max-w-5xl px-6 pt-16 text-center sm:pt-24">
        <h1 className="hero-enter hero-enter-delay-1 mx-auto mt-7 max-w-4xl text-[3.5rem] font-semibold leading-[1.02] tracking-[-0.035em] sm:text-7xl">
          Agents that are
          <br />
          <span className="grad-text">actually deterministic.</span>
        </h1>

        <p className="hero-enter hero-enter-delay-2 mx-auto mt-7 max-w-[38rem] text-lg leading-relaxed text-text-secondary">
          Deterministic flows with <b className="font-semibold text-text-primary">a2ui widgets</b> over
          the <b className="font-semibold text-text-primary">AG-UI</b> layer, driven by{" "}
          <b className="font-semibold text-text-primary">SKILL.md skills</b> in the Agent Skills layout.
          The LLM only interprets — the runtime decides, and every step renders real UI.
        </p>

        <div className="hero-enter hero-enter-delay-3 mt-9 flex flex-col items-center gap-4">
          <div className="flex flex-wrap items-center justify-center gap-2.5">
            <Button size="lg" className="h-12 px-7 text-[15px]" onClick={() => enterStudio("/canvas")}>
              <GitBranch /> Start building
            </Button>
            {IS_PUBLIC_SITE ? (
              <Button variant="secondary" size="lg" className="h-12 px-6 text-[15px]" asChild>
                <a href="https://github.com/aigenos/flow" rel="noopener" target="_blank">
                  <Github /> View on GitHub
                </a>
              </Button>
            ) : (
              <Button variant="secondary" size="lg" className="h-12 px-6 text-[15px]" onClick={enterWorkspace}>
                <LayoutGrid /> Run an agent
              </Button>
            )}
          </div>
          {IS_PUBLIC_SITE ? null : (
            <button
              onClick={() => enterStudio("/map", true)}
              className="group inline-flex items-center gap-1.5 rounded-md text-[15px] text-text-muted transition-colors hover:text-text-primary focus-ring"
            >
              <Play className="h-3 w-3" /> or take the 60-second tour
              <ArrowRight className="motion-arrow h-3 w-3 transition-transform" />
            </button>
          )}
        </div>
      </section>

      {/* 1 — CANVAS, the big one */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-16">
        <div className="wel-shot">
          <Shot
            base="canvas"
            url="localhost:5173/canvas · fail-closed verifier armed"
            alt="The Canvas — a deterministic flow as a visual graph with guard badges and a per-step inspector"
          />
        </div>
        <Reveal>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={GitBranch} term="Five kinds of step, not prompts">
              Ask, say, do, confirm, chat, and a repeat for lists. A condition is one line on a step. The file is what
              the runtime executes: diffable, reviewable in a pull request, and it reads
              back as a procedure a person can sign off. Describe a skill in a sentence
              and the Studio writes it; the loader checks every word.
            </Feature>
            <Feature icon={ShieldCheck} term="Fail-closed on save">
              The verifier refuses anything it can&rsquo;t prove safe, and puts the fix
              in the error instead of a stack trace.
            </Feature>
            <Feature icon={History} term="Versioned by default">
              Every save lands in SQLite. Restore any earlier version in a single click.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* marquee */}
      <div className="relative z-10 mt-20 border-y border-border-subtle py-3.5 [mask-image:linear-gradient(90deg,transparent,black_12%,black_88%,transparent)]">
        <div className="wel-marquee gap-8">
          {[...MARQUEE, ...MARQUEE].map((w, i) => (
            <span key={i} className="whitespace-nowrap font-mono text-[15px] text-text-muted">{w}</span>
          ))}
        </div>
      </div>

      {/* 2 — WIDGETS */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="01"
            kicker="Widgets"
            title={<>Design the UI. The flow renders it.</>}
            lede="Style grids, cards, and timelines like a design tool — typography, colors, borders, layout. Flow steps reference widgets by id, so the same design renders identically in every chat, every run."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12">
            <Shot
              base="designer"
              url="localhost:5173/widgets · click any element to style it"
              alt="The widget designer — component palette, click-to-select canvas, layers tree, and a full style inspector"
            />
          </div>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={Boxes} term="Bindings you can see">
              Data slots show what they&rsquo;re wired to while you design. The flow fills
              them at runtime.
            </Feature>
            <Feature icon={Sparkles} term="Start from a sentence">
              Generate a widget from one line of description, then refine every detail
              by hand.
            </Feature>
            <Feature icon={History} term="One history for everything">
              Widget saves land in the same version store as flows. Roll back a design
              like any other change.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* 3 — FLOWS, live */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="02"
            kicker="Flows, live"
            title={<>Run it. Widgets are steps.</>}
            lede="The product experience: a chat where the grid, card, and timeline are steps in the flow. A row click becomes a typed field update that never passes through the LLM — over AG-UI, A2UI, or MCP-UI."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12">
            <Shot
              base="flow"
              url="localhost:5173/flow · the same deterministic runtime your users get"
              alt="Live chat — a2ui widgets rendered as flow steps, with starter prompts"
            />
          </div>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={MessageSquare} term="Interruptible, not brittle">
              Free-form questions mid-flow get real answers — then the run picks the
              pending step back up.
            </Feature>
            <Feature icon={ShieldCheck} term="Soft language, hard logic">
              Guidelines shape how the agent speaks. Gates and actions stay
              engine-enforced regardless.
            </Feature>
            <Feature icon={Boxes} term="One widget, three protocols">
              The same components render over AG-UI, A2UI, and MCP-UI — anywhere React
              runs.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* 4 — WORKSPACE */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="03"
            kicker="Workspace"
            title={<>Hand it to people who don&rsquo;t build.</>}
            lede="The Workspace is the simple view. Describe a job in a sentence and it becomes a skill. Run any skill as a guided task, or just chat. Open its Canvas when you want to change it."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12">
            <Shot
              base="workspace"
              url="localhost:5173/workspace · the simple view"
              alt="The Workspace — describe a job in a sentence, quick-start templates, and the list of skills with run controls"
            />
          </div>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={Sparkles} term="A sentence becomes a skill">
              A description becomes one question per answer, the conditions you
              described, a confirmation and a built-in tool that records the request.
              Change it later in a sentence too: the model writes, the loader checks.
            </Feature>
            <Feature icon={LayoutGrid} term="Three ways in">
              Run a skill as a guided task, chat and let the agent pick the skill, or
              open the skill on the Canvas.
            </Feature>
            <Feature icon={ShieldCheck} term="Verified before it runs">
              A skill that fails the loader&rsquo;s checks is refused, not listed.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* 5 — GALLERY */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="04"
            kicker="Gallery"
            title={<>Start from one that works.</>}
            lede="The Gallery's templates are skills written as steps, with their tools beside them in scripts.py: a repeat with a manager's approval, a designed list with a chat, a booking with rules. Installing one copies the folder into your skills folder and opens it on the Canvas."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12">
            <Shot
              base="gallery"
              url="localhost:5173/gallery · install, then open on the Canvas"
              alt="The Gallery — installable templates written as steps, each showing how many nodes it draws on the Canvas and how many guarantees the verifier proved"
            />
          </div>
        </Reveal>
      </section>

      {/* 6 — SIGN-OFF: the procedure, the confirmation, someone else's yes */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="05"
            kicker="Sign-off"
            title={<>Read it back before it runs.</>}
            lede="A skill reads back as a procedure: each step in plain words, every way through the flow, and what must be true before anything changes. It is worked out from the file and its tools with no model involved, so it cannot say something the flow does not do."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12">
            <Shot
              base="procedure"
              url="localhost:5173/procedure/refund-desk · generated, not written"
              alt="The Procedure page — a skill's steps in plain words, with who may give each answer and what each rule refuses"
            />
          </div>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={ScrollText} term="For the person who signs it off">
              Copy it as Markdown, print it, or diff it in a pull request. It lists every
              tool that changes something and exactly what each one waits for.
            </Feature>
            <Feature icon={ShieldCheck} term="What you approve is what runs">
              The confirmation names the tool a yes will run and its inputs. Change an
              input after the yes and the tool does not run: the yes is asked for again.
            </Feature>
            <Feature icon={Inbox} term="A yes from someone else">
              One line makes a confirmation a manager&rsquo;s or finance&rsquo;s. The flow waits,
              and that yes cannot arrive through the chat, from the user or the model.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* 7 — THE WHOLE SURFACE */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="06"
            kicker="The Studio"
            title={<>Every surface, one backend.</>}
            lede="The Workspace, the Canvas and the Flow page work on the same skill files, and a Canvas step renders the widget you designed by its id. There is no export or import step between them."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12">
            <Shot
              base="map"
              url="localhost:5173/map · Overview"
              alt="The Studio Overview — a card each for Workspace, Canvas, Gallery, Widgets, Tools, Connectors, Approvals, Flow and Playground, labelled with the role it serves"
            />
          </div>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={Database} term="Connectors">
              Managed PostgreSQL and MongoDB operations become typed tools your flows
              can call. A write runs only after a confirmation field is set.
            </Feature>
            <Feature icon={Library} term="Gallery">
              Templates written as steps, each with how many nodes it draws and how many
              of the verifier&rsquo;s guarantees hold. Install one and change it on the Canvas.
            </Feature>
            <Feature icon={MessageSquare} term="Playground">
              Run any of your skills over AG-UI, A2UI or MCP-UI, type to it like a user,
              and read every frame the server sends: exactly what your own frontend
              would receive.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* 7 — WHO IT'S FOR: the ladder, stated plainly */}
      <section className="relative z-10 mx-auto w-full max-w-6xl px-6 pt-28">
        <Reveal>
          <SectionHead
            n="07"
            kicker="Who it's for"
            title={<>One agent. Three altitudes.</>}
            lede="Nobody adopts a platform role-by-role. Everyone meets the same skill at their own altitude — and everything below your altitude arrives already verified."
          />
        </Reveal>
        <Reveal delay={80}>
          <div className="mt-12 grid gap-x-10 gap-y-8 sm:grid-cols-3">
            <Feature icon={LayoutGrid} term="Business users — a sentence">
              Describe the job in the Workspace and run the skill it creates: a question
              per field, a summary, a confirmation and one action. No code.
            </Feature>
            <Feature icon={Library} term="Authors — one markdown file">
              A whole skill is one SKILL.md: fields, gates, effects, and the flow —
              declared, not coded. The verifier proves the gates before it ever loads.
            </Feature>
            <Feature icon={GitBranch} term="Developers — policy, not plumbing">
              Write only the lines that matter —{" "}
              <span className="font-mono text-[13px] text-text-primary">
                if amount &gt; 1000: ask for a code
              </span>{" "}
              — and drop the compiled graph into the LangGraph stack you already run.
            </Feature>
          </div>
        </Reveal>
      </section>

      {/* final CTA */}
      <Reveal>
        <section className="relative z-10 mx-auto w-full max-w-3xl px-6 py-32 text-center">
          <h2 className="text-[2.5rem] font-semibold leading-[1.08] tracking-[-0.03em] sm:text-5xl">
            Stop hoping your agent <span className="grad-text">behaves.</span>
          </h2>
          <p className="mx-auto mt-5 max-w-lg text-[15px] leading-relaxed text-text-secondary">
            Make the language soft and the logic hard. Build your first deterministic
            agent in the next five minutes — no train step, no platform to adopt.
          </p>
          <div className="mt-9 flex flex-wrap items-center justify-center gap-2.5">
            <Button size="lg" className="h-12 px-7 text-[15px]" onClick={() => enterStudio("/canvas")}>
              <GitBranch /> Start building
            </Button>
            <Button variant="secondary" size="lg" className="h-12 px-6 text-[15px]" asChild>
              <Link to="/docs"><BookOpen /> Read the docs</Link>
            </Button>
          </div>

          <div className="mt-14 flex flex-wrap items-center justify-center gap-x-2 gap-y-2">
            {KINDS.map((v, i) => (
              <code
                key={v}
                // Same 900ms/step clock as the flow mark, offset into flow order.
                style={{
                  animationDelay: `${i * CHAIN_STEP_S}s`,
                  animationDuration: `${KINDS.length * CHAIN_STEP_S}s`,
                }}
                className="wel-verb rounded-md border border-border-subtle bg-bg-muted/50 px-2 py-1 font-mono text-[13px] text-text-secondary"
              >
                {v}
              </code>
            ))}
            <span className="ml-1.5 text-[13px] text-text-muted">— the whole language, and what each step carries</span>
          </div>
        </section>
      </Reveal>

      {/* footer */}
      <footer className="relative z-10 border-t border-border-subtle">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center justify-between gap-4 px-6 py-8 text-[13px] text-text-muted">
          <div className="flex items-center gap-2">
            <img
              src={theme === "dark" ? "/aigenos-icon-dark.png" : "/aigenos-icon-light.png"}
              alt="" className="h-4 w-4 select-none opacity-70" draggable={false}
            />
            <span>aigenos flow · Apache-2.0</span>
          </div>
          <nav className="flex items-center gap-5">
            <Link to="/docs" className="transition-colors hover:text-text-primary">Documentation</Link>
            <a href="https://github.com/aigenos/flow" rel="noopener" target="_blank"
               className="transition-colors hover:text-text-primary">GitHub</a>
            <a href="https://aigenos.io" rel="noopener" target="_blank"
               className="transition-colors hover:text-text-primary">aigenos.io</a>
          </nav>
        </div>
      </footer>
    </div>
  );
}

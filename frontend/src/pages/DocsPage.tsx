/// <reference types="vite/client" />
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { marked } from "marked";
import { ArrowLeft, ArrowRight, BookOpen, Github, Search, X } from "lucide-react";
import { cn } from "../lib/cn";

/** All repo docs, bundled as raw text at build time (dev serves via @fs). */
const FILES = import.meta.glob("../../../docs/*.md", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

type DocItem = { slug: string; file: string; label: string };
type DocGroup = { title: string; items: DocItem[] };
type Heading = { id: string; text: string; level: number };

/** Sidebar structure — mirrors docs/README.md's reading order. */
const GROUPS: DocGroup[] = [
  {
    title: "Start here",
    items: [
      { slug: "tutorial", file: "GUIDE.md", label: "10-minute tutorial" },
      { slug: "steps", file: "steps.md", label: "Steps: the language on one page" },
    ],
  },
  {
    title: "Guides",
    items: [
      { slug: "cookbook", file: "cookbook.md", label: "Developer cookbook (9 recipes)" },
      { slug: "developer-guide", file: "developer-guide.md", label: "Developer guide" },
      { slug: "studio", file: "user-guide.md", label: "The Studio (no code)" },
      { slug: "skill-authoring", file: "skill-authoring.md", label: "Long-form reference" },
    ],
  },
  {
    title: "Integrate",
    items: [
      { slug: "integration-patterns", file: "integration-patterns.md", label: "Integration patterns" },
      { slug: "integration-recipe", file: "integration-recipe.md", label: "LangGraph + React recipe" },
      { slug: "connectors", file: "managed-connectors.md", label: "Managed connectors" },
    ],
  },
  {
    title: "Compare",
    items: [
      { slug: "comparison", file: "COMPARISON.md", label: "vs other frameworks" },
    ],
  },
];

const ORDER: DocItem[] = GROUPS.flatMap((g) => g.items);
const BY_FILE = new Map(ORDER.map((i) => [i.file, i]));

const REPO = "https://github.com/aigenos/flow";
const GITHUB_BLOB = `${REPO}/blob/main`;
const GITHUB_TREE = `${REPO}/tree/main`;

function rawFor(file: string): string | undefined {
  const key = Object.keys(FILES).find((k) => k.endsWith(`/${file}`));
  return key ? FILES[key] : undefined;
}

function slugifyHeading(text: string): string {
  return text.toLowerCase().replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-");
}

/** `foo.md` / `./foo.md` (optionally with a #fragment) for a doc in the sidebar. */
const DOC_HREF = /^(?:\.\/)?([\w.-]+\.md)(#.*)?$/;

/** Can this viewer render the target itself, or does it belong on GitHub? */
function routableDoc(href: string): boolean {
  const m = DOC_HREF.exec(href);
  return !!m && BY_FILE.has(m[1]);
}

export function DocsPage() {
  const { slug } = useParams();
  const navigate = useNavigate();
  const articleRef = useRef<HTMLDivElement>(null);

  const [query, setQuery] = useState("");
  const [toc, setToc] = useState<Heading[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [progress, setProgress] = useState(0);

  const current = ORDER.find((i) => i.slug === slug) ?? ORDER[0];
  const idx = ORDER.indexOf(current);
  const prev = idx > 0 ? ORDER[idx - 1] : null;
  const next = idx < ORDER.length - 1 ? ORDER[idx + 1] : null;

  const html = useMemo(() => {
    const raw = rawFor(current.file);
    if (!raw) return "<p>Document not found.</p>";
    return marked.parse(raw, { gfm: true, async: false }) as string;
  }, [current.file]);

  // Sidebar filter — matches the doc title or its group.
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return GROUPS;
    return GROUPS
      .map((g) => ({
        ...g,
        items: g.items.filter(
          (i) => i.label.toLowerCase().includes(q) || g.title.toLowerCase().includes(q),
        ),
      }))
      .filter((g) => g.items.length > 0);
  }, [query]);

  // Post-process the rendered article: heading ids + anchors, link routing,
  // code-block copy buttons, and the "on this page" outline.
  useEffect(() => {
    const root = articleRef.current;
    if (!root) return;

    const headings: Heading[] = [];
    root.querySelectorAll("h1, h2, h3, h4").forEach((h) => {
      if (!h.id) h.id = slugifyHeading(h.textContent ?? "");
      const level = Number(h.tagName[1]);
      if (level === 2 || level === 3) {
        // Read the label off a clone: this effect can run again over headings
        // that already carry an injected "#" anchor.
        const label = h.cloneNode(true) as HTMLElement;
        label.querySelector(".doc-anchor")?.remove();
        headings.push({ id: h.id, text: label.textContent?.trim() ?? "", level });
        if (!h.querySelector(".doc-anchor")) {
          const a = document.createElement("a");
          a.className = "doc-anchor";
          a.href = `#${h.id}`;
          a.textContent = "#";
          a.setAttribute("aria-label", `Link to ${h.textContent ?? "section"}`);
          h.appendChild(a);
        }
      }
    });
    setToc(headings);

    root.querySelectorAll("a").forEach((a) => {
      if (a.dataset.doc) return; // already rewritten (effects run twice in dev)
      const href = a.getAttribute("href") ?? "";
      if (href.startsWith("http")) {
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener");
      } else if (routableDoc(href)) {
        // Give the link its real address, so "open in new tab" and "copy link"
        // land where a click does. The click handler still routes in-app, keyed
        // on the original href kept in data-doc.
        const m = DOC_HREF.exec(href)!;
        a.dataset.doc = href;
        a.setAttribute("href", `/docs/${BY_FILE.get(m[1])!.slug}${m[2] ?? ""}`);
      } else if (!href.startsWith("#")) {
        // Anything the viewer cannot serve itself → GitHub. That includes a
        // .md file outside docs/ (e.g. ../CONTRIBUTING.md), which otherwise
        // resolved against the SPA route and 404'd. A trailing slash means a
        // directory, and GitHub serves those under /tree, not /blob.
        const path = href.startsWith("../")
          ? href.slice(3)
          : `docs/${href.replace(/^\.\//, "")}`;
        const view = path.endsWith("/") ? GITHUB_TREE : GITHUB_BLOB;
        a.setAttribute("href", `${view}/${path.replace(/\/$/, "")}`);
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener");
      }
    });

    root.querySelectorAll("pre").forEach((pre) => {
      if (pre.querySelector(".doc-copy")) return;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "doc-copy";
      btn.textContent = "Copy";
      btn.setAttribute("aria-label", "Copy code to clipboard");
      pre.appendChild(btn);
    });

    // Honor a #fragment on arrival, else scroll to top.
    const frag = window.location.hash.slice(1);
    const target = frag ? root.querySelector(`#${CSS.escape(frag)}`) : null;
    if (target) target.scrollIntoView({ block: "start" });
    else root.closest(".docs-scroll")?.scrollTo?.({ top: 0 });
  }, [html]);

  // Scroll-spy for the outline + a reading-progress rule. One rAF-throttled
  // listener drives both so the two never disagree.
  useEffect(() => {
    const root = articleRef.current;
    const scroller = root?.closest(".docs-scroll") as HTMLElement | null;
    if (!root || !scroller) return;

    let frame = 0;
    const update = () => {
      frame = 0;
      const scrollable = scroller.scrollHeight - scroller.clientHeight;
      setProgress(scrollable > 0 ? Math.min(1, scroller.scrollTop / scrollable) : 0);

      const list = Array.from(root.querySelectorAll<HTMLElement>("h2, h3"));
      if (!list.length) return;
      // Active = the last heading that has crossed the reading line.
      const line = scroller.getBoundingClientRect().top + 96;
      let currentId = list[0].id;
      for (const h of list) {
        if (h.getBoundingClientRect().top > line) break;
        currentId = h.id;
      }
      setActiveId(currentId);
    };
    const onScroll = () => { if (!frame) frame = requestAnimationFrame(update); };

    scroller.addEventListener("scroll", onScroll, { passive: true });
    update();
    return () => {
      scroller.removeEventListener("scroll", onScroll);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [html]);

  // Route .md links through the viewer, handle anchors and copy buttons.
  const onArticleClick = (e: React.MouseEvent) => {
    const el = e.target as HTMLElement;

    const copy = el.closest<HTMLButtonElement>(".doc-copy");
    if (copy) {
      const code = copy.closest("pre")?.querySelector("code")?.textContent ?? "";
      navigator.clipboard?.writeText(code).then(
        () => {
          copy.textContent = "Copied";
          copy.dataset.done = "1";
          setTimeout(() => { copy.textContent = "Copy"; delete copy.dataset.done; }, 1600);
        },
        () => { copy.textContent = "Failed"; setTimeout(() => { copy.textContent = "Copy"; }, 1600); },
      );
      return;
    }

    const a = el.closest("a");
    if (!a) return;
    const href = a.dataset.doc ?? a.getAttribute("href") ?? "";
    const m = DOC_HREF.exec(href);
    if (!m) return;
    const item = BY_FILE.get(m[1]);
    if (!item) return;
    e.preventDefault();
    navigate(`/docs/${item.slug}${m[2] ?? ""}`);
  };

  const jumpTo = (id: string) => {
    const el = articleRef.current?.querySelector(`#${CSS.escape(id)}`);
    el?.scrollIntoView({ block: "start", behavior: "smooth" });
    history.replaceState(null, "", `#${id}`);
  };

  return (
    <div className="flex h-full min-h-0">
      {/* sidebar */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-border-subtle bg-bg-surface/40 lg:flex">
        <div className="shrink-0 px-4 pb-3 pt-6">
          <Link
            to="/docs"
            className="flex items-center gap-2 px-1 text-sm font-semibold tracking-tight text-text-primary"
          >
            <BookOpen className="h-4 w-4 text-brand" /> Documentation
          </Link>
          <div className="relative mt-4">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter docs"
              aria-label="Filter documentation"
              className="w-full rounded-lg border border-border-subtle bg-bg-muted/50 py-1.5 pl-8 pr-7 text-[13px] text-text-primary placeholder:text-text-muted focus:outline-none focus:ring-2"
              style={{ ["--tw-ring-color" as string]: "rgb(var(--brand-ring))" }}
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                aria-label="Clear filter"
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-text-muted transition-colors hover:text-text-primary"
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </div>
        </div>

        <nav className="min-h-0 flex-1 overflow-y-auto scrollbar-thin px-4 pb-6">
          {groups.map((g) => (
            <div key={g.title} className="mt-5 first:mt-1">
              <div className="px-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
                {g.title}
              </div>
              <div className="mt-1.5">
                {g.items.map((item) => {
                  const active = item.slug === current.slug;
                  return (
                    <Link
                      key={item.slug}
                      to={`/docs/${item.slug}`}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "-ml-px block border-l py-1.5 pl-3 pr-2 text-[13px] transition-colors",
                        active
                          ? "border-brand font-medium text-brand"
                          : "border-border-subtle text-text-secondary hover:border-text-muted hover:text-text-primary",
                      )}
                    >
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
          {groups.length === 0 && (
            <p className="mt-6 px-1 text-[13px] text-text-muted">No docs match “{query}”.</p>
          )}
        </nav>

        <a
          href={`${REPO}/tree/main/docs`}
          rel="noopener"
          target="_blank"
          className="flex shrink-0 items-center gap-1.5 border-t border-border-subtle px-5 py-3.5 text-[12px] text-text-muted transition-colors hover:text-text-primary"
        >
          <Github className="h-3.5 w-3.5" /> Edit these docs on GitHub
        </a>
      </aside>

      {/* content */}
      <div className="relative min-w-0 flex-1">
        {/* reading progress — a hairline, not a loading bar */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 top-0 z-10 h-px origin-left bg-brand/70"
          style={{ transform: `scaleX(${progress})` }}
        />
        <div className="docs-scroll h-full overflow-y-auto scrollbar-thin">
          <div className="mx-auto flex w-full max-w-[74rem] gap-10 px-6 py-10 xl:px-10">
            <div className="min-w-0 flex-1">
              {/* mobile doc picker */}
              <select
                value={current.slug}
                onChange={(e) => navigate(`/docs/${e.target.value}`)}
                className="mb-8 w-full rounded-lg border border-border bg-bg-muted px-3 py-2 text-sm lg:hidden"
                aria-label="Select document"
              >
                {GROUPS.map((g) => (
                  <optgroup key={g.title} label={g.title}>
                    {g.items.map((i) => (
                      <option key={i.slug} value={i.slug}>{i.label}</option>
                    ))}
                  </optgroup>
                ))}
              </select>

              <article
                ref={articleRef}
                className="doc-prose max-w-[68ch]"
                onClick={onArticleClick}
                dangerouslySetInnerHTML={{ __html: html }}
              />

              {/* prev / next */}
              <nav className="mt-16 flex max-w-[68ch] items-stretch justify-between gap-3 border-t border-border-subtle pt-6">
                {prev ? (
                  <Link
                    to={`/docs/${prev.slug}`}
                    className="group flex-1 rounded-xl border border-border-subtle px-4 py-3 transition-colors hover:border-brand/40 hover:bg-bg-surface/60"
                  >
                    <span className="flex items-center gap-1 text-[11px] uppercase tracking-wider text-text-muted">
                      <ArrowLeft className="h-3 w-3" /> Previous
                    </span>
                    <span className="mt-0.5 block text-sm font-semibold text-text-primary transition-colors group-hover:text-brand">
                      {prev.label}
                    </span>
                  </Link>
                ) : <span className="flex-1" />}
                {next ? (
                  <Link
                    to={`/docs/${next.slug}`}
                    className="group flex-1 rounded-xl border border-border-subtle px-4 py-3 text-right transition-colors hover:border-brand/40 hover:bg-bg-surface/60"
                  >
                    <span className="flex items-center justify-end gap-1 text-[11px] uppercase tracking-wider text-text-muted">
                      Next <ArrowRight className="h-3 w-3" />
                    </span>
                    <span className="mt-0.5 block text-sm font-semibold text-text-primary transition-colors group-hover:text-brand">
                      {next.label}
                    </span>
                  </Link>
                ) : <span className="flex-1" />}
              </nav>
            </div>

            {/* on this page */}
            {toc.length > 1 && (
              <aside className="hidden w-52 shrink-0 xl:block">
                <div className="sticky top-10">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-text-muted">
                    On this page
                  </div>
                  <ul className="mt-3 border-l border-border-subtle">
                    {toc.map((h) => (
                      <li key={h.id}>
                        <button
                          onClick={() => jumpTo(h.id)}
                          title={h.text}
                          className={cn(
                            "-ml-px block w-full border-l py-1 text-left text-[12px] leading-snug transition-colors line-clamp-2",
                            h.level === 3 ? "pl-6" : "pl-3",
                            h.id === activeId
                              ? "border-brand text-brand"
                              : "border-transparent text-text-muted hover:text-text-secondary",
                          )}
                        >
                          {h.text}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              </aside>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

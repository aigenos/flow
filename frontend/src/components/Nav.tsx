import { useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  MessageSquare, Boxes, Sun, Moon, Github,
  Globe, Sparkles, Menu as MenuIcon, X, LayoutGrid, Code2, GitBranch,
  Database, BookOpen, LibraryBig, Wrench, Inbox,
} from "lucide-react";
import { cn } from "../lib/cn";
import { useTheme } from "../hooks/useTheme";
import { useMode, type Mode } from "../hooks/useMode";
import { IS_PUBLIC_SITE } from "../lib/publicSite";

// Developer surfaces (Studio mode only), in pipeline order: orient → build the
// flow → design its widgets → write its tools → connect data → try it live →
// inspect the wire.
// The legacy widget-DAG builder (/builder) is intentionally NOT in the nav —
// it's reachable from the Playground, where its graphs run.
const studioItems = [
  { to: "/map", label: "Overview", icon: LayoutGrid },
  { to: "/canvas", label: "Canvas", icon: GitBranch },
  { to: "/gallery", label: "Gallery", icon: LibraryBig },
  { to: "/widgets", label: "Widgets", icon: Boxes },
  { to: "/tools", label: "Tools", icon: Wrench },
  { to: "/connectors", label: "Connectors", icon: Database },
  { to: "/flow", label: "Flow", icon: Sparkles },
  { to: "/chat", label: "Playground", icon: MessageSquare },
];

function RoleSwitch({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Choose workspace"
      className="relative grid grid-cols-2 w-[196px] rounded-xl border border-border-subtle bg-bg-muted/75 p-1 theme-transition"
    >
      <span
        aria-hidden
        className={cn(
          "role-indicator absolute top-1 bottom-1 left-1 w-[calc(50%-4px)] rounded-lg border border-border bg-bg-elevated shadow-sm",
          mode === "studio" && "translate-x-full",
        )}
      />
      <button
        type="button"
        role="tab"
        aria-selected={mode === "workspace"}
        onClick={() => onChange("workspace")}
        className={cn(
          "control-press relative z-10 inline-flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-medium focus-ring",
          mode === "workspace" ? "text-text-primary" : "text-text-secondary hover:text-text-primary",
        )}
      >
        <LayoutGrid className="h-3.5 w-3.5" /> Workspace
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={mode === "studio"}
        onClick={() => onChange("studio")}
        className={cn(
          "control-press relative z-10 inline-flex items-center justify-center gap-1.5 rounded-lg py-1.5 text-xs font-medium focus-ring",
          mode === "studio" ? "text-text-primary" : "text-text-secondary hover:text-text-primary",
        )}
      >
        <Code2 className="h-3.5 w-3.5" /> Studio
      </button>
    </div>
  );
}

export function Nav() {
  const { theme, toggle } = useTheme();
  const { mode, setMode } = useMode();
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => { setOpen(false); }, [location.pathname]);

  // How many requests are waiting on someone's yes (see ApprovalsPage).
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    if (IS_PUBLIC_SITE) return;
    let live = true;
    const look = async () => {
      try {
        const r = await fetch("/api/flow/approvals?status=pending");
        if (live && r.ok) setWaiting(((await r.json()).approvals ?? []).length);
      } catch { /* no server: no count */ }
    };
    void look();
    const timer = window.setInterval(() => void look(), 5000);
    return () => { live = false; window.clearInterval(timer); };
  }, []);

  const switchMode = (m: Mode) => {
    setMode(m);
    navigate(m === "workspace" ? "/workspace" : "/map");
  };

  return (
    <header className="nav-material nav-edge theme-transition sticky top-0 z-30 relative">
      {/* Between xl and 2xl the Studio tabs have to share the row with the role
          switch, so that range runs slightly tighter. */}
      <div className="mx-auto max-w-[100rem] px-4 sm:px-6 lg:px-8 py-2.5 flex items-center gap-4 xl:gap-3 2xl:gap-4">
        <NavLink
          to="/"
          aria-label="aigenos flow — home"
          className="control-press flex items-center gap-2 shrink-0 rounded-lg focus-ring"
        >
          <img
            src={theme === "dark" ? "/aigenos-icon-dark.png" : "/aigenos-icon-light.png"}
            alt=""
            className="h-6 w-6 select-none"
            draggable={false}
          />
          <span className="text-lg font-bold tracking-tight text-text-primary">aigenos</span>
          <span className="text-sm font-medium text-text-muted">flow</span>
        </NavLink>

        {/* Role switch — the heart of the role-based shell. Hidden on the
            public site, where every Studio surface is unreachable anyway. */}
        {!IS_PUBLIC_SITE && (
          <div className="hidden sm:block">
            <RoleSwitch mode={mode} onChange={switchMode} />
          </div>
        )}

        {/* Developer tabs — Studio only */}
        <nav aria-label="Primary" className="hidden xl:flex items-center gap-1 ml-1">
          {!IS_PUBLIC_SITE && mode === "studio" &&
            studioItems.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  cn(
                    "control-press inline-flex items-center gap-1.5 rounded-lg px-2.5 2xl:px-3 py-1.5 text-[13px] leading-5 2xl:text-sm whitespace-nowrap focus-ring",
                    isActive
                      ? "bg-bg-elevated text-text-primary border border-border shadow-sm"
                      : "text-text-secondary hover:text-text-primary hover:bg-bg-muted/60 border border-transparent",
                  )
                }
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </NavLink>
            ))}
        </nav>

        <div className="flex items-center gap-2 ml-auto">
          {!IS_PUBLIC_SITE && (
            <NavLink
              to="/approvals"
              aria-label={waiting ? `Approvals: ${waiting} waiting` : "Approvals"}
              title={waiting ? `${waiting} waiting for a yes` : "Approvals: requests waiting for someone's yes"}
              className={({ isActive }) =>
                cn(
                  "control-press icon-control relative hidden md:inline-flex items-center justify-center rounded-xl border focus-ring",
                  isActive
                    ? "border-brand/40 bg-brand-subtle text-brand"
                    : "border-border-subtle bg-bg-muted/75 text-text-secondary hover:text-text-primary hover:border-border",
                )
              }
            >
              <Inbox className="h-3.5 w-3.5" />
              {waiting > 0 && (
                <span className="absolute -right-1.5 -top-1.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-amber-500 px-1 text-[10px] font-bold text-white tabular-nums">
                  {waiting}
                </span>
              )}
            </NavLink>
          )}
          <NavLink
            to="/docs"
            aria-label="Documentation"
            title="Documentation"
            className={({ isActive }) =>
              cn(
                "control-press icon-control hidden md:inline-flex items-center justify-center rounded-xl border focus-ring",
                isActive
                  ? "border-brand/40 bg-brand-subtle text-brand"
                  : "border-border-subtle bg-bg-muted/75 text-text-secondary hover:text-text-primary hover:border-border",
              )
            }
          >
            <BookOpen className="h-3.5 w-3.5" />
          </NavLink>
          <button
            type="button"
            onClick={toggle}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
            className="control-press icon-control inline-flex items-center justify-center rounded-xl border border-border-subtle bg-bg-muted/75 text-text-secondary hover:text-text-primary hover:border-border focus-ring"
          >
            {theme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>
          <a
            href="https://aigenos.io"
            target="_blank"
            rel="noreferrer"
            aria-label="aigenos.io"
            className="control-press icon-control hidden 2xl:inline-flex items-center justify-center rounded-xl border border-border-subtle bg-bg-muted/75 text-text-secondary hover:text-text-primary hover:border-border focus-ring"
            title="aigenos.io"
          >
            <Globe className="h-3.5 w-3.5" />
          </a>
          <a
            href="https://github.com/aigenos/flow"
            target="_blank"
            rel="noreferrer"
            aria-label="GitHub"
            className={cn(
              "control-press icon-control items-center justify-center rounded-xl border border-border-subtle bg-bg-muted/75 text-text-secondary hover:text-text-primary hover:border-border focus-ring",
              // On the public site this is the only outbound link left once the
              // menu is gone, so keep it at every width.
              // Between xl and 2xl the Studio tabs and the Approvals inbox need
              // the room; the link is in the docs and on the product page too.
              IS_PUBLIC_SITE ? "inline-flex" : mode === "studio" ? "hidden sm:inline-flex xl:hidden 2xl:inline-flex" : "hidden sm:inline-flex",
            )}
            title="GitHub"
          >
            <Github className="h-3.5 w-3.5" />
          </a>

          {/* Mobile menu — every entry is a Studio surface, so on the public
              site it would only ever bounce the reader back to the homepage.
              Hidden once the bar itself shows everything the menu holds: the
              tabs from xl in Studio mode, the role switch from sm in Workspace
              mode. */}
          {!IS_PUBLIC_SITE && (
          <div className={cn("relative", mode === "studio" ? "xl:hidden" : "sm:hidden")}>
            <button
              type="button"
              aria-label="Menu"
              aria-controls="mobile-nav-panel"
              aria-expanded={open}
              onClick={() => setOpen((prev) => !prev)}
              className="control-press icon-control inline-flex items-center justify-center rounded-xl border border-border-subtle bg-bg-muted/75 text-text-secondary hover:text-text-primary hover:border-border focus-ring"
            >
              {open ? <X className="h-4 w-4" /> : <MenuIcon className="h-4 w-4" />}
            </button>
            {open && (
              <div
                id="mobile-nav-panel"
                role="menu"
                className="mobile-menu-popup material-popover absolute right-0 mt-2 w-64 rounded-2xl border border-border bg-bg-elevated p-2 shadow-xl"
              >
                <nav aria-label="Mobile">
                  <div className="sm:hidden mb-2">
                    <RoleSwitch mode={mode} onChange={switchMode} />
                  </div>
                  {mode === "studio" &&
                    studioItems.map(({ to, label, icon: Icon }) => (
                      <button
                        key={to}
                        type="button"
                        role="menuitem"
                        onClick={() => {
                          navigate(to);
                          setOpen(false);
                        }}
                        className={cn(
                          "control-press flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-left focus-ring",
                          location.pathname === to
                            ? "bg-bg-muted text-text-primary"
                            : "text-text-secondary hover:text-text-primary hover:bg-bg-muted/60",
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0" />
                        {label}
                      </button>
                    ))}
                  {mode === "workspace" && (
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => {
                        navigate("/workspace");
                        setOpen(false);
                      }}
                      className="control-press flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-text-secondary hover:text-text-primary hover:bg-bg-muted/60 focus-ring"
                    >
                      <LayoutGrid className="h-4 w-4 shrink-0" /> Workspace
                    </button>
                  )}
                </nav>
              </div>
            )}
          </div>
          )}
        </div>
      </div>
    </header>
  );
}

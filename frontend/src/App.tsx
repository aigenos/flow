import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { Nav } from "./components/Nav";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Tour } from "./components/Tour";
import { WorkspacePage } from "./pages/WorkspacePage";
import { WorkspaceAgentPage } from "./pages/WorkspaceAgentPage";
import { ChatPage } from "./pages/ChatPage";
import { WidgetPlayground } from "./chat/WidgetPlayground";
import { FlowPage } from "./pages/FlowPage";
import { WidgetsPage } from "./pages/WidgetsPage";
import { WidgetEditorPage } from "./pages/WidgetEditorPage";
import { BuilderPage } from "./pages/BuilderPage";
import { BuilderEditorPage } from "./pages/BuilderEditorPage";
import { SkillFlowPage } from "./pages/SkillFlowPage";
import { MapPage } from "./pages/MapPage";
import { ConnectorsPage } from "./pages/ConnectorsPage";
import { ToolsPage } from "./pages/ToolsPage";
import { ProcedurePage } from "./pages/ProcedurePage";
import { ApprovalsPage } from "./pages/ApprovalsPage";
import { GalleryPage } from "./pages/GalleryPage";
import { DocsPage } from "./pages/DocsPage";
import { WelcomePage, WELCOME_SEEN_KEY } from "./pages/WelcomePage";
import { useMode } from "./hooks/useMode";
import { IS_PUBLIC_SITE } from "./lib/publicSite";

function welcomeSeen(): boolean {
  try {
    // `?welcome=0` suppresses the landing permanently (captures, deep links).
    if (new URLSearchParams(window.location.search).get("welcome") === "0") {
      localStorage.setItem(WELCOME_SEEN_KEY, "1");
    }
    return localStorage.getItem(WELCOME_SEEN_KEY) === "1";
  } catch {
    return true; // no storage — don't trap the user on the landing
  }
}

// First visit lands on the product page; after that, users land in the
// surface that matches their chosen role: business users on their Workspace,
// builders on the Studio Overview.
function RootRedirect() {
  const { mode } = useMode();
  // The public site has no backend, so the product page is the only root.
  if (IS_PUBLIC_SITE || !welcomeSeen()) return <Navigate to="/welcome" replace />;
  return <Navigate to={mode === "workspace" ? "/workspace" : "/map"} replace />;
}

/** A Studio surface. On the public build it needs a backend that isn't there,
 *  so send visitors back to the product page rather than to an empty shell. */
function studioOnly(element: React.ReactNode) {
  return IS_PUBLIC_SITE ? <Navigate to="/welcome" replace /> : element;
}

// The landing is full-bleed — no app chrome until a door is picked.
function Shell() {
  const { pathname } = useLocation();
  // The landing carries its own header. On the public site it also lives at
  // "/", so the app chrome has to stay hidden there too.
  const isWelcome = pathname === "/welcome" || (IS_PUBLIC_SITE && pathname === "/");
  return (
    <div className="flex h-full flex-col">
      {!isWelcome && <Nav />}
      <div className="flex-1 min-h-0">
        <ErrorBoundary>
          <Routes>
            {/* On the public site the product page IS the root — rendered, not
                redirected, so "/" stays a real page in history. */}
            <Route path="/" element={IS_PUBLIC_SITE ? <WelcomePage /> : <RootRedirect />} />
            <Route path="/welcome" element={<WelcomePage />} />
            <Route path="/workspace" element={studioOnly(<WorkspacePage />)} />
            <Route path="/workspace/run/:name" element={studioOnly(<WorkspaceAgentPage />)} />
            <Route path="/flow" element={studioOnly(<FlowPage />)} />
            <Route path="/chat" element={studioOnly(<ChatPage />)} />
            <Route path="/widgets" element={studioOnly(<WidgetsPage />)} />
            <Route path="/widgets/:id" element={studioOnly(<WidgetEditorPage />)} />
            <Route path="/widgets/new" element={studioOnly(<WidgetEditorPage />)} />
            <Route path="/builder" element={studioOnly(<BuilderPage />)} />
            <Route path="/builder/run" element={studioOnly(<WidgetPlayground />)} />
            <Route path="/builder/:id" element={studioOnly(<BuilderEditorPage />)} />
            <Route path="/builder/new" element={studioOnly(<BuilderEditorPage />)} />
            <Route path="/canvas" element={studioOnly(<SkillFlowPage />)} />
            <Route path="/canvas/:name" element={studioOnly(<SkillFlowPage />)} />
            <Route path="/gallery" element={studioOnly(<GalleryPage />)} />
            <Route path="/tools" element={studioOnly(<ToolsPage />)} />
            <Route path="/procedure/:name" element={studioOnly(<ProcedurePage />)} />
            <Route path="/approvals" element={studioOnly(<ApprovalsPage />)} />
            <Route path="/connectors" element={studioOnly(<ConnectorsPage />)} />
            <Route path="/map" element={studioOnly(<MapPage />)} />
            <Route path="/docs" element={<DocsPage />} />
            <Route path="/docs/:slug" element={<DocsPage />} />
            <Route path="*" element={<RootRedirect />} />
          </Routes>
        </ErrorBoundary>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Tour />
      <Shell />
    </BrowserRouter>
  );
}

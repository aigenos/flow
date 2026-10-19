// Bundler entry for the library build.
//
// It pulls in the self-contained stylesheet (auto-injected at import time via
// vite-plugin-lib-inject-css), so inside a bundler `import { Widget } from
// "@aigenos/flow"` needs no separate CSS import.
//
// Plain Node has no CSS pipeline, so the package's `node` export condition
// points at a CSS-free build of ./index instead (dist/lib/index.node.js and
// dist/lib/index.cjs). There, import "@aigenos/flow/style.css" yourself.
import "./styles/lib.css";

export * from "./index";

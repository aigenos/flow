import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import dts from "vite-plugin-dts";
import { libInjectCss } from "vite-plugin-lib-inject-css";
import { fileURLToPath } from "node:url";

/**
 * Library build for the published @aigenos/flow package.
 *
 * It writes to `dist/lib/`, NOT `dist/` — `dist/` is where the demo/Studio SPA
 * build (vite.config.ts) puts index.html, hashed assets and screenshots, and
 * package.json's `files` allowlist names `dist/lib` only. That way the demo
 * build can never be mistaken for, or packed as, the library.
 *
 * Two passes, selected by --mode:
 *
 *   --mode bundler (default)   src/lib-entry.ts
 *       → dist/lib/index.js      ESM, `import "./style.css"` injected
 *       → dist/lib/style.css     the self-contained stylesheet
 *       → dist/lib/index.d.ts    one rolled-up declaration file
 *
 *   --mode node                src/index.ts (no CSS import at all)
 *       → dist/lib/index.node.js ESM for plain Node / SSR / unit tests
 *       → dist/lib/index.cjs     CommonJS for require()
 *
 * `npm run build:lib` runs both; `npm run check:lib` verifies the result, and
 * `prepack` runs both so `npm pack` and `npm publish` can never ship anything
 * else.
 */
export default defineConfig(({ mode }) => {
  const isNode = mode === "node";
  return {
    plugins: isNode
      ? [react()]
      : [
          react(),
          libInjectCss(),
          // rollupTypes collapses the whole public graph into a single
          // dist/lib/index.d.ts. Without it the package shipped 67 declaration
          // files for Studio internals, several importing devDependencies
          // (reactflow, @radix-ui/*) that consumers do not have.
          // newlineKind: the rollup step writes CRLF by default, on every OS.
          dts({
            include: ["src"],
            rollupTypes: true,
            insertTypesEntry: true,
            rollupConfig: { newlineKind: "lf" },
          }),
        ],
    build: {
      outDir: "dist/lib",
      // The bundler pass runs first and owns the directory; the node pass adds
      // to it so both live side by side.
      emptyOutDir: !isNode,
      // Ship a map for the entry consumers actually bundle; the Node/CJS
      // mirrors would only triple the tarball.
      sourcemap: !isNode,
      copyPublicDir: false,
      lib: {
        entry: fileURLToPath(
          new URL(isNode ? "src/index.ts" : "src/lib-entry.ts", import.meta.url),
        ),
        formats: isNode ? (["es", "cjs"] as const) : (["es"] as const),
        fileName: (format: string) =>
          isNode ? (format === "cjs" ? "index.cjs" : "index.node.js") : "index.js",
      },
      rollupOptions: {
        // Don't bundle React — the host app provides it (peerDependencies).
        external: ["react", "react-dom", "react/jsx-runtime"],
        output: {
          globals: { react: "React", "react-dom": "ReactDOM" },
          assetFileNames: (info: { name?: string }) =>
            info.name && info.name.endsWith(".css") ? "style.css" : "[name][extname]",
        },
      },
    },
  };
});

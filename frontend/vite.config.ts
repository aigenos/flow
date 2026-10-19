import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Where the dev server forwards /api. Set FG_API_URL when the backend is not
// on the default port, e.g. FG_API_URL=http://localhost:8001 npm run dev
const API_TARGET = process.env.FG_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // The in-app docs viewer (`/docs`) imports ../docs/*.md as raw text.
    fs: { allow: [".."] },
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true, rewrite: (p) => p.replace(/^\/api/, "") },
    },
  },
});

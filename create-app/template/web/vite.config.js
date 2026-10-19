import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The web app calls /api/* — proxy those to the FastAPI server on :8000 so
// there's no CORS friction in development.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});

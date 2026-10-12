import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, the API runs on :8000; in Docker the API serves the built files itself.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/ready": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies to the Python backend (python run.py --no-browser)
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8765", "/ws": { target: "ws://127.0.0.1:8765", ws: true } } },
});

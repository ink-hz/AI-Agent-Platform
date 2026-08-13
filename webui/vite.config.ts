import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // One immutable build is mounted at both `/` and the isolated preview
  // namespace. Relative entry assets resolve beside each environment's login
  // route without embedding either deployment prefix in the bundle.
  base: "./",
  plugins: [react()],
  build: { outDir: "dist", manifest: true },
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});

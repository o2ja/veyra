import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// In the dev stack the API runs in the `api` container; outside Docker it is localhost.
const apiTarget = process.env.VITE_DEV_API_PROXY ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: { usePolling: process.env.VITE_POLL === "1" }, // bind mounts from Windows hosts
    proxy: { "/api": { target: apiTarget, changeOrigin: false } },
  },
  test: {
    environment: "jsdom",
    // https origin: jsdom enforces the __Host- cookie prefix rules (Secure, Path=/), like browsers
    environmentOptions: { jsdom: { url: "https://localhost/" } },
    restoreMocks: true,
  },
});

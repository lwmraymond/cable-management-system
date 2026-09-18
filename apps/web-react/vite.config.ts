import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/app-next/",
  plugins: [react()],
  resolve: { dedupe: ["react", "react-dom", "react-router", "react-router-dom"] },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:18000" },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});

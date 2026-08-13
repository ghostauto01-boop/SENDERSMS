import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "frontend/src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["frontend/src/test/setup.ts"],
    include: ["frontend/src/**/*.test.{ts,tsx}"],
    css: false,
    // One worker, one test at a time: keeps the jsdom runs deterministic and
    // lets tests that open chats clean up their polling timers cleanly.
    maxWorkers: 1,
    minWorkers: 1,
    fileParallelism: false,
  },
});

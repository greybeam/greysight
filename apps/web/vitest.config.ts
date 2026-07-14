import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    coverage: {
      provider: "v8",
      thresholds: {
        statements: 80,
        branches: 78,
        functions: 80,
        lines: 80,
      },
    },
  },
});

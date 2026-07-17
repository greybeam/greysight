import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Cap the worker pool well below the core count. The default pool spawns one
    // worker per core, which oversubscribes CPU on the full suite and starves
    // the event loop in waitFor-heavy component tests (cost-dashboard,
    // cache-settings, automated-savings-shell) — under load a sub-second test
    // balloons past the 5s default and times out intermittently. Half the cores
    // leaves headroom for the main thread, GC, and system work; the raised
    // testTimeout is a safety margin for any remaining contention. Both are
    // stability guards, not correctness changes.
    maxWorkers: "50%",
    testTimeout: 20000,
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

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

// `@next/env` is published as CommonJS; its named exports are not statically
// detectable for ESM, so import the default object and destructure at runtime.
import nextEnv from "@next/env";

const { loadEnvConfig } = nextEnv;

// Anchor the repo root to this script's own location (apps/web/scripts/with-env.mjs)
// rather than process.cwd(), so env loading is correct regardless of the cwd the
// launcher is started from. Repo root is three levels up from the script's dir.
const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..", "..", "..");

// First positional arg is the Next subcommand (e.g. "dev" or "build").
const subcommand = process.argv[2];
const dev = subcommand === "dev";

// Load root `.env*` into process.env BEFORE spawning Next, so Turbopack inlines
// NEXT_PUBLIC_* into the client bundle (it reads process.env at startup, not from
// next.config-time mutations). loadEnvConfig is tolerant — it no-ops when no
// `.env*` files exist (keeping the zero-config demo working) and never overrides
// vars already set in process.env (shell/CI/platform env stays authoritative).
loadEnvConfig(repoRoot, dev);

// Drop @next/env's processed flag so the child Next process does its own clean
// env initialization while still seeing our injected vars as ambient process.env.
delete process.env.__NEXT_PROCESSED_ENV;

const require = createRequire(import.meta.url);
const nextBin = require.resolve("next/dist/bin/next");

const child = spawn(process.execPath, [nextBin, ...process.argv.slice(2)], {
  stdio: "inherit",
  env: process.env,
});

// Forward parent termination signals to the child so the wrapper never exits while
// the Next process keeps running (which would orphan the dev server and leak port
// 3000 under `concurrently`). The child `exit` handler below still re-raises the
// signal / propagates the exit code so the wrapper exits correctly afterwards.
const forwardedSignals = ["SIGINT", "SIGTERM", "SIGHUP"];
for (const signal of forwardedSignals) {
  process.on(signal, () => {
    try {
      child.kill(signal);
    } catch {
      // Child already gone — nothing to forward.
    }
  });
}

child.on("exit", (code, signal) => {
  if (signal) {
    // Remove our forwarding handlers before re-raising, so the self-signal
    // triggers the default OS action (terminate) instead of being swallowed by
    // the handler above — which would leave the wrapper running.
    for (const forwarded of forwardedSignals) {
      process.removeAllListeners(forwarded);
    }
    process.kill(process.pid, signal);
    return;
  }

  process.exit(code ?? 0);
});

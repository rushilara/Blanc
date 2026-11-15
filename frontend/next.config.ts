import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";
import type { NextConfig } from "next";

// The frontend shares the repo-root .env (copied from .env.example) with the
// backend. npm scripts run from frontend/, so the root is "..". Precedence:
// real environment / frontend/.env* (loaded by Next itself) > root .env > default.
const rootEnvPath = path.resolve(process.cwd(), "..", ".env");
const rootEnv = existsSync(rootEnvPath) ? parseEnv(readFileSync(rootEnvPath, "utf8")) : {};

function publicVar(name: string, fallback: string): string {
  return process.env[name] || rootEnv[name] || fallback;
}

const nextConfig: NextConfig = {
  env: {
    NEXT_PUBLIC_API_BASE_URL: publicVar("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000"),
    NEXT_PUBLIC_DEFAULT_CASE_ID: publicVar("NEXT_PUBLIC_DEFAULT_CASE_ID", ""),
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    // The shared fixture lives beside frontend/, not inside its package root.
    root: path.resolve(process.cwd(), ".."),
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;

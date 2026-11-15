// Server-only settings. Like next.config.ts, the frontend shares the repo-root .env with
// the backend, so BLANC_AUTH_SECRET is set once for both. Precedence: the real
// environment (and frontend/.env*, which Next loads into it) > root .env.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";
import { authConfig, type AuthConfig } from "./auth";

const repoRoot = path.resolve(process.cwd(), "..");

function rootEnv(): Record<string, string | undefined> {
  const file = path.join(repoRoot, ".env");
  return existsSync(file) ? parseEnv(readFileSync(file, "utf8")) : {};
}

/** Read on every call so a changed .env needs no rebuild (only a restart for the backend). */
export function serverAuthConfig(): AuthConfig {
  const env = rootEnv();
  const config = authConfig(name => process.env[name] || env[name], path.join(repoRoot, ".blanc", "users.json"));
  // A relative BLANC_USERS_FILE resolves from the repo root, like the .env it sits in.
  return config.enabled && "usersFile" in config ? { ...config, usersFile: path.resolve(repoRoot, config.usersFile) } : config;
}

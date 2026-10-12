import "server-only";
import fs from "node:fs";
import path from "node:path";

const KEYS = new Set([
  "DASHBOARD_DATABASE_URL",
  "DASHBOARD_DB_HOST",
  "DASHBOARD_DB_PORT",
  "WORKSPACE_DIR",
  "LOGS_DIR",
]);

let loaded = false;

export function loadEnv(): void {
  if (loaded) return;
  loaded = true;
  for (const file of [path.resolve(process.cwd(), "../.env"), path.resolve(process.cwd(), ".env")]) {
    if (!fs.existsSync(/*turbopackIgnore: true*/ file)) continue;
    applyEnvFile(fs.readFileSync(/*turbopackIgnore: true*/ file, "utf8"));
  }
}

export function workspaceDir(): string {
  loadEnv();
  return process.env.WORKSPACE_DIR ?? path.resolve(process.cwd(), "..", "workspace");
}

export function logsDir(): string {
  loadEnv();
  return process.env.LOGS_DIR ?? path.resolve(process.cwd(), "..", "logs");
}

export function messagesDir(): string {
  return path.join(workspaceDir(), "messages");
}

function applyEnvFile(text: string): void {
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const body = line.startsWith("export ") ? line.slice(7).trim() : line;
    const eq = body.indexOf("=");
    if (eq <= 0) continue;
    const key = body.slice(0, eq).trim();
    if (!KEYS.has(key) || process.env[key] !== undefined) continue;
    let value = body.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

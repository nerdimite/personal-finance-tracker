import fs from "node:fs";
import path from "node:path";

export function safeSegment(segment: string): boolean {
  if (segment.length === 0 || segment.length > 255) return false;
  if (segment === "." || segment === "..") return false;
  if (/[\u0000-\u001f]/.test(segment)) return false;
  if (segment.includes("/") || segment.includes("\\")) return false;
  return true;
}

export function messageFileHref(storedPath: string): string | null {
  const norm = storedPath.replace(/\\/g, "/").replace(/^\/+/, "");
  if (!norm.startsWith("messages/")) return null;
  const segments = norm.slice("messages/".length).split("/");
  if (segments.length === 0 || segments.some((segment) => !safeSegment(segment))) return null;
  return `/files/${segments.map((segment) => encodeURIComponent(segment)).join("/")}`;
}

export function resolveInside(root: string, segments: string[]): string | null {
  if (segments.length === 0 || segments.some((segment) => !safeSegment(segment))) return null;
  let realRoot: string;
  try {
    realRoot = fs.realpathSync(path.resolve(root));
  } catch {
    return null;
  }
  const lexical = path.resolve(realRoot, ...segments);
  const relative = path.relative(realRoot, lexical);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return null;
  let realFile: string;
  try {
    realFile = fs.realpathSync(lexical);
  } catch {
    return null;
  }
  const realRelative = path.relative(realRoot, realFile);
  if (realRelative.startsWith("..") || path.isAbsolute(realRelative)) return null;
  try {
    if (!fs.statSync(realFile).isFile()) return null;
  } catch {
    return null;
  }
  return realFile;
}

export type RunLogResolution =
  | { status: "ok"; file: string }
  | { status: "missing" }
  | { status: "rejected" };

export function resolveRunLog(logsDir: string, logPath: string | null): RunLogResolution {
  if (!logPath || !logPath.trim()) return { status: "missing" };
  const name = runLogName(logPath, logsDir);
  if (!name) return { status: "rejected" };
  const runsRoot = path.resolve(logsDir, "runs");
  const lexical = path.resolve(runsRoot, name);
  if (path.relative(runsRoot, lexical) !== name) return { status: "rejected" };
  if (!fs.existsSync(lexical)) return { status: "missing" };
  const file = resolveInside(runsRoot, [name]);
  if (!file) return { status: "rejected" };
  return { status: "ok", file };
}

function runLogName(logPath: string, logsDir: string): string | null {
  const trimmed = logPath.trim();
  if (!trimmed || trimmed.includes("\0")) return null;
  const norm = trimmed.replace(/\\/g, "/");
  const runsRoot = path.resolve(logsDir, "runs");
  if (norm.startsWith("/")) {
    const relative = path.relative(runsRoot, path.resolve(norm));
    if (!relative || relative.startsWith("..") || relative.includes("/")) return null;
    return safeSegment(relative) ? relative : null;
  }
  let relative = norm.replace(/^\/+/, "");
  if (relative.startsWith("logs/")) relative = relative.slice("logs/".length);
  if (relative.startsWith("runs/")) relative = relative.slice("runs/".length);
  if (!safeSegment(relative)) return null;
  return relative;
}

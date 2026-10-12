import "server-only";
import fs from "node:fs";
import { logsDir } from "@/lib/env";
import { resolveRunLog } from "@/lib/safe-path";
import { parseTraceLog, type ParsedTrace } from "@/lib/trace-log";

const CAP = 8 * 1024 * 1024;

export function readRunLog(logPath: string | null): { parsed: ParsedTrace | null; note: string | null } {
  const resolved = resolveRunLog(logsDir(), logPath);
  if (resolved.status === "missing") {
    return {
      parsed: null,
      note: logPath ? `Log file is not on disk (${logPath}).` : "This run has no log path.",
    };
  }
  if (resolved.status === "rejected") {
    return { parsed: null, note: "Log path is outside logs/runs." };
  }
  const stat = fs.statSync(resolved.file);
  if (!stat.isFile()) return { parsed: null, note: "Log path is not a file." };
  const fd = fs.openSync(resolved.file, "r");
  try {
    const toRead = Math.min(stat.size, CAP);
    const buffer = Buffer.alloc(toRead);
    fs.readSync(fd, buffer, 0, toRead, 0);
    let text = buffer.toString("utf8");
    const truncated = stat.size > CAP;
    if (truncated) {
      const lastNl = text.lastIndexOf("\n");
      if (lastNl >= 0) text = text.slice(0, lastNl);
    }
    const parsed = parseTraceLog(text);
    parsed.truncated = truncated;
    return { parsed, note: null };
  } finally {
    fs.closeSync(fd);
  }
}

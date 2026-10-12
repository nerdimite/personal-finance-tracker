import Link from "next/link";
import { isImagePath } from "@/lib/content-type";
import { clip, formatDateTime, formatDuration, formatUsd, inr } from "@/lib/format";
import { messageFileHref } from "@/lib/safe-path";
import type { ParsedTrace, TraceTool } from "@/lib/trace-log";
import type { AttachmentRow, AuditRow, RunDetail, ToolCallRow } from "@/lib/types";

export function TraceView({
  run,
  parsed,
  logNote,
  attachments,
  toolCalls,
  audit,
}: {
  run: RunDetail;
  parsed: ParsedTrace | null;
  logNote: string | null;
  attachments: AttachmentRow[];
  toolCalls: ToolCallRow[];
  audit: AuditRow[];
}) {
  const finalText = chooseFinal(parsed?.finalText ?? null, run.resultText);
  return (
    <>
      <p className="muted"><Link href="/traces">Traces</Link></p>
      <div className="panel-head">
        <h1>Run {run.id}</h1>
        <span className={run.isError ? "badge bad" : "badge good"}>{run.isError ? "error" : "ok"}</span>
      </div>
      <p className="muted">
        {formatDateTime(run.startedAt)}
        {" · "}
        {formatDuration(run.durationMs)}
        {" · "}
        {run.numTurns ?? "—"} turns
        {" · "}
        {formatUsd(run.costUsd)}
        {run.sessionId ? ` · ${run.sessionId}` : ""}
      </p>
      {run.isError ? <p className="banner">This run ended with an error.</p> : null}

      <section className="panel">
        <h2>Message</h2>
        {run.messageSeq ? (
          <p className="muted note">
            <Link href={`/traces?message=${run.messageSeq}`}>Message {run.messageSeq}</Link>
            {run.folderPath ? ` · ${run.folderPath}` : ""}
            {run.messageReceivedAt ? ` · ${formatDateTime(run.messageReceivedAt)}` : ""}
          </p>
        ) : (
          <p className="empty">No message is linked to this run.</p>
        )}
        <p style={{ whiteSpace: "pre-wrap" }}>{run.messageText || "No message text."}</p>
        <AttachmentList attachments={attachments} />
      </section>

      <section className="panel">
        <h2>Turns</h2>
        {logNote ? <p className="empty">{logNote}</p> : null}
        {parsed?.truncated ? <p className="note">Log truncated at 8 MB.</p> : null}
        {parsed && parsed.skippedLines > 0 ? (
          <p className="note muted">Skipped {parsed.skippedLines} unreadable lines.</p>
        ) : null}
        {parsed && parsed.unknownTypes.length > 0 ? (
          <p className="note muted">Ignored event types: {parsed.unknownTypes.join(", ")}.</p>
        ) : null}
        {parsed?.userTexts.map((text) => (
          <p key={text} style={{ whiteSpace: "pre-wrap" }}>{text}</p>
        ))}
        {parsed?.turns.map((turn, index) => (
          <article className="turn" key={index}>
            <h3>Turn {index + 1}</h3>
            {turn.texts.map((text) => (
              <p key={text} style={{ whiteSpace: "pre-wrap" }}>{text}</p>
            ))}
            {turn.reasoning.length > 0 ? (
              <details>
                <summary>Reasoning</summary>
                {turn.reasoning.map((text) => {
                  const shown = clip(text, 4000);
                  return <pre key={text}>{shown.text}{shown.clipped ? "\n…" : ""}</pre>;
                })}
              </details>
            ) : null}
            {turn.tools.map((tool) => (
              <ToolBlock key={tool.id} tool={tool} />
            ))}
          </article>
        ))}
        {!parsed && toolCalls.length === 0 ? <p className="empty">No turn log and no stored tool calls.</p> : null}
      </section>

      <section className="panel">
        <h2>Final reply</h2>
        {finalText.log && finalText.db && finalText.log !== finalText.db ? (
          <>
            <p style={{ whiteSpace: "pre-wrap" }}>{finalText.log}</p>
            <p className="muted note">Stored result_text differs:</p>
            <p style={{ whiteSpace: "pre-wrap" }}>{finalText.db}</p>
          </>
        ) : (
          <p style={{ whiteSpace: "pre-wrap" }}>{finalText.log || finalText.db || "No final reply."}</p>
        )}
      </section>

      <section className="panel">
        <h2>Tool calls</h2>
        {toolCalls.length === 0 ? (
          <p className="empty">No tool_calls rows for this run.</p>
        ) : (
          <details open={!parsed}>
            <summary>{toolCalls.length} stored tool call{toolCalls.length === 1 ? "" : "s"}</summary>
            {toolCalls.map((call) => (
              <details key={call.seq} className={call.isError ? "sql error" : "sql"} open={call.isError}>
                <summary>
                  {call.seq}. {call.toolName}
                  {call.durationMs != null ? ` · ${formatDuration(call.durationMs)}` : ""}
                  {call.isError ? " · error" : ""}
                </summary>
                <pre>{pretty(call.input)}</pre>
                {call.output ? <pre>{clip(call.output).text}</pre> : null}
              </details>
            ))}
          </details>
        )}
      </section>

      <section className="panel">
        <h2>Database changes</h2>
        {audit.length === 0 ? (
          <p className="empty">No audit_log rows for this run.</p>
        ) : (
          <div className="scroll">
            <table>
              <caption className="sr-only">Rows changed by this run</caption>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Table</th>
                  <th>Op</th>
                  <th>Row</th>
                  <th>Change</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((row) => (
                  <tr key={row.id}>
                    <td className="num">{formatDateTime(row.at)}</td>
                    <td>{row.tableName}</td>
                    <td><span className={row.op === "DELETE" ? "badge bad" : "badge"}>{row.op}</span></td>
                    <td>{row.rowId ?? "—"}</td>
                    <td>
                      <details>
                        <summary>{summarize(row) || "row image"}</summary>
                        {row.oldRow != null ? <pre>{pretty(row.oldRow)}</pre> : null}
                        {row.newRow != null ? <pre>{pretty(row.newRow)}</pre> : null}
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function ToolBlock({ tool }: { tool: TraceTool }) {
  const summary = tool.query ? oneLine(tool.query) : tool.name;
  return (
    <details className={tool.isError ? "sql error" : "sql"} open={tool.isError}>
      <summary>
        <code>{tool.name}</code> {summary}
      </summary>
      {tool.query ? <pre>{tool.query}</pre> : tool.input != null ? <pre>{pretty(tool.input)}</pre> : null}
      {tool.result ? <pre>{clip(tool.result).text}</pre> : <p className="muted note">No result recorded.</p>}
    </details>
  );
}

function AttachmentList({ attachments }: { attachments: AttachmentRow[] }) {
  if (attachments.length === 0) return <p className="empty">No files.</p>;
  return (
    <ul className="file-list">
      {attachments.map((attachment) => {
        const href = messageFileHref(attachment.path);
        return (
          <li key={attachment.id}>
            {href ? <a href={href}>{attachment.path}</a> : <span>{attachment.path}</span>}
            {href && isImagePath(attachment.path) ? (
              <a href={href}><img className="shot" src={href} alt={attachment.path} /></a>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

function chooseFinal(logText: string | null, dbText: string | null): { log: string | null; db: string | null } {
  const log = logText?.trim() ? logText : null;
  const db = dbText?.trim() ? dbText : null;
  return { log, db };
}

function oneLine(value: string): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > 80 ? `${flat.slice(0, 79)}…` : flat;
}

function pretty(value: unknown): string {
  const shown = clip(typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "");
  return shown.clipped ? `${shown.text}\n…` : shown.text;
}

function summarize(row: AuditRow): string {
  const image = (row.op === "DELETE" ? row.oldRow : row.newRow) as Record<string, unknown> | null;
  if (!image || typeof image !== "object") return "";
  const bits: string[] = [];
  for (const key of ["path", "description", "merchant", "amount", "name", "status", "tool_name"]) {
    const value = image[key];
    if (value != null && value !== "") bits.push(`${key}: ${typeof value === "string" ? value : inrOrText(key, value)}`);
  }
  return bits.join(" · ");
}

function inrOrText(key: string, value: unknown): string {
  if (key === "amount" && (typeof value === "number" || typeof value === "string")) return inr(value);
  return String(value);
}

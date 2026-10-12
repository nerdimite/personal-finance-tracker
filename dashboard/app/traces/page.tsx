import type { Metadata } from "next";
import Link from "next/link";
import { parseMessageSeq } from "@/lib/filters";
import { formatDateTime, formatDuration, formatUsd, preview } from "@/lib/format";
import { listRuns, loadMessage } from "@/lib/queries";

export const metadata: Metadata = { title: "Traces · Ledger" };
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function TracesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const messageSeq = parseMessageSeq(await searchParams);
  const [runs, message] = await Promise.all([
    listRuns(messageSeq),
    messageSeq ? loadMessage(messageSeq) : Promise.resolve(null),
  ]);

  return (
    <>
      <header className="page-head">
        <h1>Traces</h1>
        {messageSeq ? <Link href="/traces">All runs</Link> : <p className="muted">{runs.length} agent run{runs.length === 1 ? "" : "s"}</p>}
      </header>
      {messageSeq ? (
        <section className="panel" style={{ margin: "1rem 0" }}>
          <h2>Message {messageSeq}</h2>
          {message ? (
            <p style={{ whiteSpace: "pre-wrap" }}>{message.text || "No text"}</p>
          ) : (
            <p className="empty">That message is not in the ledger.</p>
          )}
        </section>
      ) : null}
      <section>
        {runs.length === 0 ? (
          <p className="empty">No agent runs yet.</p>
        ) : (
          <div className="scroll">
            <table>
              <caption className="sr-only">Agent runs</caption>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Message</th>
                  <th className="num">Duration</th>
                  <th className="num">Turns</th>
                  <th className="num">Cost</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td className="num">{formatDateTime(run.startedAt)}</td>
                    <td>
                      <a className="rowlink" href={`/traces/${run.id}`}>
                        {preview(run.messageText || run.resultText)}
                      </a>
                    </td>
                    <td className="num">{formatDuration(run.durationMs)}</td>
                    <td className="num">{run.numTurns ?? "—"}</td>
                    <td className="num">{formatUsd(run.costUsd)}</td>
                    <td>
                      <span className={run.isError ? "badge bad" : "badge good"}>
                        {run.isError ? "error" : "ok"}
                      </span>
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

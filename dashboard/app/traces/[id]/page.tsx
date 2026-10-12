import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { TraceView } from "@/components/TraceView";
import { parseRunId } from "@/lib/filters";
import { messageAttachments, runAudit, runDetail, runToolCalls } from "@/lib/queries";
import { readRunLog } from "@/lib/run-log";

export const metadata: Metadata = { title: "Trace · Ledger" };
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function TracePage({ params }: { params: Promise<{ id: string }> }) {
  const { id: rawId } = await params;
  const id = parseRunId(rawId);
  if (!id) notFound();
  const run = await runDetail(id);
  if (!run) notFound();
  const [log, toolCalls, audit, attachments] = await Promise.all([
    Promise.resolve(readRunLog(run.logPath)),
    runToolCalls(id),
    runAudit(id),
    run.messageSeq ? messageAttachments(run.messageSeq) : Promise.resolve([]),
  ]);

  return (
    <div className="stack">
      <TraceView
        run={run}
        parsed={log.parsed}
        logNote={log.note}
        attachments={attachments}
        toolCalls={toolCalls}
        audit={audit}
      />
    </div>
  );
}

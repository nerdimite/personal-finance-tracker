import type { Metadata } from "next";
import Link from "next/link";
import { CopyButton } from "@/components/CopyButton";
import { QueueActions } from "@/components/QueueActions";
import { formatDate, formatDateTime, inr } from "@/lib/format";
import { listSplitwise, loadSplitwiseSummary } from "@/lib/queries";
import { queueFilter, splitwiseCopyText, type QueueFilter } from "@/lib/splitwise";

export const metadata: Metadata = { title: "Splitwise · Ledger" };
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const FILTERS: { status: QueueFilter; label: string }[] = [
  { status: "pending", label: "Pending" },
  { status: "added", label: "Added" },
  { status: "cancelled", label: "Dismissed" },
];

export default async function SplitwisePage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const raw = await searchParams;
  const value = Array.isArray(raw.status) ? raw.status[0] : raw.status;
  const status = queueFilter(value);
  const [summary, items] = await Promise.all([
    loadSplitwiseSummary(),
    listSplitwise(status),
  ]);

  const owedTotal = summary.people.reduce((sum, p) => sum + Number(p.owed), 0);
  return (
    <>
      <header className="page-head">
        <h1>Splitwise</h1>
        <p className="muted">{summary.pendingCount} to enter</p>
      </header>

      <section className="owed-strip" aria-label="Owed to you on pending items">
        <div className="owed">
          <span>Owed to you</span>
          <strong className="num-serif">{inr(owedTotal)}</strong>
        </div>
        {summary.people.map((person) => (
          <div className="owed" key={person.key}>
            <span>{person.name === "others" ? "Others" : person.name}</span>
            <strong className="num-serif">{inr(person.owed)}</strong>
          </div>
        ))}
      </section>

      <nav className="seg" aria-label="Queue status">
        {FILTERS.map((filter) => (
          <Link
            key={filter.status}
            href={filter.status === "pending" ? "/splitwise" : `/splitwise?status=${filter.status}`}
            aria-current={filter.status === status ? "page" : undefined}
          >
            {filter.label}
          </Link>
        ))}
      </nav>

      {items.length === 0 ? (
        <p className="empty">{emptyLabel(status)}</p>
      ) : (
        <div className="queue">
          {items.map((item) => {
            const copyText = splitwiseCopyText({
              description: item.description,
              totalAmount: item.totalAmount,
              paidBy: item.paidBy,
              splitMethod: item.splitMethod,
              shares: item.shares,
              othersOwe: item.othersOwe,
            });
            return (
              <article className={item.status === "pending" ? "queue-card" : "queue-card done"} key={item.id}>
                <h2 className="sr-only">{item.description}</h2>
                <div className="panel-head" style={{ marginBottom: 0, display: item.status === "pending" ? "none" : undefined }}>
                  {item.status !== "pending" ? (
                    <span className={item.status === "added" ? "badge good" : "badge"}>
                      {item.status === "cancelled" ? "dismissed" : item.status}
                    </span>
                  ) : null}
                </div>
                <p className="when">
                  {formatDate(item.date)}
                  {item.groupName ? ` · ${item.groupName}` : ""}
                  {" · "}
                  <Link href={`/transactions?tx=${item.transactionId}`}>Transaction {item.transactionId}</Link>
                  {item.messageSeq ? (
                    <>
                      {" · "}
                      <Link href={`/traces?message=${item.messageSeq}`}>Message {item.messageSeq}</Link>
                    </>
                  ) : null}
                </p>
                <pre>{copyText}</pre>
                {item.items.length > 0 ? (
                  <ul className="file-list">
                    {item.items.map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                ) : null}
                {item.notes ? <p className="note">{item.notes}</p> : null}
                {item.addedAt ? <p className="muted note">Marked added {formatDateTime(item.addedAt)}</p> : null}
                <div className="queue-actions">
                  <CopyButton text={copyText} />
                  {item.status === "pending" ? <QueueActions id={item.id} /> : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </>
  );
}

function emptyLabel(status: QueueFilter): string {
  if (status === "added") return "No items have been marked added.";
  if (status === "cancelled") return "No dismissed items.";
  return "Nothing waiting to add to Splitwise.";
}

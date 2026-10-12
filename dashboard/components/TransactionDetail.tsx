import Link from "next/link";
import { isImagePath } from "@/lib/content-type";
import { transactionHref, type TransactionFilters } from "@/lib/filters";
import { formatDate, formatDateTime, inr } from "@/lib/format";
import { messageFileHref } from "@/lib/safe-path";
import type { TransactionDetail as Detail } from "@/lib/types";

export function TransactionDetail({
  detail,
  filters,
}: {
  detail: Detail | null;
  filters: TransactionFilters;
}) {
  if (!detail) {
    return (
      <aside className="sheet" aria-label="Transaction detail">
        <div className="panel-head"><h2>Transaction not found</h2><Link className="close" href={transactionHref(filters, { txId: null })} aria-label="Close (Esc)">×</Link></div>
      </aside>
    );
  }

  return (
    <aside className="sheet" aria-label="Transaction detail">
      <div className="panel-head">
        <h2>{detail.description || detail.merchant || `Transaction ${detail.id}`}</h2>
        <Link className="close" href={transactionHref(filters, { txId: null })} aria-label="Close (Esc)">×</Link>
      </div>
      <div className="meta">
        <span>{formatDate(detail.date)}</span>
        <span className={detail.status === "void" ? "badge bad" : "badge"}>{detail.status}</span>
        {detail.merchant ? <span>{detail.merchant}</span> : null}
        {detail.paymentMethod ? <span className="muted">{detail.paymentMethod}</span> : null}
        {detail.tripName ? <span>Trip {detail.tripName}</span> : null}
        {detail.externalRef ? <span className="muted">Ref {detail.externalRef}</span> : null}
      </div>
      {detail.notes ? <p>{detail.notes}</p> : null}
      {detail.tags.length > 0 ? <p className="muted">{detail.tags.join(", ")}</p> : null}

      <h3>Postings</h3>
      <table>
        <caption className="sr-only">Postings</caption>
        <thead>
          <tr>
            <th>Account</th>
            <th className="num">Amount</th>
          </tr>
        </thead>
        <tbody>
          {detail.postings.map((posting) => (
            <tr key={posting.id}>
              <td>
                {posting.path}
                {posting.personName ? <p className="sub muted">{posting.personName}</p> : null}
                {posting.memo ? <p className="sub muted">{posting.memo}</p> : null}
              </td>
              <td className="num">{inr(posting.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Source message</h3>
      {detail.message ? (
        <>
          <p style={{ whiteSpace: "pre-wrap" }}>{detail.message.text || "No text"}</p>
          <p className="muted note">
            {formatDateTime(detail.message.receivedAt)}
            {detail.message.folderPath ? ` · ${detail.message.folderPath}` : ""}
          </p>
        </>
      ) : (
        <p className="empty">
          {detail.sourceMessageSeq
            ? `Message ${detail.sourceMessageSeq} is not in the ledger.`
            : "No source message."}
        </p>
      )}
      {detail.runs.length > 0 ? (
        <ul className="file-list">
          {detail.runs.map((run) => (
            <li key={run.id}>
              <Link href={`/traces/${run.id}`}>
                Message trace {run.id}
                {run.isError ? " (error)" : ""}
              </Link>
            </li>
          ))}
        </ul>
      ) : detail.sourceMessageSeq ? (
        <p>
          <Link href={`/traces?message=${detail.sourceMessageSeq}`}>Message trace</Link>
        </p>
      ) : null}

      {detail.queue.length > 0 ? (
        <>
          <h3>Splitwise</h3>
          <ul className="file-list">
            {detail.queue.map((item) => (
              <li key={item.id}>
                <span className={item.status === "added" ? "badge good" : item.status === "pending" ? "badge warn" : "badge"}>
                  {item.status}
                </span>
                {" "}
                {item.description}
                <span className="muted"> · others owe {inr(item.othersOwe)}</span>
                {" · "}
                <Link href={item.status === "pending" ? "/splitwise" : `/splitwise?status=${item.status}`}>
                  Open queue
                </Link>
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h3>Attachments</h3>
      {detail.attachments.length === 0 ? (
        <p className="empty">No attachments.</p>
      ) : (
        <ul className="file-list">
          {detail.attachments.map((attachment) => {
            const href = messageFileHref(attachment.path);
            return (
              <li key={attachment.id}>
                {href ? (
                  <a href={href}>{attachment.path}</a>
                ) : (
                  <span>{attachment.path}</span>
                )}
                {attachment.kind ? <span className="muted"> · {attachment.kind}</span> : null}
                {href && isImagePath(attachment.path) ? (
                  <a href={href}>
                    <img className="shot" src={href} alt={attachment.path} />
                  </a>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </aside>
  );
}

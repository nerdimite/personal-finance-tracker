import Link from "next/link";
import { inr0, pctChange } from "@/lib/format";
import type { SubCategory } from "@/lib/overview-extras";
import type { CategoryTotal } from "@/lib/types";

export function CategoryChart({
  rows,
  subs,
  thisLabel,
  lastLabel,
}: {
  rows: CategoryTotal[];
  subs: SubCategory[];
  thisLabel: string;
  lastLabel: string;
}) {
  if (rows.length === 0) {
    return <p className="empty">No spending recorded in {thisLabel} or {lastLabel} yet. Log something in Telegram and it lands here.</p>;
  }
  const max = Math.max(1, ...rows.map((row) => Math.max(Number(row.thisTotal), Number(row.lastTotal))));
  // With no history yet, every row would say "new" and every tick would sit at zero: show neither.
  const hasHistory = rows.some((row) => Number(row.lastTotal) !== 0);
  return (
    <div className="cats" role="list">
      {rows.map((row, i) => {
        const children = subs.filter((s) => s.top === row.topCategory && (Number(s.thisTotal) !== 0 || Number(s.lastTotal) !== 0));
        const change = pctChange(row.thisTotal, row.lastTotal);
        const body = (
          <>
            <span className="cat-name">{label(row.topCategory)}</span>
            <span className="cat-bars" aria-hidden="true">
              <span className="slab" style={{ width: `${pct(row.thisTotal, max)}%`, animationDelay: `${i * 40}ms` }} />
              {Number(row.lastTotal) > 0 ? (
                <span className="tick-last" style={{ left: `${pct(row.lastTotal, max)}%` }} title={`${lastLabel}: ${inr0(row.lastTotal)}`} />
              ) : null}
            </span>
            <span className="cat-amt num">{inr0(row.thisTotal)}</span>
            <span className={`cat-chg num ${change.tone}`}>{hasHistory ? change.text : ""}</span>
          </>
        );
        return (
          <div role="listitem" key={row.topCategory}>
            {children.length > 0 ? (
              <details className="cat">
                <summary className="cat-row">{body}</summary>
                <div className="cat-kids">
                  <Link className="cat-all" href={`/transactions?category=expense:${row.topCategory}`}>All {label(row.topCategory)} transactions →</Link>
                  {children.map((child) => (
                    <Link key={child.sub} className="cat-row kid" href={`/transactions?category=expense:${row.topCategory}${child.sub === "(direct)" ? "" : `:${child.sub}`}`}>
                      <span className="cat-name">{child.sub === "(direct)" ? "uncategorised within" : label(child.sub)}</span>
                      <span className="cat-bars" aria-hidden="true">
                        <span className="slab kid" style={{ width: `${pct(child.thisTotal, max)}%` }} />
                      </span>
                      <span className="cat-amt num">{inr0(child.thisTotal)}</span>
                      <span className="cat-chg num muted">{hasHistory && Number(child.lastTotal) ? inr0(child.lastTotal) : ""}</span>
                    </Link>
                  ))}
                </div>
              </details>
            ) : (
              <div className="cat"><div className="cat-row">{body}</div></div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function label(value: string): string {
  return value.replaceAll("_", " ");
}

function pct(value: string, max: number): number {
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0) return 0;
  return Math.max(1.5, (amount / max) * 100);
}

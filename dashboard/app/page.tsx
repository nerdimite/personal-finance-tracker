import Link from "next/link";
import { CategoryChart } from "@/components/CategoryChart";
import { SpendChart } from "@/components/SpendChart";
import { formatAge, formatDate, formatMonth, inr0 } from "@/lib/format";
import { loadAccountLines, loadDailySeries, loadLenses, loadMoneyMoved, loadSubCategories } from "@/lib/overview-extras";
import { loadOverview } from "@/lib/queries";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function OverviewPage() {
  const [{ totals, categories, merchants, imports, splitwise }, series, subs, lenses, accounts, moved] = await Promise.all([
    loadOverview(),
    loadDailySeries(),
    loadSubCategories(),
    loadLenses(),
    loadAccountLines(),
    loadMoneyMoved(),
  ]);
  const thisLabel = formatMonth(totals.thisMonth);
  const lastLabel = formatMonth(totals.lastMonth);
  const spent = Number(totals.thisTotal);
  const diff = spent - series.lastAtToday;
  const owedTotal = splitwise.people.reduce((sum, p) => sum + Number(p.owed), 0);
  const [monthName, year] = thisLabel.split(" ");
  const todayPoint = series.points[series.today - 1];
  const prevPoint = series.points[series.today - 2];
  const spentToday = (todayPoint?.thisCum ?? 0) - (prevPoint?.thisCum ?? 0);

  return (
    <>
      <header className="page-head">
        <h1>{monthName} <span className="dim">{year}</span></h1>
      </header>

      <section className="hero" aria-labelledby="h-spent">
        <div className="hero-figure">
          <h2 id="h-spent" className="section-title">Spent so far</h2>
          <p className="figure num-serif">{inr0(spent)}</p>
          <p className={`compare ${diff > 0 ? "up" : diff < 0 ? "down" : ""}`}>
            {series.lastAtToday === 0 && spent === 0
              ? "Nothing logged yet this month or last."
              : diff === 0
                ? `Level with ${lastLabel.split(" ")[0]} at day ${series.today}`
                : `${diff > 0 ? "▲" : "▼"} ${inr0(Math.abs(diff))} ${diff > 0 ? "more" : "less"} than ${lastLabel.split(" ")[0]} at day ${series.today}`}
          </p>
          <p className="fineprint">
            {totals.postingCount} expense line{totals.postingCount === 1 ? "" : "s"}. Excludes transfers, card bills, loan repayments and shares others owe you.
          </p>
        </div>
        <SpendChart series={series} thisLabel={thisLabel} lastLabel={lastLabel} />
      </section>

      <section className="waiting" aria-label="Waiting on you">
        <Link className={`tile ${splitwise.pendingCount > 0 ? "hot" : ""}`} href="/splitwise">
          <span className="tile-title">Splitwise queue</span>
          <span className="tile-num num">{splitwise.pendingCount}</span>
          <span className="tile-sub">
            {splitwise.pendingCount === 0
              ? "Nothing to enter"
              : `${inr0(owedTotal)} owed to you · oldest ${formatAge(splitwise.oldestCreatedAt)}`}
          </span>
        </Link>
        <div className={`tile ${imports.length > 0 ? "hot" : ""}`} title="Approve or discard imports in Telegram">
          <span className="tile-title">Imports to approve</span>
          <span className="tile-num num">{imports.length}</span>
          <span className="tile-sub">
            {imports.length === 0
              ? "None waiting"
              : imports.map((b) => `${fileLabel(b.filePath)} · ${b.newCount ?? 0} new${b.duplicateCount ? ` · ${b.duplicateCount} dup` : ""}`).join("; ")}
          </span>
        </div>
        <div className="tile">
          <span className="tile-title">Spent today</span>
          <span className="tile-num num">{inr0(spentToday)}</span>
          <span className="tile-sub">{inr0(spent / Math.max(1, series.today))} a day on average this month</span>
        </div>
      </section>

      <div className="split">
        <section aria-labelledby="h-cat">
          <div className="section-head">
            <h2 id="h-cat" className="section-title">Where it went</h2>
            {categories.some((c) => Number(c.lastTotal) !== 0) ? (
              <span className="chart-key small">
                <span><i className="k-this" />{monthName}</span>
                <span><i className="k-tick" />{lastLabel.split(" ")[0]}</span>
              </span>
            ) : (
              <span className="muted small">Comparison with {lastLabel.split(" ")[0]} appears once it has data</span>
            )}
          </div>
          <CategoryChart rows={categories} subs={subs} thisLabel={thisLabel} lastLabel={lastLabel} />
        </section>

        <div className="side-col">
          <section aria-labelledby="h-lens">
            <div className="section-head"><h2 id="h-lens" className="section-title">Lenses</h2></div>
            <div className="lenses">
              {lenses.map((lens) => {
                const inner = (
                  <>
                    <span className="lens-label">{lens.label}</span>
                    <span className="lens-num num-serif">{inr0(lens.total)}</span>
                    <span className="lens-sub">{lens.detail}</span>
                  </>
                );
                return lens.href ? (
                  <Link key={lens.key} className="lens" href={lens.href}>{inner}</Link>
                ) : (
                  <div key={lens.key} className="lens">{inner}</div>
                );
              })}
            </div>
          </section>

          <section aria-labelledby="h-bal">
            <div className="section-head"><h2 id="h-bal" className="section-title">Balances &amp; dues</h2></div>
            {accounts.length === 0 ? (
              <p className="empty">No accounts yet. They appear as the agent records your first statement or entry.</p>
            ) : (
              <table className="ledger">
                <caption className="sr-only">Account balances, receivables and payables</caption>
                <tbody>
                  {accounts.map((row) => {
                    const amount = Number(row.balance);
                    return (
                      <tr key={row.accountId}>
                        <th scope="row">
                          <span className="row-title">{row.label}</span>
                          <span className="row-sub">
                            {row.subtype === "credit_card" && amount > 0 ? "paid ahead · card spends not logged yet · " : ""}
                            {row.detail}
                          </span>
                        </th>
                        <td className={`num amt ${amount < 0 ? "neg" : ""}`}>{inr0(amount)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
        </div>
      </div>

      {moved.length > 0 ? (
        <section aria-labelledby="h-moved" className="block">
          <div className="section-head">
            <h2 id="h-moved" className="section-title">Bills &amp; transfers</h2>
            <span className="muted small">Not counted as spending: the spend happened when you used the card or loan.</span>
          </div>
          <div className="moved">
            {moved.map((group) => (
              <details key={group.key} className="move-group" open={group.key === "cards"}>
                <summary>
                  <span className="move-label">{group.label}</span>
                  <span className="muted small">{group.items.length}</span>
                  <span className="num">{inr0(group.total)}</span>
                </summary>
                <ul>
                  {group.items.map((item) => (
                    <li key={item.id}>
                      <span className="mono-cell">{formatDate(item.date)}</span>
                      <Link className="rowlink-plain" href={`/transactions?tx=${item.id}`}>{item.target}</Link>
                      <span className="muted small">{item.description}</span>
                      <span className="num">{inr0(item.amount)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </div>
        </section>
      ) : null}

      {merchants.length > 1 ? (
        <section aria-labelledby="h-mer" className="block">
          <div className="section-head"><h2 id="h-mer" className="section-title">Merchants this month</h2></div>
          <ol className="merchants">
            {merchants.map((row) => (
              <li key={row.merchant}>
                <span>{row.merchant}</span>
                <span className="num muted">{row.transactions}×</span>
                <span className="num">{inr0(row.total)}</span>
              </li>
            ))}
          </ol>
        </section>
      ) : null}
    </>
  );
}

function fileLabel(filePath: string | null): string {
  if (!filePath) return "Import";
  return (filePath.split("/").pop() || filePath).replace(/^\d+_/, "");
}

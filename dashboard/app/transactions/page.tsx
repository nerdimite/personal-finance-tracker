import type { Metadata } from "next";
import { Shortcuts } from "@/components/Shortcuts";
import { TransactionDetail } from "@/components/TransactionDetail";
import { transactionHref, parseTransactionFilters } from "@/lib/filters";
import { formatDate, inr0 } from "@/lib/format";
import { filterOptions, listTransactions, transactionDetail } from "@/lib/queries";

export const metadata: Metadata = { title: "Transactions · Ledger" };
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function TransactionsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const filters = parseTransactionFilters(await searchParams);
  const [options, list, detail] = await Promise.all([
    filterOptions(),
    listTransactions(filters),
    filters.txId ? transactionDetail(filters.txId) : Promise.resolve(undefined),
  ]);
  const moreActive = Boolean(filters.accountId || filters.category || filters.personId || filters.tripId);
  const closeHref = transactionHref(filters, { txId: null });

  return (
    <>
      <Shortcuts closeHref={detail !== undefined ? closeHref : null} />
      <header className="page-head">
        <h1>Transactions</h1>
        <p className="muted small">
          {list.total} recorded{list.total > list.rows.length ? ` · showing ${list.rows.length}` : ""}
        </p>
      </header>

      <form className="toolbar" method="get">
        <label className="search">
          <span className="sr-only">Search</span>
          <input name="q" type="search" defaultValue={filters.q ?? ""} placeholder="Search merchant, note, reference   /" />
        </label>
        <label>
          <span className="sr-only">Month</span>
          <select name="month" defaultValue={filters.month ?? ""}>
            <option value="">All months</option>
            {options.months.map((month) => (
              <option key={month} value={month}>{month}</option>
            ))}
          </select>
        </label>
        <details className="more" open={moreActive}>
          <summary>More filters{moreActive ? " ·" : ""}</summary>
          <div className="more-body">
            <label>
              Account
              <select name="account" defaultValue={filters.accountId ?? ""}>
                <option value="">Any</option>
                {options.accounts.map((account) => (
                  <option key={account.id} value={account.id}>{account.path}</option>
                ))}
              </select>
            </label>
            <label>
              Category
              <input name="category" list="categories" defaultValue={filters.category ?? ""} placeholder="expense:food" />
              <datalist id="categories">
                {options.categories.map((path) => (
                  <option key={path} value={path} />
                ))}
              </datalist>
            </label>
            <label>
              Person
              <select name="person" defaultValue={filters.personId ?? ""}>
                <option value="">Anyone</option>
                {options.people.map((person) => (
                  <option key={person.id} value={person.id}>{person.name}</option>
                ))}
              </select>
            </label>
            <label>
              Trip
              <select name="trip" defaultValue={filters.tripId ?? ""}>
                <option value="">Any</option>
                {options.trips.map((trip) => (
                  <option key={trip.id} value={trip.id}>{trip.name}</option>
                ))}
              </select>
            </label>
          </div>
        </details>
        <button type="submit" className="flat">Apply</button>
        <a className="reset" href="/transactions">Reset</a>
      </form>

      {list.rows.length === 0 ? (
        <p className="empty">No transactions match these filters.</p>
      ) : (
        <div className="scroll">
          <table className="tx-table">
            <caption className="sr-only">Transactions</caption>
            <thead>
              <tr>
                <th>Date</th>
                <th>Description</th>
                <th>Category</th>
                <th className="num">Amount</th>
              </tr>
            </thead>
            <tbody>
              {list.rows.map((row) => {
                const cats = row.categories ? row.categories.split(", ") : [];
                const leafs = cats.map((c) => c.split(":").slice(1).join(" › ").replaceAll("_", " "));
                const sub = [
                  row.merchant && row.merchant !== row.description ? row.merchant : null,
                  row.paymentMethod,
                  row.people,
                  row.tripName,
                  row.status === "void" ? "void" : null,
                ].filter(Boolean).join(" · ");
                return (
                  <tr
                    key={row.id}
                    className={[row.status === "void" ? "void" : "", filters.txId === row.id ? "selected" : ""].filter(Boolean).join(" ")}
                  >
                    <td className="mono-cell nowrap">{formatDate(row.date)}</td>
                    <td>
                      <a className="rowlink" href={transactionHref(filters, { txId: row.id })}>
                        {row.description || row.merchant || "Untitled"}
                      </a>
                      {sub ? <span className="row-sub">{sub}</span> : null}
                    </td>
                    <td className="cat-cell" title={row.categories ?? undefined}>
                      {row.kind !== "expense" ? <span className="kind">{row.kind}</span> : null}
                      {leafs.length > 0 ? (leafs.length > 2 ? `${leafs[0]} +${leafs.length - 1}` : leafs.join(", ")) : "—"}
                    </td>
                    <td className={row.kind === "income" ? "num amt in" : "num amt"}>{inr0(row.amount)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {detail !== undefined ? <TransactionDetail detail={detail} filters={filters} /> : null}
    </>
  );
}

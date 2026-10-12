import "server-only";
import { query } from "@/lib/db";

export type DailyPoint = { day: number; thisCum: number | null; lastCum: number };
export type DailySeries = {
  points: DailyPoint[];
  today: number;
  daysInMonth: number;
  lastAtToday: number;
};
export type SubCategory = { top: string; sub: string; thisTotal: string; lastTotal: string };
export type Lens = { key: string; label: string; total: string; detail: string; href: string | null };
export type AccountLine = {
  accountId: string;
  label: string;
  detail: string;
  kind: string;
  subtype: string | null;
  balance: string;
};
export type PendingCounts = { splitwise: number; imports: number };
export type MoveGroup = { key: string; label: string; total: number; items: { id: string; date: string; description: string; amount: number; target: string }[] };

const BOUNDS = `
  SELECT
    (date_trunc('month', timezone('Asia/Kolkata', now())))::date AS this_month,
    (date_trunc('month', timezone('Asia/Kolkata', now())) - interval '1 month')::date AS last_month,
    timezone('Asia/Kolkata', now())::date AS today
`;

/** Cumulative expense by day of month, this month vs last. */
export async function loadDailySeries(): Promise<DailySeries> {
  const rows = await query<{ month: "this" | "last"; day: number; total: string }>(`
    WITH b AS (${BOUNDS})
    SELECT
      CASE WHEN date_trunc('month', v.date)::date = b.this_month THEN 'this' ELSE 'last' END AS month,
      EXTRACT(DAY FROM v.date)::int AS day,
      SUM(v.amount)::text AS total
    FROM v_spend v CROSS JOIN b
    WHERE v.date >= b.last_month AND v.date < (b.this_month + interval '1 month')::date
    GROUP BY 1, 2
  `);
  const meta = await query<{ today: number; days_in_month: number; last_days: number }>(`
    WITH b AS (${BOUNDS})
    SELECT
      EXTRACT(DAY FROM b.today)::int AS today,
      EXTRACT(DAY FROM (b.this_month + interval '1 month' - interval '1 day'))::int AS days_in_month,
      EXTRACT(DAY FROM (b.this_month - interval '1 day'))::int AS last_days
    FROM b
  `);
  const { today, days_in_month: daysInMonth, last_days: lastDays } = meta[0] ?? {
    today: 1,
    days_in_month: 30,
    last_days: 30,
  };
  const byDay = (month: "this" | "last") => {
    const map = new Map<number, number>();
    for (const row of rows) if (row.month === month) map.set(row.day, Number(row.total));
    return map;
  };
  const thisDays = byDay("this");
  const lastDaysMap = byDay("last");
  const points: DailyPoint[] = [];
  let thisCum = 0;
  let lastCum = 0;
  let lastAtToday = 0;
  for (let day = 1; day <= daysInMonth; day += 1) {
    thisCum += thisDays.get(day) ?? 0;
    if (day <= lastDays) lastCum += lastDaysMap.get(day) ?? 0;
    if (day === today) lastAtToday = lastCum;
    points.push({ day, thisCum: day <= today ? thisCum : null, lastCum });
  }
  return { points, today, daysInMonth, lastAtToday };
}

/** Second-level categories under each top-level category, this month vs last. */
export async function loadSubCategories(): Promise<SubCategory[]> {
  const rows = await query<{ top: string; sub: string; this_total: string; last_total: string }>(`
    WITH b AS (${BOUNDS})
    SELECT
      split_part(v.category_path, ':', 2) AS top,
      COALESCE(NULLIF(split_part(v.category_path, ':', 3), ''), '(direct)') AS sub,
      COALESCE(SUM(v.total) FILTER (WHERE v.month = b.this_month), 0)::text AS this_total,
      COALESCE(SUM(v.total) FILTER (WHERE v.month = b.last_month), 0)::text AS last_total
    FROM v_monthly_by_category v CROSS JOIN b
    WHERE v.month IN (b.this_month, b.last_month)
    GROUP BY 1, 2
    ORDER BY 1, COALESCE(SUM(v.total) FILTER (WHERE v.month = b.this_month), 0) DESC
  `);
  return rows.map((row) => ({ top: row.top, sub: row.sub, thisTotal: row.this_total, lastTotal: row.last_total }));
}

/** Cross-cutting views: trips this month, spend with a partner, local rides. */
export async function loadLenses(): Promise<Lens[]> {
  const [trips, partner, rides] = await Promise.all([
    query<{ trip_id: string; trip_name: string; total: string; n: number; start_date: string | null; end_date: string | null }>(`
      WITH b AS (${BOUNDS})
      SELECT v.trip_id::text AS trip_id, MAX(v.trip_name) AS trip_name, SUM(v.amount)::text AS total,
             COUNT(DISTINCT v.transaction_id)::int AS n,
             MAX(t.start_date)::text AS start_date, MAX(t.end_date)::text AS end_date
      FROM v_spend v CROSS JOIN b
      JOIN trips t ON t.id = v.trip_id
      WHERE v.trip_id IS NOT NULL
        AND v.date >= b.last_month AND v.date < (b.this_month + interval '1 month')::date
      GROUP BY v.trip_id
      ORDER BY MAX(v.date) DESC
      LIMIT 2
    `),
    query<{ person_id: string; name: string; total: string; n: number }>(`
      WITH b AS (${BOUNDS})
      SELECT pe.id::text AS person_id, pe.name, SUM(v.amount)::text AS total, COUNT(DISTINCT v.transaction_id)::int AS n
      FROM v_spend v CROSS JOIN b
      JOIN people pe ON pe.id = v.person_id AND pe.relation = 'partner'
      WHERE v.date >= b.this_month AND v.date < (b.this_month + interval '1 month')::date
      GROUP BY pe.id, pe.name
      ORDER BY SUM(v.amount) DESC
      LIMIT 1
    `),
    query<{ total: string; n: number }>(`
      WITH b AS (${BOUNDS})
      SELECT COALESCE(SUM(v.amount), 0)::text AS total, COUNT(DISTINCT v.transaction_id)::int AS n
      FROM v_spend v CROSS JOIN b
      WHERE v.category_path LIKE 'expense:transport:local:%'
        AND v.date >= b.this_month AND v.date < (b.this_month + interval '1 month')::date
    `),
  ]);
  const lenses: Lens[] = trips.map((trip) => ({
    key: `trip-${trip.trip_id}`,
    label: trip.trip_name,
    total: trip.total,
    detail: `${trip.n} txn${trip.n === 1 ? "" : "s"}`,
    href: `/transactions?trip=${trip.trip_id}`,
  }));
  const p = partner[0];
  lenses.push(
    p
      ? { key: "partner", label: `With ${p.name}`, total: p.total, detail: `${p.n} txn${p.n === 1 ? "" : "s"}`, href: `/transactions?person=${p.person_id}` }
      : { key: "partner", label: "With partner", total: "0", detail: "nothing tagged yet", href: null },
  );
  const r = rides[0];
  lenses.push({
    key: "rides",
    label: "Local rides",
    total: r?.total ?? "0",
    detail: r && r.n > 0 ? `${r.n} ride${r.n === 1 ? "" : "s"} · avg ₹${Math.round(Number(r.total) / r.n)}` : "no rides yet",
    href: "/transactions?category=expense:transport:local",
  });
  return lenses;
}

/** Real-money accounts with human labels (institution + last4), people balances folded in. */
export async function loadAccountLines(): Promise<AccountLine[]> {
  const rows = await query<{
    account_id: string;
    path: string;
    kind: string;
    subtype: string | null;
    institution: string | null;
    last4: string | null;
    person_name: string | null;
    balance: string;
  }>(`
    SELECT b.account_id::text AS account_id, b.path, b.kind, b.subtype, a.institution, a.last4,
           pe.name AS person_name, b.balance::text AS balance
    FROM v_balances b
    JOIN accounts a ON a.id = b.account_id
    LEFT JOIN people pe ON pe.id = a.person_id
    WHERE b.kind IN ('asset', 'liability') AND a.is_active
    ORDER BY CASE b.subtype WHEN 'bank' THEN 0 WHEN 'cash' THEN 1 WHEN 'wallet' THEN 1 WHEN 'credit_card' THEN 2
                            WHEN 'investment' THEN 3 WHEN 'payable' THEN 4 WHEN 'receivable' THEN 5 ELSE 6 END,
             b.path
  `);
  return rows.map((row) => ({
    accountId: row.account_id,
    label: accountLabel(row),
    detail: row.last4 ? `••${row.last4} · ${row.path}` : row.path,
    kind: row.kind,
    subtype: row.subtype,
    balance: row.balance,
  }));
}

function accountLabel(row: { path: string; institution: string | null; last4: string | null; person_name: string | null; subtype: string | null }): string {
  const leaf = row.path.split(":").pop() ?? row.path;
  const pretty = leaf.replaceAll("_", " ");
  if (row.person_name) {
    if (row.path.startsWith("liability:loan:")) return `${row.person_name} · loan`;
    if (row.path.startsWith("asset:held_by:")) return `Held by ${row.person_name}`;
    return row.subtype === "payable" ? `You owe ${row.person_name}` : `${row.person_name} owes you`;
  }
  if (row.subtype === "credit_card") return friendlyName(row.path, row.institution, row.subtype);
  return row.institution || pretty.replace(/\b\w/g, (c) => c.toUpperCase());
}

export async function loadPendingCounts(): Promise<PendingCounts> {
  try {
    const rows = await query<{ splitwise: number; imports: number }>(`
      SELECT
        (SELECT COUNT(*) FROM splitwise_queue WHERE status = 'pending')::int AS splitwise,
        (SELECT COUNT(*) FROM import_batches WHERE status = 'summarized')::int AS imports
    `);
    return rows[0] ?? { splitwise: 0, imports: 0 };
  } catch {
    return { splitwise: 0, imports: 0 };
  }
}

/**
 * Money that moved this month without being spend: card bills, loan repayments,
 * investments, and transfers between own accounts. A transaction lands in a group
 * by the account that received money.
 */
export async function loadMoneyMoved(): Promise<MoveGroup[]> {
  const rows = await query<{ id: string; date: string; description: string | null; amount: string; path: string; subtype: string | null; institution: string | null; from_subtype: string | null }>(`
    WITH b AS (${BOUNDS}),
    tx AS (
      SELECT t.id FROM transactions t CROSS JOIN b
      WHERE t.status <> 'void' AND t.date >= b.this_month AND t.date < (b.this_month + interval '1 month')::date
        AND NOT EXISTS (
          SELECT 1 FROM postings p JOIN accounts a ON a.id = p.account_id
          WHERE p.transaction_id = t.id AND a.kind IN ('expense', 'income', 'equity')
        )
    )
    SELECT t.id::text AS id, t.date::text AS date, t.description, p.amount::text AS amount, a.path, a.subtype, a.institution,
           (SELECT a2.subtype FROM postings p2 JOIN accounts a2 ON a2.id = p2.account_id
             WHERE p2.transaction_id = t.id AND p2.amount < 0 ORDER BY p2.amount LIMIT 1) AS from_subtype
    FROM tx JOIN transactions t ON t.id = tx.id
    JOIN postings p ON p.transaction_id = t.id AND p.amount > 0
    JOIN accounts a ON a.id = p.account_id
    WHERE a.kind IN ('asset', 'liability')
    ORDER BY t.date DESC, t.id DESC
  `);
  const groups: Record<string, MoveGroup> = {
    cards: { key: "cards", label: "Card bills paid", total: 0, items: [] },
    loans: { key: "loans", label: "Loan repayments", total: 0, items: [] },
    invest: { key: "invest", label: "Invested", total: 0, items: [] },
    own: { key: "own", label: "Between your accounts", total: 0, items: [] },
    back: { key: "back", label: "Received back from people", total: 0, items: [] },
  };
  const seen = new Set<string>();
  for (const row of rows) {
    if (seen.has(row.id)) continue;
    const key = row.subtype === "credit_card" ? "cards"
      : row.path.startsWith("liability:loan:") ? "loans"
      : row.subtype === "investment" ? "invest"
      : OWN.has(row.subtype ?? "") && OWN.has(row.from_subtype ?? "") ? "own"
      : OWN.has(row.subtype ?? "") && row.from_subtype === "receivable" ? "back"
      : null;
    if (!key) continue;
    seen.add(row.id);
    const amount = Number(row.amount);
    groups[key].total += amount;
    groups[key].items.push({ id: row.id, date: row.date, description: row.description ?? "", amount, target: friendlyName(row.path, row.institution, row.subtype) });
  }
  return Object.values(groups).filter((g) => g.items.length > 0);
}

const OWN = new Set(["bank", "cash", "wallet"]);
const ACRONYMS: Record<string, string> = { hdfc: "HDFC", sbi: "SBI", icici: "ICICI", idbi: "IDBI", amex: "Amex", rupay: "RuPay", axis: "Axis" };

function friendlyName(path: string, institution: string | null, subtype: string | null): string {
  const leaf = (path.split(":").pop() ?? path)
    .split("_")
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
  if (subtype === "credit_card") return `${leaf} card`;
  return institution || leaf;
}

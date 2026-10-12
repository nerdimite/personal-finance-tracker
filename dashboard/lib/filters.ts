export type TransactionFilters = {
  month: string | null;
  accountId: string | null;
  category: string | null;
  personId: string | null;
  tripId: string | null;
  q: string | null;
  txId: string | null;
};

type RawSearch = Record<string, string | string[] | undefined>;

const EMPTY: TransactionFilters = {
  month: null,
  accountId: null,
  category: null,
  personId: null,
  tripId: null,
  q: null,
  txId: null,
};

export function emptyFilters(): TransactionFilters {
  return { ...EMPTY };
}

export function parseTransactionFilters(raw: RawSearch): TransactionFilters {
  return {
    month: monthOrNull(first(raw, "month")),
    accountId: idOrNull(first(raw, "account")),
    category: cap(first(raw, "category"), 120),
    personId: idOrNull(first(raw, "person")),
    tripId: idOrNull(first(raw, "trip")),
    q: cap(first(raw, "q"), 200),
    txId: idOrNull(first(raw, "tx")),
  };
}

export function parseMessageSeq(raw: RawSearch): string | null {
  return idOrNull(first(raw, "message"));
}

export function parseRunId(value: string): string | null {
  return idOrNull(value);
}

export function transactionHref(
  filters: TransactionFilters,
  patch: Partial<TransactionFilters> = {},
): string {
  const next = { ...filters, ...patch };
  const params = new URLSearchParams();
  if (next.month) params.set("month", next.month);
  if (next.accountId) params.set("account", next.accountId);
  if (next.category) params.set("category", next.category);
  if (next.personId) params.set("person", next.personId);
  if (next.tripId) params.set("trip", next.tripId);
  if (next.q) params.set("q", next.q);
  if (next.txId) params.set("tx", next.txId);
  const query = params.toString();
  return query ? `/transactions?${query}` : "/transactions";
}

export function likeContains(value: string): string {
  return `%${escapeLike(value)}%`;
}

export function transactionWhere(filters: TransactionFilters): { clause: string; params: unknown[] } {
  const where: string[] = [];
  const params: unknown[] = [];
  const add = (sql: string, ...values: unknown[]) => {
    let text = sql;
    for (const value of values) {
      params.push(value);
      text = text.replace("$?", `$${params.length}`);
    }
    where.push(text);
  };

  if (filters.month) {
    add(
      "t.date >= $?::date AND t.date < ($?::date + interval '1 month')::date",
      `${filters.month}-01`,
      `${filters.month}-01`,
    );
  }
  if (filters.accountId) {
    add(
      `EXISTS (
        SELECT 1 FROM postings p
        WHERE p.transaction_id = t.id AND p.account_id = $?::bigint
      )`,
      filters.accountId,
    );
  }
  if (filters.category) {
    add(
      `EXISTS (
        SELECT 1 FROM postings p
        JOIN accounts a ON a.id = p.account_id
        WHERE p.transaction_id = t.id
          AND a.kind = 'expense'
          AND (a.path = $? OR a.path LIKE $? || ':%' ESCAPE '!')
      )`,
      filters.category,
      escapeLike(filters.category),
    );
  }
  if (filters.personId) {
    add(
      `(
        EXISTS (
          SELECT 1 FROM postings p
          WHERE p.transaction_id = t.id AND p.person_id = $?::bigint
        )
        OR EXISTS (
          SELECT 1 FROM postings p
          JOIN accounts a ON a.id = p.account_id
          WHERE p.transaction_id = t.id AND a.person_id = $?::bigint
        )
      )`,
      filters.personId,
      filters.personId,
    );
  }
  if (filters.tripId) add("t.trip_id = $?::bigint", filters.tripId);
  if (filters.q) {
    const pattern = likeContains(filters.q);
    add(
      `(
        COALESCE(t.description, '') ILIKE $? ESCAPE '!'
        OR COALESCE(t.merchant, '') ILIKE $? ESCAPE '!'
        OR COALESCE(t.notes, '') ILIKE $? ESCAPE '!'
        OR COALESCE(t.external_ref, '') ILIKE $? ESCAPE '!'
      )`,
      pattern,
      pattern,
      pattern,
      pattern,
    );
  }

  return {
    clause: where.length ? `WHERE ${where.join("\n  AND ")}` : "",
    params,
  };
}

function escapeLike(value: string): string {
  return value.replace(/[!%_]/g, (char) => `!${char}`);
}

function first(raw: RawSearch, key: string): string | null {
  const value = raw[key];
  const text = Array.isArray(value) ? value[0] : value;
  const trimmed = text?.trim() ?? "";
  return trimmed ? trimmed : null;
}

function cap(value: string | null, max: number): string | null {
  if (!value) return null;
  return value.slice(0, max);
}

function idOrNull(value: string | null): string | null {
  if (!value || !/^[1-9]\d{0,17}$/.test(value)) return null;
  return value;
}

function monthOrNull(value: string | null): string | null {
  if (!value || !/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return null;
  return value;
}

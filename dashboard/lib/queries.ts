import "server-only";
import { query } from "@/lib/db";
import { transactionWhere, type TransactionFilters } from "@/lib/filters";
import { movementOf } from "@/lib/movement";
import { itemLines, parseShares, type QueueFilter } from "@/lib/splitwise";
import type {
  AccountBalance,
  AttachmentRow,
  AuditRow,
  CategoryTotal,
  FilterOptions,
  ImportBatch,
  MerchantTotal,
  MessageRow,
  OverviewTotals,
  PersonBalance,
  RunDetail,
  RunLink,
  RunSummary,
  SplitwiseQueueItem,
  SplitwiseQueueLink,
  SplitwiseSummary,
  ToolCallRow,
  TransactionDetail,
  TransactionRow,
} from "@/lib/types";

const LIST_LIMIT = 200;

const BOUNDS = `
  SELECT
    (date_trunc('month', timezone('Asia/Kolkata', now())))::date AS this_month,
    (date_trunc('month', timezone('Asia/Kolkata', now())) - interval '1 month')::date AS last_month
`;

export async function loadOverview(): Promise<{
  totals: OverviewTotals;
  categories: CategoryTotal[];
  merchants: MerchantTotal[];
  people: PersonBalance[];
  accounts: AccountBalance[];
  imports: ImportBatch[];
  splitwise: SplitwiseSummary;
}> {
  const [totalsRows, categories, merchants, people, accounts, imports, splitwise] = await Promise.all([
    query<{
      this_month: string;
      last_month: string;
      this_total: string;
      last_total: string;
      posting_count: number;
    }>(`
      WITH bounds AS (${BOUNDS})
      SELECT
        b.this_month::text AS this_month,
        b.last_month::text AS last_month,
        COALESCE((SELECT SUM(total) FROM v_monthly_by_category v WHERE v.month = b.this_month), 0)::text AS this_total,
        COALESCE((SELECT SUM(total) FROM v_monthly_by_category v WHERE v.month = b.last_month), 0)::text AS last_total,
        COALESCE((SELECT SUM(posting_count) FROM v_monthly_by_category v WHERE v.month = b.this_month), 0)::int AS posting_count
      FROM bounds b
    `),
    query<{ top_category: string; this_total: string; last_total: string }>(`
      WITH bounds AS (${BOUNDS})
      SELECT
        COALESCE(NULLIF(split_part(v.category_path, ':', 2), ''), v.category_path) AS top_category,
        COALESCE(SUM(v.total) FILTER (WHERE v.month = b.this_month), 0)::text AS this_total,
        COALESCE(SUM(v.total) FILTER (WHERE v.month = b.last_month), 0)::text AS last_total
      FROM v_monthly_by_category v
      CROSS JOIN bounds b
      WHERE v.month IN (b.this_month, b.last_month)
      GROUP BY 1, b.this_month, b.last_month
      HAVING COALESCE(SUM(v.total) FILTER (WHERE v.month = b.this_month), 0) <> 0
          OR COALESCE(SUM(v.total) FILTER (WHERE v.month = b.last_month), 0) <> 0
      ORDER BY COALESCE(SUM(v.total) FILTER (WHERE v.month = b.this_month), 0) DESC, 1
    `),
    query<{ merchant: string; total: string; transactions: number }>(`
      WITH bounds AS (
        SELECT (date_trunc('month', timezone('Asia/Kolkata', now())))::date AS this_month
      )
      SELECT
        btrim(v.merchant) AS merchant,
        SUM(v.amount)::text AS total,
        COUNT(DISTINCT v.transaction_id)::int AS transactions
      FROM v_spend v
      CROSS JOIN bounds b
      WHERE v.date >= b.this_month
        AND v.date < (b.this_month + interval '1 month')::date
        AND v.merchant IS NOT NULL
        AND btrim(v.merchant) <> ''
      GROUP BY 1
      ORDER BY SUM(v.amount) DESC, 1
      LIMIT 8
    `),
    query<{
      person_id: string;
      person_name: string;
      relation: string | null;
      path: string;
      subtype: string | null;
      balance: string;
    }>(`
      SELECT
        person_id::text AS person_id,
        person_name,
        relation,
        path,
        subtype,
        balance::text AS balance
      FROM v_people_balances
      ORDER BY ABS(v_people_balances.balance) DESC, person_name, path
    `),
    query<{
      account_id: string;
      path: string;
      kind: string;
      subtype: string | null;
      institution: string | null;
      balance: string;
    }>(`
      SELECT
        account_id::text AS account_id,
        path,
        kind,
        subtype,
        institution,
        balance::text AS balance
      FROM v_balances
      WHERE kind IN ('asset', 'liability')
      ORDER BY kind, path
    `),
    query<{
      id: string;
      message_seq: string | null;
      file_path: string | null;
      account_path: string | null;
      period_start: string | null;
      period_end: string | null;
      row_count: number | null;
      new_count: number | null;
      duplicate_count: number | null;
      conflict_count: number | null;
      skipped_count: number | null;
    }>(`
      SELECT
        b.id::text AS id,
        b.message_seq::text AS message_seq,
        b.file_path,
        a.path AS account_path,
        b.period_start::text AS period_start,
        b.period_end::text AS period_end,
        b.row_count,
        b.new_count,
        b.duplicate_count,
        b.conflict_count,
        b.skipped_count
      FROM import_batches b
      LEFT JOIN accounts a ON a.id = b.account_id
      WHERE b.status = 'summarized'
      ORDER BY b.id DESC
    `),
    loadSplitwiseSummary(),
  ]);

  const totals = totalsRows[0];
  if (!totals) throw new Error("Overview query returned no row");

  return {
    totals: {
      thisMonth: totals.this_month,
      lastMonth: totals.last_month,
      thisTotal: totals.this_total,
      lastTotal: totals.last_total,
      postingCount: totals.posting_count,
    },
    categories: categories.map((row) => ({
      topCategory: row.top_category,
      thisTotal: row.this_total,
      lastTotal: row.last_total,
    })),
    merchants: merchants.map((row) => ({
      merchant: row.merchant,
      total: row.total,
      transactions: row.transactions,
    })),
    people: people.map((row) => ({
      personId: row.person_id,
      personName: row.person_name,
      relation: row.relation,
      path: row.path,
      subtype: row.subtype,
      balance: row.balance,
    })),
    accounts: accounts.map((row) => ({
      accountId: row.account_id,
      path: row.path,
      kind: row.kind,
      subtype: row.subtype,
      institution: row.institution,
      balance: row.balance,
    })),
    imports: imports.map((row) => ({
      id: row.id,
      messageSeq: row.message_seq,
      filePath: row.file_path,
      accountPath: row.account_path,
      periodStart: row.period_start,
      periodEnd: row.period_end,
      rowCount: row.row_count,
      newCount: row.new_count,
      duplicateCount: row.duplicate_count,
      conflictCount: row.conflict_count,
      skippedCount: row.skipped_count,
    })),
    splitwise,
  };
}

export async function loadSplitwiseSummary(): Promise<SplitwiseSummary> {
  const [counts, people] = await Promise.all([
    query<{ pending_count: number; oldest_created_at: Date | null }>(`
      SELECT
        COUNT(*)::int AS pending_count,
        MIN(created_at) AS oldest_created_at
      FROM splitwise_queue
      WHERE status = 'pending'
    `),
    query<{ person_key: string; name: string; owed: string }>(`
      SELECT
        COALESCE(person_id::text, person_name, '') AS person_key,
        COALESCE(NULLIF(btrim(MAX(person_name)), ''), 'Someone') AS name,
        MAX(total_owed)::text AS owed
      FROM v_splitwise_pending
      WHERE person_amount IS NOT NULL
      GROUP BY COALESCE(person_id::text, person_name, '')
      ORDER BY MAX(total_owed) DESC, 2
    `),
  ]);
  const count = counts[0];
  return {
    pendingCount: count?.pending_count ?? 0,
    oldestCreatedAt: count?.oldest_created_at ?? null,
    people: people.map((row) => ({ key: row.person_key, name: row.name, owed: row.owed })),
  };
}

export async function listSplitwise(status: QueueFilter): Promise<SplitwiseQueueItem[]> {
  const rows = await query<{
    id: string;
    transaction_id: string;
    date: string;
    description: string;
    total_amount: string;
    paid_by: string;
    split_method: string;
    shares: unknown;
    my_share: string;
    others_owe: string;
    items: unknown;
    group_name: string | null;
    status: string;
    created_at: Date;
    added_at: Date | null;
    source_message_seq: string | null;
    notes: string | null;
  }>(
    `
    SELECT
      id::text AS id,
      transaction_id::text AS transaction_id,
      date::text AS date,
      description,
      total_amount::text AS total_amount,
      paid_by,
      split_method,
      shares,
      my_share::text AS my_share,
      others_owe::text AS others_owe,
      items,
      group_name,
      status,
      created_at,
      added_at,
      source_message_seq::text AS source_message_seq,
      notes
    FROM splitwise_queue
    WHERE status = $1
    ORDER BY
      CASE WHEN $1 = 'pending' THEN created_at END ASC,
      CASE WHEN $1 <> 'pending' THEN COALESCE(added_at, created_at) END DESC,
      id ASC
  `,
    [status],
  );
  return rows.map((row) => ({
    id: row.id,
    transactionId: row.transaction_id,
    date: row.date,
    description: row.description,
    totalAmount: row.total_amount,
    paidBy: row.paid_by,
    splitMethod: row.split_method,
    shares: parseShares(row.shares),
    myShare: row.my_share,
    othersOwe: row.others_owe,
    items: itemLines(row.items),
    groupName: row.group_name,
    status: row.status,
    createdAt: row.created_at,
    addedAt: row.added_at,
    messageSeq: row.source_message_seq,
    notes: row.notes,
  }));
}

async function queueForTransaction(id: string): Promise<SplitwiseQueueLink[]> {
  const rows = await query<{
    id: string;
    status: string;
    description: string;
    others_owe: string;
  }>(
    `
    SELECT
      id::text AS id,
      status,
      description,
      others_owe::text AS others_owe
    FROM splitwise_queue
    WHERE transaction_id = $1::bigint
    ORDER BY id
  `,
    [id],
  );
  return rows.map((row) => ({
    id: row.id,
    status: row.status,
    description: row.description,
    othersOwe: row.others_owe,
  }));
}

export async function filterOptions(): Promise<FilterOptions> {
  const [months, accounts, people, trips, categories] = await Promise.all([
    query<{ month: string }>(`
      SELECT to_char(month_start, 'YYYY-MM') AS month
      FROM (
        SELECT date_trunc('month', date)::date AS month_start FROM transactions
        UNION
        SELECT (date_trunc('month', timezone('Asia/Kolkata', now())))::date
      ) months
      ORDER BY month DESC
    `),
    query<{ id: string; path: string }>(`
      SELECT id::text AS id, path
      FROM accounts
      WHERE kind IN ('asset', 'liability')
      ORDER BY path
    `),
    query<{ id: string; name: string }>(`SELECT id::text AS id, name FROM people ORDER BY name`),
    query<{ id: string; name: string }>(`SELECT id::text AS id, name FROM trips ORDER BY name`),
    query<{ path: string }>(`
      SELECT path FROM accounts
      WHERE kind = 'expense' AND is_active
      ORDER BY path
    `),
  ]);
  return {
    months: months.map((row) => row.month),
    accounts,
    people,
    trips,
    categories: categories.map((row) => row.path),
  };
}

export async function listTransactions(
  filters: TransactionFilters,
): Promise<{ rows: TransactionRow[]; total: number }> {
  const { clause, params } = transactionWhere(filters);
  const [rows, counts] = await Promise.all([
    query<{
      id: string;
      date: string;
      description: string | null;
      merchant: string | null;
      status: string;
      payment_method: string | null;
      trip_name: string | null;
      expense_total: string;
      income_total: string;
      abs_total: string;
      categories: string | null;
      people: string | null;
    }>(
      `
      SELECT
        t.id::text AS id,
        t.date::text AS date,
        t.description,
        t.merchant,
        t.status,
        t.payment_method,
        tr.name AS trip_name,
        COALESCE((
          SELECT SUM(p.amount)
          FROM postings p
          JOIN accounts a ON a.id = p.account_id
          WHERE p.transaction_id = t.id AND a.kind = 'expense'
        ), 0)::text AS expense_total,
        COALESCE((
          SELECT SUM(p.amount)
          FROM postings p
          JOIN accounts a ON a.id = p.account_id
          WHERE p.transaction_id = t.id AND a.kind = 'income'
        ), 0)::text AS income_total,
        COALESCE((
          SELECT SUM(ABS(p.amount)) FROM postings p WHERE p.transaction_id = t.id
        ), 0)::text AS abs_total,
        (
          SELECT string_agg(DISTINCT a.path, ', ' ORDER BY a.path)
          FROM postings p
          JOIN accounts a ON a.id = p.account_id
          WHERE p.transaction_id = t.id AND a.kind = 'expense'
        ) AS categories,
        (
          SELECT string_agg(DISTINCT pe.name, ', ' ORDER BY pe.name)
          FROM postings p
          JOIN people pe ON pe.id = p.person_id
          WHERE p.transaction_id = t.id
        ) AS people
      FROM transactions t
      LEFT JOIN trips tr ON tr.id = t.trip_id
      ${clause}
      ORDER BY t.date DESC, t.id DESC
      LIMIT ${LIST_LIMIT}
    `,
      params,
    ),
    query<{ n: number }>(`SELECT COUNT(*)::int AS n FROM transactions t ${clause}`, params),
  ]);

  return {
    total: counts[0]?.n ?? rows.length,
    rows: rows.map((row) => {
      const movement = movementOf(row.expense_total, row.income_total, row.abs_total);
      return {
        id: row.id,
        date: row.date,
        description: row.description,
        merchant: row.merchant,
        status: row.status,
        paymentMethod: row.payment_method,
        tripName: row.trip_name,
        categories: row.categories,
        people: row.people,
        kind: movement.kind,
        amount: movement.amount,
      };
    }),
  };
}

export async function transactionDetail(id: string): Promise<TransactionDetail | null> {
  const rows = await query<{
    id: string;
    date: string;
    description: string | null;
    merchant: string | null;
    external_ref: string | null;
    payment_method: string | null;
    status: string;
    notes: string | null;
    tags: string[] | null;
    source_message_seq: string | null;
    trip_name: string | null;
  }>(
    `
    SELECT
      t.id::text AS id,
      t.date::text AS date,
      t.description,
      t.merchant,
      t.external_ref,
      t.payment_method,
      t.status,
      t.notes,
      t.tags,
      t.source_message_seq::text AS source_message_seq,
      tr.name AS trip_name
    FROM transactions t
    LEFT JOIN trips tr ON tr.id = t.trip_id
    WHERE t.id = $1::bigint
  `,
    [id],
  );
  const row = rows[0];
  if (!row) return null;

  const [postings, attachments, message, runs, queue] = await Promise.all([
    query<{
      id: string;
      amount: string;
      memo: string | null;
      path: string;
      kind: string;
      person_name: string | null;
    }>(
      `
      SELECT
        p.id::text AS id,
        p.amount::text AS amount,
        p.memo,
        a.path,
        a.kind,
        pe.name AS person_name
      FROM postings p
      JOIN accounts a ON a.id = p.account_id
      LEFT JOIN people pe ON pe.id = p.person_id
      WHERE p.transaction_id = $1::bigint
      ORDER BY p.id
    `,
      [id],
    ),
    query<{ id: string; path: string; kind: string | null; transaction_id: string | null }>(
      `
      SELECT id::text AS id, path, kind, transaction_id::text AS transaction_id
      FROM attachments
      WHERE transaction_id = $1::bigint
         OR ($2::bigint IS NOT NULL AND message_seq = $2::bigint)
      ORDER BY id
    `,
      [id, row.source_message_seq],
    ),
    row.source_message_seq ? loadMessage(row.source_message_seq) : Promise.resolve(null),
    row.source_message_seq ? loadRunLinks(row.source_message_seq) : Promise.resolve([]),
    queueForTransaction(id),
  ]);

  return {
    id: row.id,
    date: row.date,
    description: row.description,
    merchant: row.merchant,
    externalRef: row.external_ref,
    paymentMethod: row.payment_method,
    status: row.status,
    notes: row.notes,
    tags: row.tags ?? [],
    sourceMessageSeq: row.source_message_seq,
    tripName: row.trip_name,
    postings: postings.map((posting) => ({
      id: posting.id,
      amount: posting.amount,
      memo: posting.memo,
      path: posting.path,
      kind: posting.kind,
      personName: posting.person_name,
    })),
    attachments: attachments.map(mapAttachment),
    message,
    runs,
    queue,
  };
}

export async function listRuns(messageSeq: string | null): Promise<RunSummary[]> {
  const rows = await query<{
    id: string;
    started_at: Date;
    duration_ms: number | null;
    num_turns: number | null;
    cost_usd: string | null;
    is_error: boolean;
    result_text: string | null;
    log_path: string | null;
    message_seq: string | null;
    message_text: string | null;
  }>(
    `
    SELECT
      r.id::text AS id,
      r.started_at,
      r.duration_ms,
      r.num_turns,
      r.cost_usd::text AS cost_usd,
      r.is_error,
      r.result_text,
      r.log_path,
      r.message_seq::text AS message_seq,
      m.text AS message_text
    FROM agent_runs r
    LEFT JOIN messages m ON m.seq = r.message_seq
    WHERE ($1::bigint IS NULL OR r.message_seq = $1::bigint)
    ORDER BY r.started_at DESC, r.id DESC
    LIMIT ${LIST_LIMIT}
  `,
    [messageSeq],
  );
  return rows.map(mapRun);
}

export async function loadMessage(seq: string): Promise<MessageRow | null> {
  const rows = await query<{
    seq: string;
    text: string | null;
    received_at: Date;
    folder_path: string | null;
  }>(
    `
    SELECT seq::text AS seq, text, received_at, folder_path
    FROM messages
    WHERE seq = $1::bigint
  `,
    [seq],
  );
  const row = rows[0];
  if (!row) return null;
  return {
    seq: row.seq,
    text: row.text,
    receivedAt: row.received_at,
    folderPath: row.folder_path,
  };
}

export async function messageAttachments(seq: string): Promise<AttachmentRow[]> {
  const rows = await query<{ id: string; path: string; kind: string | null; transaction_id: string | null }>(
    `
    SELECT DISTINCT id::text AS id, path, kind, transaction_id::text AS transaction_id
    FROM attachments
    WHERE message_seq = $1::bigint
       OR transaction_id IN (SELECT id FROM transactions WHERE source_message_seq = $1::bigint)
    ORDER BY id
  `,
    [seq],
  );
  return rows.map(mapAttachment);
}

export async function runDetail(id: string): Promise<RunDetail | null> {
  const rows = await query<{
    id: string;
    started_at: Date;
    duration_ms: number | null;
    num_turns: number | null;
    cost_usd: string | null;
    is_error: boolean;
    result_text: string | null;
    log_path: string | null;
    message_seq: string | null;
    message_text: string | null;
    session_id: string | null;
    folder_path: string | null;
    message_received_at: Date | null;
  }>(
    `
    SELECT
      r.id::text AS id,
      r.started_at,
      r.duration_ms,
      r.num_turns,
      r.cost_usd::text AS cost_usd,
      r.is_error,
      r.result_text,
      r.log_path,
      r.message_seq::text AS message_seq,
      m.text AS message_text,
      r.session_id,
      m.folder_path,
      m.received_at AS message_received_at
    FROM agent_runs r
    LEFT JOIN messages m ON m.seq = r.message_seq
    WHERE r.id = $1::bigint
  `,
    [id],
  );
  const row = rows[0];
  if (!row) return null;
  return {
    ...mapRun(row),
    sessionId: row.session_id,
    folderPath: row.folder_path,
    messageReceivedAt: row.message_received_at,
  };
}

export async function runToolCalls(id: string): Promise<ToolCallRow[]> {
  const rows = await query<{
    seq: number;
    tool_name: string;
    input: unknown;
    output: string | null;
    is_error: boolean;
    duration_ms: number | null;
  }>(
    `
    SELECT seq, tool_name, input, output, is_error, duration_ms
    FROM tool_calls
    WHERE run_id = $1::bigint
    ORDER BY seq
  `,
    [id],
  );
  return rows.map((row) => ({
    seq: row.seq,
    toolName: row.tool_name,
    input: row.input,
    output: row.output,
    isError: row.is_error,
    durationMs: row.duration_ms,
  }));
}

export async function runAudit(id: string): Promise<AuditRow[]> {
  const rows = await query<{
    id: string;
    table_name: string;
    row_id: string | null;
    op: string;
    old_row: unknown;
    new_row: unknown;
    at: Date;
  }>(
    `
    SELECT
      id::text AS id,
      "table" AS table_name,
      row_id,
      op,
      "old" AS old_row,
      "new" AS new_row,
      at
    FROM audit_log
    WHERE run_id = $1
    ORDER BY id
  `,
    [id],
  );
  return rows.map((row) => ({
    id: row.id,
    tableName: row.table_name,
    rowId: row.row_id,
    op: row.op,
    oldRow: row.old_row,
    newRow: row.new_row,
    at: row.at,
  }));
}

function mapAttachment(row: {
  id: string;
  path: string;
  kind: string | null;
  transaction_id: string | null;
}): AttachmentRow {
  return {
    id: row.id,
    path: row.path,
    kind: row.kind,
    transactionId: row.transaction_id,
  };
}

function mapRun(row: {
  id: string;
  started_at: Date;
  duration_ms: number | null;
  num_turns: number | null;
  cost_usd: string | null;
  is_error: boolean;
  result_text: string | null;
  log_path: string | null;
  message_seq: string | null;
  message_text: string | null;
}): RunSummary {
  return {
    id: row.id,
    startedAt: row.started_at,
    durationMs: row.duration_ms,
    numTurns: row.num_turns,
    costUsd: row.cost_usd,
    isError: row.is_error,
    resultText: row.result_text,
    logPath: row.log_path,
    messageSeq: row.message_seq,
    messageText: row.message_text,
  };
}

async function loadRunLinks(seq: string): Promise<RunLink[]> {
  const rows = await query<{ id: string; started_at: Date; is_error: boolean }>(
    `
    SELECT id::text AS id, started_at, is_error
    FROM agent_runs
    WHERE message_seq = $1::bigint
    ORDER BY started_at, id
  `,
    [seq],
  );
  return rows.map((row) => ({ id: row.id, startedAt: row.started_at, isError: row.is_error }));
}

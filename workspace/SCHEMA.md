# Database schema

Generated from the live Postgres catalog by `ft-schema-doc`. Do not edit by hand.
The next migration rewrites this file.

Postings are signed double-entry and must sum to 0 on every non-void transaction.
Positive amounts increase assets and expenses. Negative amounts increase liabilities
and income. Category accounts use paths such as `expense:food:ordered_in`.

## Tables

### `accounts`

A place money can sit: bank, card, person, category, equity, or investment. Categories are expense and income accounts.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `path` | `text` | no | Stable path such as asset:bank:hdfc or expense:food:ordered_in. Unique. |
| `kind` | `text` | no | asset, liability, income, expense, or equity. |
| `subtype` | `text` | yes | bank, credit_card, cash, wallet, investment, receivable, payable, or category. |
| `institution` | `text` | yes | Bank, card issuer, or app name. |
| `last4` | `text` | yes | Last four digits of the account or card number. |
| `person_id` | `bigint` | yes | Person this receivable or payable belongs to. |
| `parent_id` | `bigint` | yes | Parent account in a category or account tree. |
| `is_active` | `boolean` | no | False when a category was removed from CATEGORIES.md. Rows are not deleted. |
| `notes` | `text` | yes | Free-form notes. |
| `attributes` | `jsonb` | no | Ad-hoc fields that do not deserve a column yet. Must be a JSON object. |

Triggers:

- `audit_accounts` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `agent_runs`

One claude -p invocation, parsed from the stream-json log.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. The bot sets app.run_id to this id. |
| `message_seq` | `bigint` | yes | Message that started this run. |
| `session_id` | `text` | yes | Claude session id. |
| `started_at` | `timestamp with time zone` | no | When the run started. |
| `duration_ms` | `integer` | yes | Wall time of the run, in milliseconds. |
| `num_turns` | `integer` | yes | Assistant turns reported by Claude. |
| `cost_usd` | `numeric(12,6)` | yes | Estimated cost in USD. |
| `is_error` | `boolean` | no | True when the run ended in an error. |
| `result_text` | `text` | yes | Final assistant text. |
| `log_path` | `text` | yes | Path of the raw stream-json log under logs/runs/. |

Triggers:

- `audit_agent_runs` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `attachments`

A file (screenshot, statement) linked to the message it arrived on and the transactions it produced.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `transaction_id` | `bigint` | yes | Transaction this file supports. |
| `message_seq` | `bigint` | yes | Message folder the file lives in. |
| `path` | `text` | no | Path of the file, relative to the workspace. |
| `sha256` | `text` | yes | Content hash, used to spot the same file twice. |
| `kind` | `text` | yes | photo, pdf, csv, or another short kind label. |

Triggers:

- `audit_attachments` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `audit_log`

Before/after image of every change to ledger data. Filled by trigger. Powers undo.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `table` | `text` | no | Table that changed. |
| `row_id` | `text` | yes | Primary key of the changed row, as text. |
| `op` | `text` | no | INSERT, UPDATE, or DELETE. |
| `old` | `jsonb` | yes | Row before the change. Null on insert. |
| `new` | `jsonb` | yes | Row after the change. Null on delete. |
| `run_id` | `text` | yes | Value of the app.run_id session setting, when the writer set one. Text, not a foreign key, so it can be recorded before the agent_runs row exists. |
| `at` | `timestamp with time zone` | no | When the change was recorded. |

### `balance_checks`

A claimed balance for an account on a date, taken from a statement or entered by hand.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `account_id` | `bigint` | no | Account the figure applies to. |
| `date` | `date` | no | Date the balance was true. |
| `expected_balance` | `numeric(14,2)` | no | Balance the source reported. |
| `source` | `text` | no | Where the figure came from: a statement file name, or manual. |

Triggers:

- `audit_balance_checks` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `import_batches`

A statement or screenshot import, staged before it is committed.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `message_seq` | `bigint` | yes | Message that carried the file. |
| `file_path` | `text` | yes | Path of the imported file. |
| `account_id` | `bigint` | yes | Account the statement belongs to. |
| `period_start` | `date` | yes | First date covered by the file. |
| `period_end` | `date` | yes | Last date covered by the file. |
| `row_count` | `integer` | yes | How many rows the file contained. |
| `new_count` | `integer` | yes | Rows that would be inserted. |
| `duplicate_count` | `integer` | yes | Rows that match an existing transaction. |
| `conflict_count` | `integer` | yes | Rows that disagree with an existing transaction. |
| `skipped_count` | `integer` | yes | Rows the import will ignore. |
| `status` | `text` | no | staging, summarized, committed, or rejected. |

Triggers:

- `audit_import_batches` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `import_rows`

One staged line from an import, before or after it is matched to a transaction.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `batch_id` | `bigint` | no | Batch this row belongs to. |
| `raw` | `jsonb` | yes | Original parsed fields. |
| `parsed_date` | `date` | yes | Date taken from the row. |
| `parsed_amount` | `numeric(14,2)` | yes | Amount taken from the row. |
| `parsed_description` | `text` | yes | Description taken from the row. |
| `parsed_ref` | `text` | yes | Reference taken from the row, compared to transactions.external_ref. |
| `match_transaction_id` | `bigint` | yes | Existing transaction this row duplicates or conflicts with. |
| `status` | `text` | yes | new, duplicate, conflict, or skipped. Null until the row is classified. |

Triggers:

- `audit_import_rows` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `messages`

Index of workspace/messages/. seq is the global message number used in folder names and logs.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `seq` | `bigint` | no | Global message sequence. Also the folder number under workspace/messages/. |
| `telegram_message_id` | `bigint` | yes | Telegram message id. |
| `received_at` | `timestamp with time zone` | no | When the bot stored the message. |
| `text` | `text` | yes | Message text, including captions. |
| `folder_path` | `text` | yes | Path of the message folder, relative to the workspace. |
| `session_id` | `text` | yes | Claude session id that handled this message. |

Triggers:

- `audit_messages` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `people`

Someone money can be owed to, owed by, or spent with.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `name` | `text` | no | Display name. |
| `relation` | `text` | yes | friend, family, flatmate, landlord, partner, or colleague. |
| `upi_id` | `text` | yes | UPI id, when known. |
| `phone` | `text` | yes | Phone number, when known. |
| `notes` | `text` | yes | Free-form notes. |
| `attributes` | `jsonb` | no | Ad-hoc fields that do not deserve a column yet. Must be a JSON object. |

Triggers:

- `audit_people` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `postings`

One leg of a transaction. Amounts are signed: assets and expenses increase when positive; liabilities and income increase when negative. Legs of a non-void transaction sum to 0.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `transaction_id` | `bigint` | no | Transaction this leg belongs to. |
| `account_id` | `bigint` | no | Account this leg posts to. |
| `amount` | `numeric(14,2)` | no | Signed amount. Positive increases assets and expenses. Negative increases liabilities and income. |
| `person_id` | `bigint` | yes | Person this leg was spent on or with, for example a dinner with a partner. Distinct from a receivable account. |
| `memo` | `text` | yes | Short note for this leg only. |

Triggers:

- `audit_postings` — Records the old and new row in audit_log. Copies app.run_id when the session set it.
- `postings_external_ref` — Deferred check: a non-void external_ref cannot be reused on the same account.
- `postings_must_balance` — Deferred check: postings of a non-void transaction must sum to 0.

### `schema_migrations`

Migrations that have been applied. The agent cannot write this table.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `version` | `text` | no | Numeric prefix of the migration file, for example 001. |
| `name` | `text` | no | Name suffix of the migration file. |
| `applied_at` | `timestamp with time zone` | no | When the migration committed. |
| `approved_by` | `text` | yes | Who approved it. Bootstrap migrations record bootstrap. |

### `splitwise_queue`

A shared expense ready to type into Splitwise. The ledger holds only your share; this row is the copy-ready entry.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `transaction_id` | `bigint` | no | Ledger transaction this shared expense belongs to. |
| `date` | `date` | no | Date to enter in Splitwise. |
| `description` | `text` | no | Short Splitwise description. |
| `total_amount` | `numeric(14,2)` | no | Shared part only, not the whole receipt when some items are personal. |
| `paid_by` | `text` | no | Who paid. Defaults to me. |
| `split_method` | `text` | no | equal, exact, percent, or shares. |
| `shares` | `jsonb` | no | JSON array of {person_id, name, amount}, including me. |
| `my_share` | `numeric(14,2)` | no | Amount that stays yours. |
| `others_owe` | `numeric(14,2)` | no | Amount the other people owe. |
| `items` | `jsonb` | yes | Optional item list for the shared part. |
| `group_name` | `text` | yes | Splitwise group name, when the expense belongs to one. |
| `status` | `text` | no | pending, added, or cancelled. |
| `created_at` | `timestamp with time zone` | no | When the row was queued. |
| `added_at` | `timestamp with time zone` | yes | When it was marked added in Splitwise. Null until then. |
| `source_message_seq` | `bigint` | yes | Message that produced this queue row. |
| `notes` | `text` | yes | Free-form notes. |

Triggers:

- `audit_splitwise_queue` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `tool_calls`

One tool call parsed from a run log. This is the data later tool design is based on.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `run_id` | `bigint` | no | Run this call belongs to. |
| `seq` | `integer` | no | Order of the call within the run. |
| `tool_name` | `text` | no | Tool name, for example mcp__finance__sql. |
| `input` | `jsonb` | yes | Tool input payload. |
| `output` | `text` | yes | Tool result text. |
| `is_error` | `boolean` | no | True when the tool returned an error. |
| `duration_ms` | `integer` | yes | How long the call took, in milliseconds. |

Triggers:

- `audit_tool_calls` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

### `transactions`

One real-world money event. The postings under it must sum to 0 unless status is void.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `date` | `date` | no | Date the money moved. |
| `description` | `text` | yes | What happened, in plain language. |
| `merchant` | `text` | yes | Counterparty or merchant name. |
| `external_ref` | `text` | yes | Bank or UPI reference. Unique per account among non-void transactions that post to that account. |
| `attributes` | `jsonb` | no | Ad-hoc fields such as order_id. Must be a JSON object. Prefer this over a migration. |
| `payment_method` | `text` | yes | upi, card, cash, netbanking, or auto_debit. |
| `trip_id` | `bigint` | yes | Trip this event belongs to, when it does. |
| `source_message_seq` | `bigint` | yes | Message that created this transaction. |
| `status` | `text` | no | confirmed, pending, or void. Void transactions are ignored by the balance rule and the views. |
| `tags` | `text[]` | no | Short labels. |
| `notes` | `text` | yes | Free-form notes. |
| `created_at` | `timestamp with time zone` | no | When this row was inserted. |

Triggers:

- `audit_transactions` — Records the old and new row in audit_log. Copies app.run_id when the session set it.
- `transactions_external_ref` — Re-checks external_ref uniqueness per account when the transaction changes.
- `transactions_must_balance` — Re-checks the posting sum when a transaction is inserted, updated, or deleted. Void transactions are exempt.

### `trips`

A trip whose transactions are tagged for a cross-category total.

| Column | Type | Nullable | Comment |
| --- | --- | --- | --- |
| `id` | `bigint` | no | Surrogate primary key. |
| `name` | `text` | no | Trip name, for example Kodai Camping. |
| `start_date` | `date` | yes | First day of the trip. |
| `end_date` | `date` | yes | Last day of the trip. |
| `location` | `text` | yes | Where the trip was. |
| `notes` | `text` | yes | Free-form notes. |

Triggers:

- `audit_trips` — Records the old and new row in audit_log. Copies app.run_id when the session set it.

## Views

### `v_balances`

Signed balance of every account. Void transactions are excluded. Positive increases assets and expenses.

```sql
SELECT a.id AS account_id,
    a.path,
    a.kind,
    a.subtype,
    a.institution,
    a.is_active,
    a.person_id,
    COALESCE(sum(p.amount) FILTER (WHERE t.status IS DISTINCT FROM 'void'::text), 0::numeric)::numeric(14,2) AS balance
   FROM accounts a
     LEFT JOIN postings p ON p.account_id = a.id
     LEFT JOIN transactions t ON t.id = p.transaction_id
  GROUP BY a.id;
```

### `v_monthly_by_category`

Non-void expense totals by calendar month and category path.

```sql
SELECT date_trunc('month'::text, t.date::timestamp with time zone)::date AS month,
    a.path AS category_path,
    sum(p.amount)::numeric(14,2) AS total,
    count(*) AS posting_count
   FROM postings p
     JOIN transactions t ON t.id = p.transaction_id
     JOIN accounts a ON a.id = p.account_id
  WHERE a.kind = 'expense'::text AND t.status <> 'void'::text
  GROUP BY (date_trunc('month'::text, t.date::timestamp with time zone)::date), a.path;
```

### `v_people_balances`

Receivable and payable balances per person. Positive receivable means they owe you. Negative payable means you owe them.

```sql
SELECT pe.id AS person_id,
    pe.name AS person_name,
    pe.relation,
    a.id AS account_id,
    a.path,
    a.kind,
    a.subtype,
    COALESCE(sum(p.amount) FILTER (WHERE t.status IS DISTINCT FROM 'void'::text), 0::numeric)::numeric(14,2) AS balance
   FROM people pe
     JOIN accounts a ON a.person_id = pe.id AND (a.subtype = ANY (ARRAY['receivable'::text, 'payable'::text]))
     LEFT JOIN postings p ON p.account_id = a.id
     LEFT JOIN transactions t ON t.id = p.transaction_id
  GROUP BY pe.id, a.id;
```

### `v_spend`

Expense postings on non-void transactions, with category path, person, and trip.

```sql
SELECT p.id AS posting_id,
    t.id AS transaction_id,
    t.date,
    t.description,
    t.merchant,
    t.payment_method,
    t.status,
    a.path AS category_path,
    p.amount,
    p.person_id,
    pe.name AS person_name,
    t.trip_id,
    tr.name AS trip_name,
    p.memo,
    t.source_message_seq
   FROM postings p
     JOIN transactions t ON t.id = p.transaction_id
     JOIN accounts a ON a.id = p.account_id
     LEFT JOIN people pe ON pe.id = p.person_id
     LEFT JOIN trips tr ON tr.id = t.trip_id
  WHERE a.kind = 'expense'::text AND t.status <> 'void'::text;
```

### `v_splitwise_pending`

Pending Splitwise queue rows, one per other person in shares. total_owed is that person across every pending row. The payer named me is omitted.

```sql
SELECT q.id,
    q.transaction_id,
    q.date,
    q.description,
    q.total_amount,
    q.paid_by,
    q.split_method,
    q.my_share,
    q.others_owe,
    q.items,
    q.group_name,
    q.created_at,
    q.source_message_seq,
    q.notes,
    s.person_id,
    s.name AS person_name,
    s.amount::numeric(14,2) AS person_amount,
    sum(s.amount) OVER (PARTITION BY (COALESCE(s.person_id::text, s.name)))::numeric(14,2) AS total_owed
   FROM splitwise_queue q
     LEFT JOIN LATERAL jsonb_to_recordset(q.shares) s(person_id bigint, name text, amount numeric) ON lower(COALESCE(s.name, ''::text)) <> 'me'::text
  WHERE q.status = 'pending'::text;
```


-- Initial ledger schema, roles, balance rule, audit trigger, and views.
-- The migration runner applies this file in one transaction. Do not add BEGIN/COMMIT.

DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent') THEN
    CREATE ROLE agent LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'dashboard') THEN
    CREATE ROLE dashboard LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'migrator') THEN
    CREATE ROLE migrator LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  ALTER ROLE migrator LOGIN NOSUPERUSER CREATEDB NOCREATEROLE NOINHERIT;
END
$do$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;

DO $do$
BEGIN
  EXECUTE format(
    'GRANT CONNECT ON DATABASE %I TO agent, dashboard, migrator',
    current_database()
  );
  EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
END
$do$;

GRANT USAGE ON SCHEMA public TO agent, dashboard;

-- Objects created later by migrator are usable without a hand-written GRANT.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO dashboard;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO agent;

CREATE TABLE people (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name text NOT NULL,
  relation text,
  upi_id text,
  phone text,
  notes text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT people_relation_known CHECK (
    relation IS NULL OR relation IN (
      'friend', 'family', 'flatmate', 'landlord', 'partner', 'colleague'
    )
  ),
  CONSTRAINT people_attributes_object CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE TABLE accounts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  path text NOT NULL UNIQUE,
  kind text NOT NULL,
  subtype text,
  institution text,
  last4 text,
  person_id bigint REFERENCES people (id),
  parent_id bigint REFERENCES accounts (id),
  is_active boolean NOT NULL DEFAULT true,
  notes text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT accounts_kind_known CHECK (
    kind IN ('asset', 'liability', 'income', 'expense', 'equity')
  ),
  CONSTRAINT accounts_subtype_known CHECK (
    subtype IS NULL OR subtype IN (
      'bank', 'credit_card', 'cash', 'wallet', 'investment',
      'receivable', 'payable', 'category'
    )
  ),
  CONSTRAINT accounts_last4_digits CHECK (last4 IS NULL OR last4 ~ '^[0-9]{4}$'),
  CONSTRAINT accounts_attributes_object CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE INDEX accounts_person_id_idx ON accounts (person_id);
CREATE INDEX accounts_parent_id_idx ON accounts (parent_id);

CREATE TABLE trips (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name text NOT NULL,
  start_date date,
  end_date date,
  location text,
  notes text,
  CONSTRAINT trips_dates CHECK (
    end_date IS NULL OR start_date IS NULL OR end_date >= start_date
  )
);

CREATE TABLE messages (
  seq bigint PRIMARY KEY,
  telegram_message_id bigint,
  received_at timestamptz NOT NULL DEFAULT now(),
  "text" text,
  folder_path text,
  session_id text,
  CONSTRAINT messages_seq_positive CHECK (seq > 0)
);

CREATE INDEX messages_telegram_message_id_idx ON messages (telegram_message_id);

CREATE TABLE transactions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  date date NOT NULL,
  description text,
  merchant text,
  external_ref text,
  attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
  payment_method text,
  trip_id bigint REFERENCES trips (id),
  source_message_seq bigint REFERENCES messages (seq),
  status text NOT NULL DEFAULT 'confirmed',
  tags text[] NOT NULL DEFAULT '{}'::text[],
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT transactions_status_known CHECK (status IN ('confirmed', 'pending', 'void')),
  CONSTRAINT transactions_payment_method_known CHECK (
    payment_method IS NULL OR payment_method IN (
      'upi', 'card', 'cash', 'netbanking', 'auto_debit'
    )
  ),
  CONSTRAINT transactions_attributes_object CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE INDEX transactions_date_idx ON transactions (date);
CREATE INDEX transactions_trip_id_idx ON transactions (trip_id);
CREATE INDEX transactions_source_message_seq_idx ON transactions (source_message_seq);
CREATE INDEX transactions_external_ref_idx ON transactions (external_ref)
  WHERE external_ref IS NOT NULL;

CREATE TABLE postings (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  transaction_id bigint NOT NULL REFERENCES transactions (id),
  account_id bigint NOT NULL REFERENCES accounts (id),
  amount numeric(14, 2) NOT NULL,
  person_id bigint REFERENCES people (id),
  memo text
);

CREATE INDEX postings_transaction_id_idx ON postings (transaction_id);
CREATE INDEX postings_account_id_idx ON postings (account_id);
CREATE INDEX postings_person_id_idx ON postings (person_id);

CREATE TABLE attachments (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  transaction_id bigint REFERENCES transactions (id),
  message_seq bigint REFERENCES messages (seq),
  path text NOT NULL,
  sha256 text,
  kind text,
  CONSTRAINT attachments_linked CHECK (
    transaction_id IS NOT NULL OR message_seq IS NOT NULL
  )
);

CREATE INDEX attachments_transaction_id_idx ON attachments (transaction_id);
CREATE INDEX attachments_message_seq_idx ON attachments (message_seq);

CREATE TABLE agent_runs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_seq bigint REFERENCES messages (seq),
  session_id text,
  started_at timestamptz NOT NULL DEFAULT now(),
  duration_ms integer,
  num_turns integer,
  cost_usd numeric(12, 6),
  is_error boolean NOT NULL DEFAULT false,
  result_text text,
  log_path text
);

CREATE INDEX agent_runs_message_seq_idx ON agent_runs (message_seq);

CREATE TABLE tool_calls (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id bigint NOT NULL REFERENCES agent_runs (id),
  seq integer NOT NULL,
  tool_name text NOT NULL,
  input jsonb,
  output text,
  is_error boolean NOT NULL DEFAULT false,
  duration_ms integer,
  UNIQUE (run_id, seq)
);

CREATE TABLE import_batches (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_seq bigint REFERENCES messages (seq),
  file_path text,
  account_id bigint REFERENCES accounts (id),
  period_start date,
  period_end date,
  row_count integer,
  new_count integer,
  duplicate_count integer,
  conflict_count integer,
  skipped_count integer,
  status text NOT NULL DEFAULT 'staging',
  CONSTRAINT import_batches_status_known CHECK (
    status IN ('staging', 'summarized', 'committed', 'rejected')
  ),
  CONSTRAINT import_batches_period CHECK (
    period_end IS NULL OR period_start IS NULL OR period_end >= period_start
  )
);

CREATE INDEX import_batches_message_seq_idx ON import_batches (message_seq);
CREATE INDEX import_batches_account_id_idx ON import_batches (account_id);

CREATE TABLE import_rows (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  batch_id bigint NOT NULL REFERENCES import_batches (id),
  raw jsonb,
  parsed_date date,
  parsed_amount numeric(14, 2),
  parsed_description text,
  parsed_ref text,
  match_transaction_id bigint REFERENCES transactions (id),
  status text,
  CONSTRAINT import_rows_status_known CHECK (
    status IS NULL OR status IN ('new', 'duplicate', 'conflict', 'skipped')
  )
);

CREATE INDEX import_rows_batch_id_idx ON import_rows (batch_id);
CREATE INDEX import_rows_match_transaction_id_idx ON import_rows (match_transaction_id);

CREATE TABLE balance_checks (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  account_id bigint NOT NULL REFERENCES accounts (id),
  date date NOT NULL,
  expected_balance numeric(14, 2) NOT NULL,
  source text NOT NULL DEFAULT 'manual',
  UNIQUE (account_id, date, source)
);

CREATE TABLE splitwise_queue (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  transaction_id bigint NOT NULL REFERENCES transactions (id),
  date date NOT NULL,
  description text NOT NULL,
  total_amount numeric(14, 2) NOT NULL,
  paid_by text NOT NULL DEFAULT 'me',
  split_method text NOT NULL,
  shares jsonb NOT NULL,
  my_share numeric(14, 2) NOT NULL,
  others_owe numeric(14, 2) NOT NULL,
  items jsonb,
  group_name text,
  status text NOT NULL DEFAULT 'pending',
  created_at timestamptz NOT NULL DEFAULT now(),
  added_at timestamptz,
  source_message_seq bigint REFERENCES messages (seq),
  notes text,
  CONSTRAINT splitwise_queue_status_known CHECK (
    status IN ('pending', 'added', 'cancelled')
  ),
  CONSTRAINT splitwise_queue_split_method_known CHECK (
    split_method IN ('equal', 'exact', 'percent', 'shares')
  )
);

CREATE INDEX splitwise_queue_transaction_id_idx ON splitwise_queue (transaction_id);
CREATE INDEX splitwise_queue_status_created_idx ON splitwise_queue (status, created_at);

CREATE TABLE schema_migrations (
  version text PRIMARY KEY,
  name text NOT NULL,
  applied_at timestamptz NOT NULL DEFAULT now(),
  approved_by text
);

CREATE TABLE audit_log (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  "table" text NOT NULL,
  row_id text,
  op text NOT NULL,
  "old" jsonb,
  "new" jsonb,
  run_id text,
  at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT audit_log_op_known CHECK (op IN ('INSERT', 'UPDATE', 'DELETE'))
);

CREATE INDEX audit_log_run_id_idx ON audit_log (run_id);
CREATE INDEX audit_log_table_row_idx ON audit_log ("table", row_id);

-- Balance: a non-void transaction needs at least two postings, and they must sum to 0.
-- Deferred so the transaction row and its postings can be written in any order.

CREATE FUNCTION ft_assert_balanced(tid bigint)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
  txn_status text;
  total numeric;
  n bigint;
BEGIN
  IF tid IS NULL THEN
    RETURN;
  END IF;
  SELECT status INTO txn_status FROM public.transactions WHERE id = tid;
  IF txn_status IS NULL OR txn_status = 'void' THEN
    RETURN;
  END IF;
  SELECT COALESCE(SUM(amount), 0), count(*) INTO total, n
  FROM public.postings
  WHERE transaction_id = tid;
  IF n < 2 THEN
    RAISE EXCEPTION 'transaction % needs at least 2 postings, found %', tid, n
      USING ERRCODE = '23514';
  END IF;
  IF total <> 0 THEN
    RAISE EXCEPTION 'transaction % postings sum to %, expected 0', tid, total
      USING ERRCODE = '23514';
  END IF;
END;
$fn$;

CREATE FUNCTION ft_postings_balance()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $fn$
BEGIN
  IF TG_TABLE_NAME = 'postings' THEN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
      PERFORM public.ft_assert_balanced(OLD.transaction_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
      PERFORM public.ft_assert_balanced(NEW.transaction_id);
    END IF;
  ELSE
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
      PERFORM public.ft_assert_balanced(OLD.id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
      PERFORM public.ft_assert_balanced(NEW.id);
    END IF;
  END IF;
  RETURN NULL;
END;
$fn$;

CREATE CONSTRAINT TRIGGER postings_must_balance
AFTER INSERT OR UPDATE OR DELETE ON postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION ft_postings_balance();

CREATE CONSTRAINT TRIGGER transactions_must_balance
AFTER INSERT OR UPDATE OR DELETE ON transactions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION ft_postings_balance();

-- external_ref is unique per account among non-void transactions.
-- The ref lives on transactions and the account lives on postings, so a
-- trigger checks the pair. Same ref on two different accounts is allowed.

CREATE FUNCTION ft_assert_external_ref(tid bigint)
RETURNS void
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
  ref text;
  txn_status text;
  clash bigint;
BEGIN
  SELECT external_ref, status INTO ref, txn_status
  FROM public.transactions
  WHERE id = tid;
  IF ref IS NULL OR txn_status IS NULL OR txn_status = 'void' THEN
    RETURN;
  END IF;
  SELECT t.id INTO clash
  FROM public.transactions t
  JOIN public.postings p ON p.transaction_id = t.id
  WHERE t.id <> tid
    AND t.external_ref = ref
    AND t.status <> 'void'
    AND p.account_id IN (
      SELECT account_id FROM public.postings WHERE transaction_id = tid
    )
  LIMIT 1;
  IF clash IS NOT NULL THEN
    RAISE EXCEPTION
      'external_ref % already used on the same account by transaction %', ref, clash
      USING ERRCODE = '23505';
  END IF;
END;
$fn$;

CREATE FUNCTION ft_external_ref_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $fn$
BEGIN
  IF TG_TABLE_NAME = 'postings' THEN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
      PERFORM public.ft_assert_external_ref(OLD.transaction_id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
      PERFORM public.ft_assert_external_ref(NEW.transaction_id);
    END IF;
  ELSE
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
      PERFORM public.ft_assert_external_ref(OLD.id);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
      PERFORM public.ft_assert_external_ref(NEW.id);
    END IF;
  END IF;
  RETURN NULL;
END;
$fn$;

CREATE CONSTRAINT TRIGGER postings_external_ref
AFTER INSERT OR UPDATE OR DELETE ON postings
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION ft_external_ref_guard();

CREATE CONSTRAINT TRIGGER transactions_external_ref
AFTER INSERT OR UPDATE OR DELETE ON transactions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION ft_external_ref_guard();

-- Audit. SECURITY DEFINER so the agent can change data without having INSERT
-- on audit_log (it cannot forge a history row). app.run_id is optional.

CREATE FUNCTION ft_audit()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
  rid text;
  row_image jsonb;
  row_key text;
BEGIN
  rid := nullif(current_setting('app.run_id', true), '');
  IF TG_OP = 'DELETE' THEN
    row_image := to_jsonb(OLD);
  ELSE
    row_image := to_jsonb(NEW);
  END IF;
  row_key := coalesce(row_image ->> 'id', row_image ->> 'seq', row_image ->> 'version');
  INSERT INTO public.audit_log ("table", row_id, op, "old", "new", run_id)
  VALUES (
    TG_TABLE_NAME,
    row_key,
    TG_OP,
    CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END,
    rid
  );
  RETURN NULL;
END;
$fn$;

REVOKE ALL ON FUNCTION ft_audit() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION ft_audit() TO agent;
GRANT EXECUTE ON FUNCTION ft_postings_balance() TO agent;
GRANT EXECUTE ON FUNCTION ft_external_ref_guard() TO agent;
GRANT EXECUTE ON FUNCTION ft_assert_balanced(bigint) TO agent;
GRANT EXECUTE ON FUNCTION ft_assert_external_ref(bigint) TO agent;

DO $do$
DECLARE
  audited text;
BEGIN
  FOREACH audited IN ARRAY ARRAY[
    'accounts', 'people', 'transactions', 'postings', 'trips', 'attachments',
    'messages', 'agent_runs', 'tool_calls', 'import_batches', 'import_rows',
    'balance_checks', 'splitwise_queue'
  ]
  LOOP
    EXECUTE format(
      'CREATE TRIGGER %I AFTER INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION ft_audit()',
      'audit_' || audited,
      audited
    );
    EXECUTE format(
      'COMMENT ON TRIGGER %I ON %I IS %L',
      'audit_' || audited,
      audited,
      'Records the old and new row in audit_log. Copies app.run_id when the session set it.'
    );
  END LOOP;
END
$do$;

COMMENT ON TRIGGER postings_must_balance ON postings IS
  'Deferred check: postings of a non-void transaction must sum to 0.';
COMMENT ON TRIGGER transactions_must_balance ON transactions IS
  'Re-checks the posting sum when a transaction is inserted, updated, or deleted. Void transactions are exempt.';
COMMENT ON TRIGGER postings_external_ref ON postings IS
  'Deferred check: a non-void external_ref cannot be reused on the same account.';
COMMENT ON TRIGGER transactions_external_ref ON transactions IS
  'Re-checks external_ref uniqueness per account when the transaction changes.';

CREATE VIEW v_spend
WITH (security_invoker = true) AS
SELECT
  p.id AS posting_id,
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
WHERE a.kind = 'expense'
  AND t.status <> 'void';

CREATE VIEW v_balances
WITH (security_invoker = true) AS
SELECT
  a.id AS account_id,
  a.path,
  a.kind,
  a.subtype,
  a.institution,
  a.is_active,
  a.person_id,
  COALESCE(
    SUM(p.amount) FILTER (WHERE t.status IS DISTINCT FROM 'void'),
    0
  )::numeric(14, 2) AS balance
FROM accounts a
LEFT JOIN postings p ON p.account_id = a.id
LEFT JOIN transactions t ON t.id = p.transaction_id
GROUP BY a.id;

CREATE VIEW v_people_balances
WITH (security_invoker = true) AS
SELECT
  pe.id AS person_id,
  pe.name AS person_name,
  pe.relation,
  a.id AS account_id,
  a.path,
  a.kind,
  a.subtype,
  COALESCE(
    SUM(p.amount) FILTER (WHERE t.status IS DISTINCT FROM 'void'),
    0
  )::numeric(14, 2) AS balance
FROM people pe
JOIN accounts a
  ON a.person_id = pe.id
 AND a.subtype IN ('receivable', 'payable')
LEFT JOIN postings p ON p.account_id = a.id
LEFT JOIN transactions t ON t.id = p.transaction_id
GROUP BY pe.id, a.id;

CREATE VIEW v_monthly_by_category
WITH (security_invoker = true) AS
SELECT
  date_trunc('month', t.date)::date AS month,
  a.path AS category_path,
  SUM(p.amount)::numeric(14, 2) AS total,
  count(*) AS posting_count
FROM postings p
JOIN transactions t ON t.id = p.transaction_id
JOIN accounts a ON a.id = p.account_id
WHERE a.kind = 'expense'
  AND t.status <> 'void'
GROUP BY 1, 2;

COMMENT ON VIEW v_spend IS
  'Expense postings on non-void transactions, with category path, person, and trip.';
COMMENT ON VIEW v_balances IS
  'Signed balance of every account. Void transactions are excluded. Positive increases assets and expenses.';
COMMENT ON VIEW v_people_balances IS
  'Receivable and payable balances per person. Positive receivable means they owe you. Negative payable means you owe them.';
COMMENT ON VIEW v_monthly_by_category IS
  'Non-void expense totals by calendar month and category path.';

CREATE VIEW v_splitwise_pending
WITH (security_invoker = true) AS
SELECT
  q.id,
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
  s.amount::numeric(14, 2) AS person_amount,
  SUM(s.amount) OVER (
    PARTITION BY COALESCE(s.person_id::text, s.name)
  )::numeric(14, 2) AS total_owed
FROM splitwise_queue q
LEFT JOIN LATERAL jsonb_to_recordset(q.shares)
  AS s(person_id bigint, name text, amount numeric)
  ON lower(coalesce(s.name, '')) <> 'me'
WHERE q.status = 'pending';

COMMENT ON VIEW v_splitwise_pending IS
  'Pending Splitwise queue rows, one per other person in shares. total_owed is that person across every pending row. The payer named me is omitted.';

GRANT SELECT ON ALL TABLES IN SCHEMA public TO agent, dashboard;
GRANT INSERT, UPDATE, DELETE ON
  accounts, people, transactions, postings, trips, attachments,
  import_batches, import_rows,
  balance_checks
TO agent;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent;

REVOKE INSERT, UPDATE, DELETE ON
  messages, agent_runs, tool_calls, audit_log, schema_migrations
FROM agent;
GRANT INSERT, UPDATE ON splitwise_queue TO agent;
REVOKE DELETE ON splitwise_queue FROM agent;
GRANT UPDATE (status, added_at) ON splitwise_queue TO dashboard;
REVOKE INSERT, UPDATE, DELETE ON
  v_spend, v_balances, v_people_balances, v_monthly_by_category, v_splitwise_pending
FROM agent;

COMMENT ON ROLE agent IS
  'DML on ledger data only. No DDL, no writes to messages, agent_runs, tool_calls, audit_log, or schema_migrations.';
COMMENT ON ROLE dashboard IS
  'Read-only access for the local Next.js dashboard.';
COMMENT ON ROLE migrator IS
  'Owns the app schema. Applies approved migrations. Not a superuser.';

COMMENT ON TABLE people IS
  'Someone money can be owed to, owed by, or spent with.';
COMMENT ON COLUMN people.id IS 'Surrogate primary key.';
COMMENT ON COLUMN people.name IS 'Display name.';
COMMENT ON COLUMN people.relation IS
  'friend, family, flatmate, landlord, partner, or colleague.';
COMMENT ON COLUMN people.upi_id IS 'UPI id, when known.';
COMMENT ON COLUMN people.phone IS 'Phone number, when known.';
COMMENT ON COLUMN people.notes IS 'Free-form notes.';
COMMENT ON COLUMN people.attributes IS
  'Ad-hoc fields that do not deserve a column yet. Must be a JSON object.';

COMMENT ON TABLE accounts IS
  'A place money can sit: bank, card, person, category, equity, or investment. Categories are expense and income accounts.';
COMMENT ON COLUMN accounts.id IS 'Surrogate primary key.';
COMMENT ON COLUMN accounts.path IS
  'Stable path such as asset:bank:hdfc or expense:food:ordered_in. Unique.';
COMMENT ON COLUMN accounts.kind IS
  'asset, liability, income, expense, or equity.';
COMMENT ON COLUMN accounts.subtype IS
  'bank, credit_card, cash, wallet, investment, receivable, payable, or category.';
COMMENT ON COLUMN accounts.institution IS 'Bank, card issuer, or app name.';
COMMENT ON COLUMN accounts.last4 IS 'Last four digits of the account or card number.';
COMMENT ON COLUMN accounts.person_id IS
  'Person this receivable or payable belongs to.';
COMMENT ON COLUMN accounts.parent_id IS 'Parent account in a category or account tree.';
COMMENT ON COLUMN accounts.is_active IS
  'False when a category was removed from CATEGORIES.md. Rows are not deleted.';
COMMENT ON COLUMN accounts.notes IS 'Free-form notes.';
COMMENT ON COLUMN accounts.attributes IS
  'Ad-hoc fields that do not deserve a column yet. Must be a JSON object.';

COMMENT ON TABLE trips IS 'A trip whose transactions are tagged for a cross-category total.';
COMMENT ON COLUMN trips.id IS 'Surrogate primary key.';
COMMENT ON COLUMN trips.name IS 'Trip name, for example Kodai Camping.';
COMMENT ON COLUMN trips.start_date IS 'First day of the trip.';
COMMENT ON COLUMN trips.end_date IS 'Last day of the trip.';
COMMENT ON COLUMN trips.location IS 'Where the trip was.';
COMMENT ON COLUMN trips.notes IS 'Free-form notes.';

COMMENT ON TABLE messages IS
  'Index of workspace/messages/. seq is the global message number used in folder names and logs.';
COMMENT ON COLUMN messages.seq IS 'Global message sequence. Also the folder number under workspace/messages/.';
COMMENT ON COLUMN messages.telegram_message_id IS 'Telegram message id.';
COMMENT ON COLUMN messages.received_at IS 'When the bot stored the message.';
COMMENT ON COLUMN messages."text" IS 'Message text, including captions.';
COMMENT ON COLUMN messages.folder_path IS 'Path of the message folder, relative to the workspace.';
COMMENT ON COLUMN messages.session_id IS 'Claude session id that handled this message.';

COMMENT ON TABLE transactions IS
  'One real-world money event. The postings under it must sum to 0 unless status is void.';
COMMENT ON COLUMN transactions.id IS 'Surrogate primary key.';
COMMENT ON COLUMN transactions.date IS 'Date the money moved.';
COMMENT ON COLUMN transactions.description IS 'What happened, in plain language.';
COMMENT ON COLUMN transactions.merchant IS 'Counterparty or merchant name.';
COMMENT ON COLUMN transactions.external_ref IS
  'Bank or UPI reference. Unique per account among non-void transactions that post to that account.';
COMMENT ON COLUMN transactions.attributes IS
  'Ad-hoc fields such as order_id. Must be a JSON object. Prefer this over a migration.';
COMMENT ON COLUMN transactions.payment_method IS
  'upi, card, cash, netbanking, or auto_debit.';
COMMENT ON COLUMN transactions.trip_id IS 'Trip this event belongs to, when it does.';
COMMENT ON COLUMN transactions.source_message_seq IS
  'Message that created this transaction.';
COMMENT ON COLUMN transactions.status IS
  'confirmed, pending, or void. Void transactions are ignored by the balance rule and the views.';
COMMENT ON COLUMN transactions.tags IS 'Short labels.';
COMMENT ON COLUMN transactions.notes IS 'Free-form notes.';
COMMENT ON COLUMN transactions.created_at IS 'When this row was inserted.';

COMMENT ON TABLE postings IS
  'One leg of a transaction. Amounts are signed: assets and expenses increase when positive; liabilities and income increase when negative. Legs of a non-void transaction sum to 0.';
COMMENT ON COLUMN postings.id IS 'Surrogate primary key.';
COMMENT ON COLUMN postings.transaction_id IS 'Transaction this leg belongs to.';
COMMENT ON COLUMN postings.account_id IS 'Account this leg posts to.';
COMMENT ON COLUMN postings.amount IS
  'Signed amount. Positive increases assets and expenses. Negative increases liabilities and income.';
COMMENT ON COLUMN postings.person_id IS
  'Person this leg was spent on or with, for example a dinner with a partner. Distinct from a receivable account.';
COMMENT ON COLUMN postings.memo IS 'Short note for this leg only.';

COMMENT ON TABLE attachments IS
  'A file (screenshot, statement) linked to the message it arrived on and the transactions it produced.';
COMMENT ON COLUMN attachments.id IS 'Surrogate primary key.';
COMMENT ON COLUMN attachments.transaction_id IS 'Transaction this file supports.';
COMMENT ON COLUMN attachments.message_seq IS 'Message folder the file lives in.';
COMMENT ON COLUMN attachments.path IS 'Path of the file, relative to the workspace.';
COMMENT ON COLUMN attachments.sha256 IS 'Content hash, used to spot the same file twice.';
COMMENT ON COLUMN attachments.kind IS 'photo, pdf, csv, or another short kind label.';

COMMENT ON TABLE agent_runs IS
  'One claude -p invocation, parsed from the stream-json log.';
COMMENT ON COLUMN agent_runs.id IS 'Surrogate primary key. The bot sets app.run_id to this id.';
COMMENT ON COLUMN agent_runs.message_seq IS 'Message that started this run.';
COMMENT ON COLUMN agent_runs.session_id IS 'Claude session id.';
COMMENT ON COLUMN agent_runs.started_at IS 'When the run started.';
COMMENT ON COLUMN agent_runs.duration_ms IS 'Wall time of the run, in milliseconds.';
COMMENT ON COLUMN agent_runs.num_turns IS 'Assistant turns reported by Claude.';
COMMENT ON COLUMN agent_runs.cost_usd IS 'Estimated cost in USD.';
COMMENT ON COLUMN agent_runs.is_error IS 'True when the run ended in an error.';
COMMENT ON COLUMN agent_runs.result_text IS 'Final assistant text.';
COMMENT ON COLUMN agent_runs.log_path IS 'Path of the raw stream-json log under logs/runs/.';

COMMENT ON TABLE tool_calls IS
  'One tool call parsed from a run log. This is the data later tool design is based on.';
COMMENT ON COLUMN tool_calls.id IS 'Surrogate primary key.';
COMMENT ON COLUMN tool_calls.run_id IS 'Run this call belongs to.';
COMMENT ON COLUMN tool_calls.seq IS 'Order of the call within the run.';
COMMENT ON COLUMN tool_calls.tool_name IS 'Tool name, for example mcp__finance__sql.';
COMMENT ON COLUMN tool_calls.input IS 'Tool input payload.';
COMMENT ON COLUMN tool_calls.output IS 'Tool result text.';
COMMENT ON COLUMN tool_calls.is_error IS 'True when the tool returned an error.';
COMMENT ON COLUMN tool_calls.duration_ms IS 'How long the call took, in milliseconds.';

COMMENT ON TABLE import_batches IS
  'A statement or screenshot import, staged before it is committed.';
COMMENT ON COLUMN import_batches.id IS 'Surrogate primary key.';
COMMENT ON COLUMN import_batches.message_seq IS 'Message that carried the file.';
COMMENT ON COLUMN import_batches.file_path IS 'Path of the imported file.';
COMMENT ON COLUMN import_batches.account_id IS 'Account the statement belongs to.';
COMMENT ON COLUMN import_batches.period_start IS 'First date covered by the file.';
COMMENT ON COLUMN import_batches.period_end IS 'Last date covered by the file.';
COMMENT ON COLUMN import_batches.row_count IS 'How many rows the file contained.';
COMMENT ON COLUMN import_batches.new_count IS 'Rows that would be inserted.';
COMMENT ON COLUMN import_batches.duplicate_count IS 'Rows that match an existing transaction.';
COMMENT ON COLUMN import_batches.conflict_count IS 'Rows that disagree with an existing transaction.';
COMMENT ON COLUMN import_batches.skipped_count IS 'Rows the import will ignore.';
COMMENT ON COLUMN import_batches.status IS
  'staging, summarized, committed, or rejected.';

COMMENT ON TABLE import_rows IS
  'One staged line from an import, before or after it is matched to a transaction.';
COMMENT ON COLUMN import_rows.id IS 'Surrogate primary key.';
COMMENT ON COLUMN import_rows.batch_id IS 'Batch this row belongs to.';
COMMENT ON COLUMN import_rows.raw IS 'Original parsed fields.';
COMMENT ON COLUMN import_rows.parsed_date IS 'Date taken from the row.';
COMMENT ON COLUMN import_rows.parsed_amount IS 'Amount taken from the row.';
COMMENT ON COLUMN import_rows.parsed_description IS 'Description taken from the row.';
COMMENT ON COLUMN import_rows.parsed_ref IS 'Reference taken from the row, compared to transactions.external_ref.';
COMMENT ON COLUMN import_rows.match_transaction_id IS
  'Existing transaction this row duplicates or conflicts with.';
COMMENT ON COLUMN import_rows.status IS
  'new, duplicate, conflict, or skipped. Null until the row is classified.';

COMMENT ON TABLE balance_checks IS
  'A claimed balance for an account on a date, taken from a statement or entered by hand.';
COMMENT ON COLUMN balance_checks.id IS 'Surrogate primary key.';
COMMENT ON COLUMN balance_checks.account_id IS 'Account the figure applies to.';
COMMENT ON COLUMN balance_checks.date IS 'Date the balance was true.';
COMMENT ON COLUMN balance_checks.expected_balance IS 'Balance the source reported.';
COMMENT ON COLUMN balance_checks.source IS
  'Where the figure came from: a statement file name, or manual.';

COMMENT ON TABLE splitwise_queue IS
  'A shared expense ready to type into Splitwise. The ledger holds only your share; this row is the copy-ready entry.';
COMMENT ON COLUMN splitwise_queue.id IS 'Surrogate primary key.';
COMMENT ON COLUMN splitwise_queue.transaction_id IS 'Ledger transaction this shared expense belongs to.';
COMMENT ON COLUMN splitwise_queue.date IS 'Date to enter in Splitwise.';
COMMENT ON COLUMN splitwise_queue.description IS 'Short Splitwise description.';
COMMENT ON COLUMN splitwise_queue.total_amount IS 'Shared part only, not the whole receipt when some items are personal.';
COMMENT ON COLUMN splitwise_queue.paid_by IS 'Who paid. Defaults to me.';
COMMENT ON COLUMN splitwise_queue.split_method IS 'equal, exact, percent, or shares.';
COMMENT ON COLUMN splitwise_queue.shares IS
  'JSON array of {person_id, name, amount}, including me.';
COMMENT ON COLUMN splitwise_queue.my_share IS 'Amount that stays yours.';
COMMENT ON COLUMN splitwise_queue.others_owe IS 'Amount the other people owe.';
COMMENT ON COLUMN splitwise_queue.items IS 'Optional item list for the shared part.';
COMMENT ON COLUMN splitwise_queue.group_name IS 'Splitwise group name, when the expense belongs to one.';
COMMENT ON COLUMN splitwise_queue.status IS 'pending, added, or cancelled.';
COMMENT ON COLUMN splitwise_queue.created_at IS 'When the row was queued.';
COMMENT ON COLUMN splitwise_queue.added_at IS 'When it was marked added in Splitwise. Null until then.';
COMMENT ON COLUMN splitwise_queue.source_message_seq IS 'Message that produced this queue row.';
COMMENT ON COLUMN splitwise_queue.notes IS 'Free-form notes.';

COMMENT ON TABLE schema_migrations IS
  'Migrations that have been applied. The agent cannot write this table.';
COMMENT ON COLUMN schema_migrations.version IS 'Numeric prefix of the migration file, for example 001.';
COMMENT ON COLUMN schema_migrations.name IS 'Name suffix of the migration file.';
COMMENT ON COLUMN schema_migrations.applied_at IS 'When the migration committed.';
COMMENT ON COLUMN schema_migrations.approved_by IS
  'Who approved it. Bootstrap migrations record bootstrap.';

COMMENT ON TABLE audit_log IS
  'Before/after image of every change to ledger data. Filled by trigger. Powers undo.';
COMMENT ON COLUMN audit_log.id IS 'Surrogate primary key.';
COMMENT ON COLUMN audit_log."table" IS 'Table that changed.';
COMMENT ON COLUMN audit_log.row_id IS 'Primary key of the changed row, as text.';
COMMENT ON COLUMN audit_log.op IS 'INSERT, UPDATE, or DELETE.';
COMMENT ON COLUMN audit_log."old" IS 'Row before the change. Null on insert.';
COMMENT ON COLUMN audit_log."new" IS 'Row after the change. Null on delete.';
COMMENT ON COLUMN audit_log.run_id IS
  'Value of the app.run_id session setting, when the writer set one. Text, not a foreign key, so it can be recorded before the agent_runs row exists.';
COMMENT ON COLUMN audit_log.at IS 'When the change was recorded.';

-- migrator owns the app schema. The database owner stays the bootstrap superuser.
ALTER SCHEMA public OWNER TO migrator;

DO $own$
DECLARE
  rel record;
  proc record;
BEGIN
  FOR rel IN
    SELECT c.oid::regclass AS name, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND NOT EXISTS (
        SELECT 1
        FROM pg_depend d
        WHERE d.classid = 'pg_class'::regclass
          AND d.objid = c.oid
          AND d.deptype IN ('a', 'i')
      )
  LOOP
    IF rel.relkind = 'S' THEN
      EXECUTE format('ALTER SEQUENCE %s OWNER TO migrator', rel.name);
    ELSE
      EXECUTE format('ALTER TABLE %s OWNER TO migrator', rel.name);
    END IF;
  END LOOP;
  FOR proc IN
    SELECT p.oid::regprocedure AS name
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public'
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO migrator', proc.name);
  END LOOP;
END
$own$;

ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT SELECT ON TABLES TO dashboard;
ALTER DEFAULT PRIVILEGES FOR ROLE migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO agent;

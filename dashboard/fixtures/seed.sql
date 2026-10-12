-- Load one dashboard fixture run. Run cleanup.sql first if message 900001 already exists.
-- The trace page also needs logs/runs/000900.jsonl, and the file route needs
-- workspace/messages/900001/receipt.png. This script does not copy those files.
-- Apply with the owner role, then run cleanup.sql. Do not leave these rows loaded.

DO $seed$
DECLARE
  trip_id bigint;
  run_id bigint;
  txn_id bigint;
  bank_id bigint;
  expense_id bigint;
  person_id bigint;
BEGIN
  IF EXISTS (SELECT 1 FROM messages WHERE seq = 900001) THEN
    RAISE EXCEPTION 'dashboard fixture message 900001 already exists; run fixtures/cleanup.sql first';
  END IF;

  SELECT id INTO bank_id FROM accounts WHERE path = 'asset:bank:hdfc';
  SELECT id INTO expense_id FROM accounts WHERE path = 'expense:transport:local:bike_taxi';
  SELECT id INTO person_id FROM people WHERE name = 'Girlfriend' ORDER BY id LIMIT 1;
  IF bank_id IS NULL OR expense_id IS NULL THEN
    RAISE EXCEPTION 'fixture accounts asset:bank:hdfc and expense:transport:local:bike_taxi are missing';
  END IF;

  INSERT INTO messages (seq, text, folder_path, received_at)
  VALUES (
    900001,
    'Paid Rapido 120 for the ride home',
    'messages/900001',
    now()
  );

  INSERT INTO trips (name, start_date, end_date, location, notes)
  VALUES (
    'Fixture weekend',
    (timezone('Asia/Kolkata', now()))::date,
    (timezone('Asia/Kolkata', now()))::date,
    'Goa',
    'dashboard-fixture'
  )
  RETURNING id INTO trip_id;

  INSERT INTO agent_runs (
    message_seq, session_id, started_at, duration_ms, num_turns,
    cost_usd, is_error, result_text, log_path
  ) VALUES (
    900001,
    'fixture-session',
    now(),
    4200,
    2,
    0.012300,
    false,
    'Recorded ₹120 for Rapido.',
    'runs/000900.jsonl'
  )
  RETURNING id INTO run_id;

  PERFORM set_config('app.run_id', run_id::text, true);

  INSERT INTO transactions (
    date, description, merchant, external_ref, payment_method,
    trip_id, source_message_seq, status, notes, tags
  ) VALUES (
    (timezone('Asia/Kolkata', now()))::date,
    'dashboard-fixture rapido',
    'Rapido',
    'FIXTURE-DASH-900001',
    'upi',
    trip_id,
    900001,
    'confirmed',
    'dashboard-fixture',
    ARRAY['fixture']::text[]
  )
  RETURNING id INTO txn_id;

  INSERT INTO postings (transaction_id, account_id, amount)
  VALUES (txn_id, bank_id, -120.00);

  INSERT INTO postings (transaction_id, account_id, amount, person_id, memo)
  VALUES (txn_id, expense_id, 120.00, person_id, 'ride home');

  INSERT INTO attachments (transaction_id, message_seq, path, kind)
  VALUES (txn_id, 900001, 'messages/900001/receipt.png', 'photo');

  INSERT INTO tool_calls (run_id, seq, tool_name, input, output, is_error, duration_ms)
  VALUES
    (
      run_id,
      1,
      'mcp__finance__sql',
      jsonb_build_object(
        'query',
        'SELECT path FROM accounts WHERE path = ''expense:transport:local:bike_taxi'''
      ),
      'fixture-sql-result-ok',
      false,
      30
    ),
    (
      run_id,
      2,
      'mcp__finance__sql',
      jsonb_build_object('query', 'SELECT * FROM missing_fixture_table'),
      'fixture-sql-result-error: relation "missing_fixture_table" does not exist',
      true,
      12
    );
END
$seed$;

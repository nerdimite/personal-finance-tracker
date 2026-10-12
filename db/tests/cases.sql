-- "How your cases are recorded", run as the agent role inside one transaction
-- by `make test-db`. Amounts are small stand-ins for the spec's X / R / mine.
-- 11 transactions, 25 postings.

INSERT INTO people (name, relation) VALUES
  ('Girlfriend', 'partner'),
  ('Flatmate', 'flatmate'),
  ('Mom', 'family'),
  ('Friend', 'friend');

INSERT INTO accounts (path, kind, subtype, institution, person_id, attributes) VALUES
  ('asset:bank:hdfc', 'asset', 'bank', 'HDFC', NULL, '{}'::jsonb),
  ('asset:bank:sbi', 'asset', 'bank', 'SBI', NULL, '{}'::jsonb),
  ('liability:cc:amex', 'liability', 'credit_card', 'Amex', NULL, '{}'::jsonb),
  (
    'asset:receivable:flatmate', 'asset', 'receivable', NULL,
    (SELECT id FROM people WHERE name = 'Flatmate'), '{}'::jsonb
  ),
  ('asset:receivable:splitwise', 'asset', 'receivable', 'Splitwise', NULL, '{}'::jsonb),
  (
    'liability:payable:mom', 'liability', 'payable', NULL,
    (SELECT id FROM people WHERE name = 'Mom'), '{}'::jsonb
  ),
  ('asset:investment:mf_groww', 'asset', 'investment', 'Groww', NULL, '{}'::jsonb),
  ('expense:gifts', 'expense', 'category', NULL, NULL, '{}'::jsonb);

-- Rapido ₹120 via UPI (HDFC).
WITH txn AS (
  INSERT INTO transactions (date, description, merchant, payment_method, status)
  VALUES (DATE '2026-10-01', 'case: rapido', 'Rapido', 'upi', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('expense:transport:local:bike_taxi'::text, 120.00::numeric),
  ('asset:bank:hdfc'::text, -120.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Self-transfer SBI -> HDFC.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-01', 'case: transfer sbi hdfc', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('asset:bank:hdfc'::text, 1000.00::numeric),
  ('asset:bank:sbi'::text, -1000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Credit-card bill payment. Not an expense.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-02', 'case: cc bill', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('liability:cc:amex'::text, 5000.00::numeric),
  ('asset:bank:hdfc'::text, -5000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Flatmate sends their rent share.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-03', 'case: flatmate rent share', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('asset:bank:hdfc'::text, 10000.00::numeric),
  ('asset:receivable:flatmate'::text, -10000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Pay the landlord the full rent. Only your share is an expense.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-03', 'case: pay landlord', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('expense:housing:rent'::text, 10000.00::numeric),
  ('asset:receivable:flatmate'::text, 10000.00::numeric),
  ('asset:bank:hdfc'::text, -20000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Split dinner, logged in Splitwise. Only your share is an expense.
WITH txn AS (
  INSERT INTO transactions (date, description, merchant, payment_method, status)
  VALUES (DATE '2026-10-04', 'case: split dinner', 'Dinner', 'card', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('expense:food:dining_out'::text, 400.00::numeric),
  ('asset:receivable:splitwise'::text, 600.00::numeric),
  ('liability:cc:amex'::text, -1000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Dinner with girlfriend. You paid. The whole amount is an expense, tagged with her.
WITH txn AS (
  INSERT INTO transactions (date, description, payment_method, status)
  VALUES (DATE '2026-10-04', 'case: dinner girlfriend', 'card', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount, person_id)
SELECT
  txn.id,
  a.id,
  v.amount,
  CASE
    WHEN v.path = 'expense:food:dining_out'
      THEN (SELECT id FROM people WHERE name = 'Girlfriend')
  END
FROM txn
JOIN (VALUES
  ('expense:food:dining_out'::text, 800.00::numeric),
  ('liability:cc:amex'::text, -800.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Salary, and part of it is Mom's. Only your part is income.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-05', 'case: salary', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('asset:bank:hdfc'::text, 100000.00::numeric),
  ('income:salary'::text, -70000.00::numeric),
  ('liability:payable:mom'::text, -30000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Send Mom her share. Not an expense.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-06', 'case: send mom', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('liability:payable:mom'::text, 30000.00::numeric),
  ('asset:bank:hdfc'::text, -30000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- SIP. A transfer into an investment account, not an expense.
WITH txn AS (
  INSERT INTO transactions (date, description, payment_method, status)
  VALUES (DATE '2026-10-07', 'case: sip', 'auto_debit', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount)
SELECT txn.id, a.id, v.amount
FROM txn
JOIN (VALUES
  ('asset:investment:mf_groww'::text, 5000.00::numeric),
  ('asset:bank:hdfc'::text, -5000.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

-- Gift for a friend.
WITH txn AS (
  INSERT INTO transactions (date, description, status)
  VALUES (DATE '2026-10-08', 'case: gift', 'confirmed')
  RETURNING id
)
INSERT INTO postings (transaction_id, account_id, amount, person_id)
SELECT
  txn.id,
  a.id,
  v.amount,
  CASE
    WHEN v.path = 'expense:gifts'
      THEN (SELECT id FROM people WHERE name = 'Friend')
  END
FROM txn
JOIN (VALUES
  ('expense:gifts'::text, 1500.00::numeric),
  ('asset:bank:hdfc'::text, -1500.00::numeric)
) AS v(path, amount) ON true
JOIN accounts a ON a.path = v.path;

DO $do$
DECLARE
  n integer;
BEGIN
  SELECT count(*) INTO n FROM transactions WHERE description LIKE 'case:%';
  IF n <> 11 THEN
    RAISE EXCEPTION 'expected 11 case transactions, found %', n;
  END IF;
  SELECT count(*) INTO n
  FROM postings p
  JOIN transactions t ON t.id = p.transaction_id
  WHERE t.description LIKE 'case:%';
  IF n <> 25 THEN
    RAISE EXCEPTION 'expected 25 case postings, found %', n;
  END IF;
END
$do$;

SELECT set_config('app.run_id', 'case-audit', true);

INSERT INTO people (name, relation, notes) VALUES ('Audit Probe', 'friend', 'v1');
UPDATE people SET notes = 'v2' WHERE name = 'Audit Probe';
DELETE FROM people WHERE name = 'Audit Probe';

-- Remove dashboard fixture rows. Safe to run when none are present.
-- Uses the owner role. Does not touch ledger rows that lack the fixture marker.

DO $cleanup$
DECLARE
  txn_ids text[] := ARRAY[]::text[];
  posting_ids text[] := ARRAY[]::text[];
  attachment_ids text[] := ARRAY[]::text[];
  tool_ids text[] := ARRAY[]::text[];
  run_ids bigint[] := ARRAY[]::bigint[];
  run_texts text[] := ARRAY[]::text[];
  trip_ids text[] := ARRAY[]::text[];
  message_is_fixture boolean := false;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM messages WHERE seq = 900001 AND folder_path = 'messages/900001'
  ) INTO message_is_fixture;

  SELECT COALESCE(array_agg(id::text), ARRAY[]::text[]) INTO txn_ids
  FROM transactions WHERE notes = 'dashboard-fixture';

  SELECT COALESCE(array_agg(id::text), ARRAY[]::text[]) INTO posting_ids
  FROM postings WHERE transaction_id::text = ANY (txn_ids);

  SELECT COALESCE(array_agg(id::text), ARRAY[]::text[]) INTO attachment_ids
  FROM attachments
  WHERE transaction_id::text = ANY (txn_ids)
     OR (message_seq = 900001 AND message_is_fixture);

  SELECT COALESCE(array_agg(id), ARRAY[]::bigint[]) INTO run_ids
  FROM agent_runs
  WHERE log_path = 'runs/000900.jsonl'
     OR (message_seq = 900001 AND message_is_fixture);

  SELECT COALESCE(array_agg(id::text), ARRAY[]::text[]) INTO run_texts
  FROM agent_runs WHERE id = ANY (run_ids);

  SELECT COALESCE(array_agg(id::text), ARRAY[]::text[]) INTO tool_ids
  FROM tool_calls WHERE run_id = ANY (run_ids);

  SELECT COALESCE(array_agg(id::text), ARRAY[]::text[]) INTO trip_ids
  FROM trips WHERE notes = 'dashboard-fixture';

  DELETE FROM tool_calls WHERE run_id = ANY (run_ids);
  DELETE FROM attachments WHERE id::text = ANY (attachment_ids);
  DELETE FROM postings WHERE id::text = ANY (posting_ids);
  DELETE FROM transactions WHERE id::text = ANY (txn_ids);
  DELETE FROM agent_runs WHERE id = ANY (run_ids);
  DELETE FROM messages WHERE seq = 900001 AND folder_path = 'messages/900001';
  DELETE FROM trips WHERE id::text = ANY (trip_ids);

  DELETE FROM audit_log
  WHERE run_id = ANY (run_texts)
     OR ("table" = 'messages' AND row_id = '900001' AND message_is_fixture)
     OR ("table" = 'transactions' AND row_id = ANY (txn_ids))
     OR ("table" = 'postings' AND row_id = ANY (posting_ids))
     OR ("table" = 'attachments' AND row_id = ANY (attachment_ids))
     OR ("table" = 'tool_calls' AND row_id = ANY (tool_ids))
     OR ("table" = 'agent_runs' AND row_id = ANY (run_texts))
     OR ("table" = 'trips' AND row_id = ANY (trip_ids))
     OR COALESCE("new"->>'notes', "old"->>'notes') = 'dashboard-fixture'
     OR COALESCE("new"->>'log_path', "old"->>'log_path') = 'runs/000900.jsonl'
     OR COALESCE("new"->>'folder_path', "old"->>'folder_path') = 'messages/900001';
END
$cleanup$;

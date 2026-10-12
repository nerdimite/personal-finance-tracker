-- Bootstrap accounts. Personal bank, card, and people accounts arrive by chat.
-- Safe to re-run: existing paths are left as they are.

INSERT INTO accounts (path, kind, subtype, is_active, attributes)
VALUES
  ('equity:opening', 'equity', NULL, true, '{}'::jsonb),
  ('expense:uncategorized', 'expense', 'category', true, '{}'::jsonb),
  ('income', 'income', 'category', true, '{}'::jsonb),
  ('income:salary', 'income', 'category', true, '{}'::jsonb),
  ('income:interest', 'income', 'category', true, '{}'::jsonb),
  ('income:dividends', 'income', 'category', true, '{}'::jsonb),
  ('income:refunds_cashback', 'income', 'category', true, '{}'::jsonb),
  ('income:gifts_received', 'income', 'category', true, '{}'::jsonb),
  ('income:other', 'income', 'category', true, '{}'::jsonb)
ON CONFLICT (path) DO NOTHING;

UPDATE accounts AS child
SET parent_id = parent.id
FROM accounts AS parent
WHERE child.parent_id IS NULL
  AND parent.path = regexp_replace(child.path, ':[^:]+$', '')
  AND parent.path <> child.path;

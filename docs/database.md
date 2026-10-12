# Database, migrations and the MCP server

Double-entry ledger in Postgres, with a category tree synced from `workspace/CATEGORIES.md`. The SQL agent talks to Postgres through the finance MCP server. The Telegram bot and dashboard come in later tickets.

Postgres listens on `127.0.0.1:5433` (not the default 5432). The `bot` and `dashboard` services are described in `docker-compose.yml` and are not started yet. Inside that network the database host is `postgres`. Host-side commands use the URLs below, which default to `127.0.0.1`.

## Setup

```bash
cp .env.example .env   # if you do not already have one
make db-up             # docker compose up -d --wait postgres
make migrate           # apply db/migrations, set role passwords, write SCHEMA.md
make seed              # equity:opening, expense:uncategorized, income roots
make sync-categories   # upsert workspace/CATEGORIES.md
make test-db           # cases, balance rule, audit, role locks (throwaway database)
make backup            # pg_dump into backups/
```

Day to day: `make up` builds and starts the whole stack (postgres, bot, dashboard), `make down` stops it and keeps the database volume, `make restart`, `make ps`, and `make logs` (or `make logs S=bot`).

`make schema-doc` rewrites `workspace/SCHEMA.md` from the live catalog without applying a migration.

`make test-db` creates `finance_test`, migrates and seeds that database, runs the checks, and drops it. The `finance` database is not modified.

`make reset-ledger` refuses to run. `make reset-ledger YES=1` deletes transactions, postings, attachments, imports, balance checks, messages, agent runs, tool calls, trips, people, audit rows, and accounts that are not part of the seed or `CATEGORIES.md`, then re-runs seed and category sync. It does not delete `logs/` or `workspace/messages/`.

Copy `.env.example` only when `.env` is absent. An existing `.env` already holds the Telegram token; add the database keys beside it, do not replace the file. `OWNER_DATABASE_URL`, `AGENT_DATABASE_URL`, `MIGRATOR_DATABASE_URL`, and `DASHBOARD_DATABASE_URL` are what the Python tools open. `ft-migrate` sets the `agent`, `dashboard`, and `migrator` role passwords from those URLs. The `owner` password is `POSTGRES_PASSWORD`, and it has to be the password in `OWNER_DATABASE_URL`. The MCP server does not open `OWNER_DATABASE_URL`.

| Role | Can do |
| --- | --- |
| `owner` | Migrations and other DDL. The Docker superuser. |
| `agent` | Select, insert, update, and delete on ledger tables. No DDL, no writes to `audit_log` or `schema_migrations`. |
| `dashboard` | Select only. |

## Layout

```
db/migrations/     plain SQL, applied in filename order, one transaction each
db/seed.sql
db/tests/cases.sql the spec's posting examples
db/backup.sh
ft_db/             importable Python: migrations, seed, categories, schema doc
ft_bot/            Telegram bot (ticket 03)
ft_mcp/            finance MCP server (`sql`, `propose_migration`) and `ft-migration`
workspace/CATEGORIES.md
workspace/SCHEMA.md
workspace/mcp.json Claude Code stdio config, server name `finance`
bot/  dashboard/  logs/  backups/
```

A migration file is `NNN_name.sql`. The runner skips a file whose first comment is `-- status: pending` or `-- status: rejected`, so a proposal can sit in `db/migrations/` until it is approved. Files must not contain `BEGIN` or `COMMIT`.

## Library

Later tickets apply and dry-run migrations through this package:

```python
from ft_db import (
    apply_migration_sql,
    apply_pending_migrations,
    dry_run_sql,
    write_schema_doc,
)
```

`apply_migration_sql` runs the SQL and inserts `schema_migrations` in one transaction, then rewrites `workspace/SCHEMA.md`. `dry_run_sql` runs the SQL and rolls it back. Both expect an idle connection; they own the transaction.

Category sync and the connection URLs:

```python
from ft_db import sync_categories, owner_database_url
```

## Finance MCP server

`workspace/mcp.json` registers the server as `finance`. The command is `python -m ft_mcp` with no host path. Claude Code expands `${APP_RUN_ID:-}` in the `env` block, so an unset run id becomes empty instead of the literal `${APP_RUN_ID}`.

On the host, `python` has to be this project's virtualenv (`PATH=".venv/bin:$PATH"`, or run Claude from `uv run`). The server then imports `ft_mcp` and `ft_db` and finds `db/` and `.env` from the package location, not from the working directory.

In the bot container the repo is at `/app` and the image's Python is on `PATH`. Ticket 03 installs this project (or sets `PYTHONPATH=/app`) and points `AGENT_DATABASE_URL` and `MIGRATOR_DATABASE_URL` at host `postgres`. The bot inserts `agent_runs` and exports that id as `APP_RUN_ID` before it starts Claude. The `sql` tool copies it into `set_config('app.run_id', ...)`, which the audit trigger stores on `audit_log.run_id`. The server never opens the superuser URL.

| Tool or command | What it does |
| --- | --- |
| `sql` | Agent role, one transaction, several statements. JSON rows with a row cap and a truncation note, plus affected-row counts. Postgres errors come back as the tool result. `SET` and `set_config` are rejected. Statement timeout defaults to 15s (`SQL_STATEMENT_TIMEOUT`). |
| `propose_migration` | Writes `db/migrations/NNN_name.sql` with `-- status: pending` and a sha256 of the SQL. Dry-runs as the non-superuser `migrator` role, on a scratch database when that can be built, and otherwise inside a rollback. Refuses COPY, DO, extensions, SECURITY DEFINER, owner changes, role changes, and views without `security_invoker` before any database call. |
| `ft-migration list` | Versions, status, classification. |
| `ft-migration apply <id>` | Checks the sha256, runs `pg_dump -Fc` (the `pg_dump` on `PATH`) into `backups/`, then applies as `migrator` and regenerates `workspace/SCHEMA.md`. |
| `ft-migration reject <id>` | Sets the file status to rejected. The runner skips it. |

`AUTO_APPLY_ADDITIVE` defaults to false. When it is true, only an additive proposal is applied. Destructive SQL never auto-applies. That includes DROP, RENAME, ALTER TYPE, SET NOT NULL, ADD COLUMN NOT NULL without a default, CREATE OR REPLACE, any ALTER TABLE action other than ADD COLUMN, and anything that is not CREATE TABLE, CREATE INDEX, a `security_invoker` view, or a nullable-or-default ADD COLUMN.

`make migrate` applies a file with no `-- status:` header only when that file is `001`. A later headerless file is refused, including one that says `-- bootstrap: true`.

Apply git-commits `db/migrations` and `workspace/SCHEMA.md` when git works and the repository already has a commit. If git is missing, this directory is not a work tree, or there is no commit yet, apply still succeeds and reports that the commit was skipped.

```python
from ft_mcp import apply_migration, propose_migration, reject_migration, run_sql
```

## Backup

`make backup` runs `pg_dump` inside the Postgres container and writes `backups/finance-<timestamp>.dump`.

Cron, 03:15 every day:

```cron
15 3 * * * /Users/bhavesh/Projects/finance-tracker/db/backup.sh
```

launchd, already pointed at this checkout:

```bash
cp db/launchd/com.finance-tracker.backup.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.finance-tracker.backup.plist
```

Change the paths in the plist if the repo moves.

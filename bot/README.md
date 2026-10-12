# Bot

The Telegram bot is the Python package `ft_bot`. This directory holds the image and these notes. Run it on the host with `uv run python -m ft_bot` (`make bot`) or in Compose as the `bot` service.

The bot accepts only `TELEGRAM_ALLOWED_CHAT_ID`. Everyone else is ignored. A message is written to `workspace/messages/NNNNNN/` and a `messages` row as soon as it arrives. The worker then claims the lowest seq that has no `agent_runs` row. A restart re-queues those rows. A run left with a null duration is not run again; the bot says that message was interrupted. `TZ` is `Asia/Kolkata`.

## Claude

`cwd` is `workspace/`. The process is `claude -p` with `--setting-sources project`, `--settings` pointing at a file outside the workspace, `--strict-mcp-config --mcp-config /app/bot/agent/mcp.json`, `--tools Read,Write,Edit,Glob,Grep` (no Bash), and `--output-format stream-json --verbose`. `--bare` is not used. Writes are limited to `MEMORY.md` and `CATEGORIES.md`. The Claude process environment is an allowlist: it receives `AGENT_DATABASE_URL` and `MIGRATOR_DATABASE_URL`, not the superuser URL.

`--permission-mode dontAsk` is added so a headless run cannot wait on a permission prompt. Tools outside the allow list are denied.

The bot inserts `agent_runs` before it starts Claude and passes that id as `APP_RUN_ID`. The table has no status column: a row with a null duration and a null result is the in-progress run. The bot updates it after parsing `logs/runs/NNNNNN.jsonl`. Tool calls land in `tool_calls`. Writes use `OWNER_DATABASE_URL`.

Sessions live in `logs/bot_state.json`. The first message of a local day uses `--session-id`. Later messages that day use `--resume`. `/new` starts a new id. `/status` reports the queue, the session, and the last run. `/splitwise` lists pending Splitwise entries, oldest first, each as its own plain-text message with ✅ Added and ✖ Cancel. Those buttons update `splitwise_queue` on the worker, without the agent. A run that inserted queue rows appends `🧾 N added to Splitwise queue — /splitwise`.

Edit and Undo are inline buttons. Undo sends a message keyed by the agent run id. Edit waits for the next text message. See Approvals.

Inside Compose, `FT_DB_HOST=postgres` and `FT_DB_PORT=5432` rewrite the host database URLs (passwords unchanged) before the process starts. `./workspace`, `./logs`, `./db/migrations`, and `./backups` are mounted. `.git` is not. `/root/.claude` is the `claude_home` volume. The image includes Python 3.13, Node, `@anthropic-ai/claude-code`, `@llamaindex/liteparse`, qpdf, LibreOffice Writer (`soffice`), and `postgresql-client-17`. `zzz/` is gitignored and listed in `.dockerignore`, so statement files are not part of the build context.

`python -m ft_mcp` imports from `/app` (`PYTHONPATH=/app`).

## Approvals

Replies are plain text, split into 4096-character messages. Edit and Undo are attached only when that run inserted, updated, or deleted a `transactions` or `postings` row (`audit_log` for this run). A question, `/start`, or an import summary does not get them. Edit asks you to type the correction; the next text message is sent to the agent as `Correction for message NNNNNN: ...`. A photo does not consume that prompt.

After a run, each `import_batches` row this run left at `summarized` gets ✅ Commit and ❌ Discard. The press is a new message telling the agent to commit or reject that batch, and those buttons are removed.

A pending `propose_migration` result, or a pending file in `db/migrations/` that has not been shown yet, is a separate message with the name, reason, classification, and SQL. SQL longer than about 3500 characters is truncated in the message and sent in full as a `.sql` file. Apply and Reject call `apply_migration` and `reject_migration` in the bot. The agent is not told about a success. A failed apply becomes a message describing the error.

## File pre-parse

Before Claude starts, the bot writes sibling files next to each attachment and lists them under `<!-- ft-parsed -->` in `message.md`. PDFs that need a password are decrypted with `qpdf --password-file=-` (the password is on stdin, not argv) using `STATEMENT_PASSWORDS` (a JSON object or `bank=password` pairs). The bank name is recorded (`decrypted with: hdfc`). The password is not written to the folder, the prompt, or the log. `lit` and `qpdf` do not receive database URLs. `lit parse` then writes `NN_name.pdf.lit.txt`, plus `.lit.json` when the text is short or looks like a table. Spreadsheets and CSV files become one CSV per sheet, including a bank file's header block. Apple Numbers files become one CSV per table via `numbers-parser`. Images are left as-is. A parse failure is a note in `message.md`; the agent run still starts. After the run, the bot fills `attachments.sha256` for files in that folder.

## Isolation (`--setting-sources project`)

Checked on the host with Claude Code 2.1.296. A temp project had its own `CLAUDE.md` (`pineapple-ledger`). A planted auto-memory file (`zebra-accordion`) sat in the cwd's memory directory. The global rule file on this machine is `~/.claude/rules/context7.md`. There is no `~/.claude/CLAUDE.md`.

| Source | With `--setting-sources project` only | After the disable below |
| --- | --- | --- |
| Project `CLAUDE.md` | Loaded. The model quoted the marker. | Still loaded. |
| `~/.claude/CLAUDE.md` | Not present, so nothing to load. | Same. |
| Global rules (`~/.claude/rules`) | Not loaded. The model reported no user rules, and `ctx7` was absent from the stream. | Same. |
| User skills and user plugins | Not in the `system/init` skills list. Built-in skills still appear. | Same. |
| Auto memory | Loaded. The model quoted the planted `MEMORY.md`. `system/init` included `memory_paths.auto`. | Not loaded. `memory_paths` is absent. |

Both of these turn auto memory off. The bot uses both.

- Project file `workspace/.claude/settings.json`: `{"autoMemoryEnabled": false}`. Confirmed: a second run with that file and without the env var left `memory_paths` unset and the model reported no memory text.
- Env `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` on the `claude` process, in the image, and on the Compose service. Confirmed the same way.

User settings permission rules were replaced with empty lists in the debug log, which matches `--setting-sources project` skipping the user source.

"""Prove the ledger rules against a throwaway database. Run via `make test-db`.

The checks migrate, seed, and write only to `finance_test`, then drop it.
Row counts in the real finance database must be unchanged.
"""

from __future__ import annotations

import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql

from ft_db.categories import parse_categories, sync_categories
from ft_db.connect import (
    agent_database_url,
    connect,
    dashboard_database_url,
    migrator_database_url,
    owner_database_url,
    repo_root,
    safe_target,
    with_database,
)
from ft_db.migrate import apply_pending_migrations, dry_run_sql, sync_role_passwords
from ft_db.seed import apply_seed
from ft_db.sql_script import exec_script, split_sql

_TEST_DATABASE = "finance_test"

_REQUIRED_PATHS = {
    "expense:transport:local:bike_taxi": "expense",
    "expense:housing:utilities:electricity": "expense",
    "expense:housing:home_services:maid": "expense",
    "expense:shopping:clothing_footwear": "expense",
    "expense:food:ordered_in": "expense",
    "expense:food:dining_out": "expense",
    "expense:housing:rent": "expense",
    "expense:relationships:gifts": "expense",
    "expense:relationships:partner": "expense",
    "expense:travel:misc": "expense",
    "expense:misc": "expense",
    "income:salary": "income",
    "income:refunds_cashback": "income",
}


def run_db_tests() -> None:
    _check_parser()
    _check_splitter()
    _check_review_static()
    real_url = owner_database_url()
    if url_database(real_url) == _TEST_DATABASE:
        raise SystemExit(f"refusing to treat {_TEST_DATABASE} as the real database")
    before = _table_counts(real_url)
    admin_url = with_database(real_url, "postgres")
    error: BaseException | None = None
    try:
        _recreate_test_database(admin_url)
        _run_on_test_database(real_url)
    except BaseException as exc:
        error = exc
    finally:
        _drop_test_database(admin_url)
    after = _table_counts(real_url)
    if before != after:
        print(_count_diff(before, after), file=sys.stderr)
        if error is None:
            raise SystemExit("test-db changed row counts in the finance database")
    if error is not None:
        raise error
    print("test-db: ok (finance row counts unchanged)")


def _run_on_test_database(real_owner_url: str) -> None:
    owner_url = with_database(real_owner_url, _TEST_DATABASE)
    agent_url = with_database(agent_database_url(), _TEST_DATABASE)
    dashboard_url = with_database(dashboard_database_url(), _TEST_DATABASE)
    print(f"test database {safe_target(owner_url)}")
    with connect(owner_url) as owner:
        apply_pending_migrations(owner, regenerate_schema=False)
        sync_role_passwords(owner)
        apply_seed(owner)
        sync_categories(owner)
        _require_ready(owner)
        _check_comments(owner)
        again = apply_pending_migrations(owner, regenerate_schema=False)
        if again:
            raise SystemExit(f"migrate was not idempotent, applied {again}")
        dry_run_sql(owner, "ALTER TABLE people ADD COLUMN dry_run_probe text")
        leftover = owner.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'people'
              AND column_name = 'dry_run_probe'
            """
        ).fetchone()
        if leftover is not None:
            raise SystemExit("dry_run_sql left a column behind")
        owner.commit()
        _reset_fixtures(owner)
        marker = owner.execute("SELECT coalesce(max(id), 0) FROM audit_log").fetchone()[0]
        owner.commit()

        print(f"agent {safe_target(agent_url)}")
        with connect(agent_url) as agent:
            with agent.transaction():
                exec_script(agent, (repo_root() / "db" / "tests" / "cases.sql").read_text())
            _assert_cases(agent)
            _assert_audit(agent, marker)
            _assert_unbalanced_rejected(agent)
            _assert_void_unbalanced_allowed(agent)
            _assert_external_ref(agent)
            _expect_denied(agent, "DROP TABLE postings")
            _expect_denied(agent, "ALTER TABLE postings ADD COLUMN hack text")
            _expect_denied(agent, "CREATE TABLE agent_should_fail (id integer)")
            _expect_denied(
                agent,
                "INSERT INTO schema_migrations (version, name) VALUES ('999', 'nope')",
            )
            _expect_denied(agent, "TRUNCATE postings")
            _expect_denied(agent, "CREATE TEMP TABLE agent_temp (id integer)")
            _expect_denied(agent, "INSERT INTO messages (seq, text) VALUES (900001, 'nope')")
            _expect_denied(agent, "INSERT INTO agent_runs (session_id) VALUES ('nope')")
            _expect_denied(agent, "DELETE FROM tool_calls WHERE seq = 1")
            read_messages = agent.execute("SELECT count(*) FROM messages").fetchone()
            if read_messages is None:
                raise SystemExit("agent could not select messages")
            _assert_posting_count(agent)

        migrator_url = with_database(migrator_database_url(), _TEST_DATABASE)
        _assert_migrator(migrator_url)
        _assert_temp_shadow(owner, agent_url)
        _assert_headerless_refused(owner)
        _assert_scratch_dry_run(migrator_url)
        _assert_splitwise(agent_url, dashboard_url)
        _assert_dashboard(dashboard_url)
        _assert_inactive_category(owner)


def _check_parser() -> None:
    nodes = parse_categories((repo_root() / "workspace" / "CATEGORIES.md").read_text())
    by_path = {node.path: node for node in nodes}
    for path, kind in _REQUIRED_PATHS.items():
        node = by_path.get(path)
        if node is None or node.kind != kind:
            raise SystemExit(f"category tree missing {path} ({kind})")
    bike = by_path["expense:transport:local:bike_taxi"]
    if bike.parent_path != "expense:transport:local":
        raise SystemExit(f"bike_taxi parent is {bike.parent_path}")
    if len(nodes) < 70:
        raise SystemExit(f"expected a full category tree, parsed {len(nodes)}")


def _check_splitter() -> None:
    parts = split_sql(
        """
        CREATE FUNCTION f() RETURNS void
        LANGUAGE plpgsql AS $fn$
        BEGIN
          PERFORM 1;
        END;
        $fn$;
        -- tail comment; with a semicolon
        SELECT 1;
        """
    )
    if len(parts) != 2:
        raise SystemExit(f"SQL splitter returned {len(parts)} statements, expected 2")


def _require_ready(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE version = '001'"
    ).fetchone()
    if row is None:
        raise SystemExit("migration 001 is not applied; run make migrate")
    needed = (
        "equity:opening",
        "expense:uncategorized",
        "income:salary",
        "expense:transport:local:bike_taxi",
        "expense:housing:rent",
        "expense:food:dining_out",
    )
    found = {
        r[0]
        for r in conn.execute(
            "SELECT path FROM accounts WHERE path = ANY(%s)",
            (list(needed),),
        ).fetchall()
    }
    missing = [path for path in needed if path not in found]
    if missing:
        raise SystemExit(
            "missing accounts "
            + ", ".join(missing)
            + "; run make seed and make sync-categories"
        )
    conn.commit()


def _check_comments(conn: psycopg.Connection) -> None:
    missing_tables = conn.execute(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'v')
          AND obj_description(c.oid, 'pg_class') IS NULL
        ORDER BY 1
        """
    ).fetchall()
    missing_columns = conn.execute(
        """
        SELECT c.relname, a.attname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
          AND a.attnum > 0
          AND NOT a.attisdropped
          AND col_description(c.oid, a.attnum) IS NULL
        ORDER BY 1, a.attnum
        """
    ).fetchall()
    conn.commit()
    if missing_tables or missing_columns:
        lines = [f"table {name}" for (name,) in missing_tables]
        lines += [f"{table}.{column}" for table, column in missing_columns]
        raise SystemExit("missing comments:\n" + "\n".join(lines))


def _reset_fixtures(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute("DELETE FROM tool_calls")
        conn.execute("DELETE FROM agent_runs")
        conn.execute("DELETE FROM import_rows")
        conn.execute("DELETE FROM import_batches")
        conn.execute("DELETE FROM attachments")
        conn.execute("DELETE FROM splitwise_queue")
        conn.execute("DELETE FROM postings")
        conn.execute("DELETE FROM transactions")
        conn.execute("DELETE FROM balance_checks")
        conn.execute("DELETE FROM messages")
        conn.execute(
            """
            DELETE FROM accounts
            WHERE kind IN ('asset', 'liability')
               OR path = 'expense:gifts'
            """
        )
        conn.execute("DELETE FROM people")
        conn.execute("DELETE FROM trips")


def _balance(conn: psycopg.Connection, path: str) -> Decimal:
    row = conn.execute(
        "SELECT balance FROM v_balances WHERE path = %s",
        (path,),
    ).fetchone()
    if row is None:
        raise SystemExit(f"no balance for {path}")
    return row[0]


def _assert_cases(conn: psycopg.Connection) -> None:
    if _balance(conn, "asset:bank:hdfc") != Decimal("49380.00"):
        raise SystemExit(f"HDFC balance is {_balance(conn, 'asset:bank:hdfc')}, expected 49380.00")
    if _balance(conn, "liability:cc:amex") != Decimal("3200.00"):
        raise SystemExit(f"Amex balance is {_balance(conn, 'liability:cc:amex')}, expected 3200.00")
    if _balance(conn, "income:salary") != Decimal("-70000.00"):
        raise SystemExit("salary income was not recorded as -70000")
    if _balance(conn, "asset:bank:sbi") != Decimal("-1000.00"):
        raise SystemExit("SBI balance was not -1000")
    if _balance(conn, "asset:investment:mf_groww") != Decimal("5000.00"):
        raise SystemExit("SIP investment balance was not 5000")

    rapido = conn.execute(
        """
        SELECT category_path, amount, person_name
        FROM v_spend
        WHERE description = 'case: rapido'
        """
    ).fetchone()
    if rapido != ("expense:transport:local:bike_taxi", Decimal("120.00"), None):
        raise SystemExit(f"rapido spend row is {rapido}")

    dinner = conn.execute(
        """
        SELECT category_path, amount, person_name
        FROM v_spend
        WHERE description = 'case: dinner girlfriend'
        """
    ).fetchone()
    if dinner != ("expense:food:dining_out", Decimal("800.00"), "Girlfriend"):
        raise SystemExit(f"girlfriend dinner row is {dinner}")

    gift = conn.execute(
        """
        SELECT category_path, amount, person_name
        FROM v_spend
        WHERE description = 'case: gift'
        """
    ).fetchone()
    if gift != ("expense:gifts", Decimal("1500.00"), "Friend"):
        raise SystemExit(f"gift row is {gift}")

    month = conn.execute(
        """
        SELECT total
        FROM v_monthly_by_category
        WHERE month = DATE '2026-10-01'
          AND category_path = 'expense:transport:local:bike_taxi'
        """
    ).fetchone()
    if month is None or month[0] != Decimal("120.00"):
        raise SystemExit(f"monthly bike_taxi total is {month}")

    flatmate = conn.execute(
        """
        SELECT balance
        FROM v_people_balances
        WHERE person_name = 'Flatmate' AND path = 'asset:receivable:flatmate'
        """
    ).fetchone()
    mom = conn.execute(
        """
        SELECT balance
        FROM v_people_balances
        WHERE person_name = 'Mom' AND path = 'liability:payable:mom'
        """
    ).fetchone()
    if flatmate is None or flatmate[0] != Decimal("0.00"):
        raise SystemExit(f"flatmate balance is {flatmate}")
    if mom is None or mom[0] != Decimal("0.00"):
        raise SystemExit(f"mom payable balance is {mom}")
    conn.commit()


def _json_obj(value):
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        return json.loads(value)
    raise SystemExit(f"unexpected audit image type {type(value).__name__}")


def _assert_audit(conn: psycopg.Connection, marker: int) -> None:
    rows = conn.execute(
        """
        SELECT op, "old", "new"
        FROM audit_log
        WHERE run_id = 'case-audit' AND id > %s
        ORDER BY id
        """,
        (marker,),
    ).fetchall()
    ops = [row[0] for row in rows]
    if ops != ["INSERT", "UPDATE", "DELETE"]:
        raise SystemExit(f"audit ops {ops}")
    insert_old, insert_new = _json_obj(rows[0][1]), _json_obj(rows[0][2])
    update_old, update_new = _json_obj(rows[1][1]), _json_obj(rows[1][2])
    delete_old, delete_new = _json_obj(rows[2][1]), _json_obj(rows[2][2])
    if insert_old is not None or insert_new.get("notes") != "v1":
        raise SystemExit("audit insert image is wrong")
    if update_old.get("notes") != "v1" or update_new.get("notes") != "v2":
        raise SystemExit("audit update image is wrong")
    if delete_new is not None or delete_old.get("notes") != "v2":
        raise SystemExit("audit delete image is wrong")
    txn_inserts = conn.execute(
        """
        SELECT count(*)
        FROM audit_log
        WHERE "table" = 'transactions' AND op = 'INSERT' AND id > %s
        """,
        (marker,),
    ).fetchone()[0]
    if txn_inserts < 11:
        raise SystemExit(f"expected transaction insert audits, found {txn_inserts}")
    conn.commit()


def _assert_unbalanced_rejected(conn: psycopg.Connection) -> None:
    before = _balance(conn, "asset:bank:hdfc")
    conn.commit()
    try:
        with conn.transaction():
            exec_script(
                conn,
                """
                INSERT INTO transactions (date, description, status)
                VALUES (DATE '2026-10-09', 'case: unbalanced', 'confirmed');
                INSERT INTO postings (transaction_id, account_id, amount)
                SELECT t.id, a.id, v.amount
                FROM transactions t
                JOIN (VALUES
                  ('asset:bank:hdfc'::text, 100.00::numeric),
                  ('expense:uncategorized'::text, -40.00::numeric)
                ) AS v(path, amount) ON true
                JOIN accounts a ON a.path = v.path
                WHERE t.description = 'case: unbalanced';
                """,
            )
    except psycopg.Error as exc:
        if "expected 0" not in str(exc):
            raise
    else:
        raise SystemExit("unbalanced transaction was accepted")
    if _balance(conn, "asset:bank:hdfc") != before:
        raise SystemExit("rejected transaction changed the HDFC balance")
    left = conn.execute(
        "SELECT count(*) FROM transactions WHERE description = 'case: unbalanced'"
    ).fetchone()[0]
    if left != 0:
        raise SystemExit("rejected transaction was kept")
    conn.commit()


def _assert_void_unbalanced_allowed(conn: psycopg.Connection) -> None:
    before = _balance(conn, "asset:bank:hdfc")
    conn.commit()
    with conn.transaction():
        exec_script(
            conn,
            """
            INSERT INTO transactions (date, description, status)
            VALUES (DATE '2026-10-09', 'case: void unbalanced', 'void');
            INSERT INTO postings (transaction_id, account_id, amount)
            SELECT t.id, a.id, v.amount
            FROM transactions t
            JOIN (VALUES
              ('asset:bank:hdfc'::text, 100.00::numeric),
              ('expense:uncategorized'::text, -40.00::numeric)
            ) AS v(path, amount) ON true
            JOIN accounts a ON a.path = v.path
            WHERE t.description = 'case: void unbalanced';
            """,
        )
    if _balance(conn, "asset:bank:hdfc") != before:
        raise SystemExit("void transaction was included in the balance")
    shown = conn.execute(
        """
        SELECT count(*) FROM v_spend WHERE description = 'case: void unbalanced'
        """
    ).fetchone()[0]
    if shown != 0:
        raise SystemExit("void expense showed up in v_spend")
    conn.commit()


def _assert_external_ref(conn: psycopg.Connection) -> None:
    with conn.transaction():
        exec_script(
            conn,
            """
            WITH txn AS (
              INSERT INTO transactions (date, description, external_ref, status)
              VALUES (DATE '2026-10-10', 'case: utr one', 'UTR-CASE-1', 'confirmed')
              RETURNING id
            )
            INSERT INTO postings (transaction_id, account_id, amount)
            SELECT txn.id, a.id, v.amount
            FROM txn
            JOIN (VALUES
              ('expense:uncategorized'::text, 10.00::numeric),
              ('asset:bank:hdfc'::text, -10.00::numeric)
            ) AS v(path, amount) ON true
            JOIN accounts a ON a.path = v.path;
            """,
        )
    try:
        with conn.transaction():
            exec_script(
                conn,
                """
                WITH txn AS (
                  INSERT INTO transactions (date, description, external_ref, status)
                  VALUES (DATE '2026-10-10', 'case: utr dup', 'UTR-CASE-1', 'confirmed')
                  RETURNING id
                )
                INSERT INTO postings (transaction_id, account_id, amount)
                SELECT txn.id, a.id, v.amount
                FROM txn
                JOIN (VALUES
                  ('expense:food:dining_out'::text, 10.00::numeric),
                  ('asset:bank:hdfc'::text, -10.00::numeric)
                ) AS v(path, amount) ON true
                JOIN accounts a ON a.path = v.path;
                """,
            )
    except psycopg.Error as exc:
        if "external_ref" not in str(exc):
            raise
    else:
        raise SystemExit("duplicate external_ref on HDFC was accepted")

    with conn.transaction():
        exec_script(
            conn,
            """
            WITH txn AS (
              INSERT INTO transactions (date, description, external_ref, status)
              VALUES (DATE '2026-10-10', 'case: utr other account', 'UTR-CASE-1', 'confirmed')
              RETURNING id
            )
            INSERT INTO postings (transaction_id, account_id, amount)
            SELECT txn.id, a.id, v.amount
            FROM txn
            JOIN (VALUES
              ('expense:food:snacks_beverages'::text, 10.00::numeric),
              ('asset:bank:sbi'::text, -10.00::numeric)
            ) AS v(path, amount) ON true
            JOIN accounts a ON a.path = v.path;
            """,
        )
    conn.commit()


def _expect_denied(conn: psycopg.Connection, statement: str) -> None:
    try:
        with conn.transaction():
            conn.execute(statement)
    except psycopg.Error as exc:
        if exc.sqlstate != "42501":
            raise SystemExit(
                f"expected permission denied for {statement}, got {exc.sqlstate}: {exc}"
            ) from exc
    else:
        raise SystemExit(f"agent was allowed to run: {statement}")


def _check_review_static() -> None:
    from ft_db.sql_guard import proposal_refusal, session_control_reason
    from ft_mcp.classify import classify_statements
    from ft_mcp.proposals import MigrationError, assert_proposal_hash, propose_migration
    from ft_mcp.sql_tool import QueryFailed, run_sql

    refused = {
        "COPY (SELECT 1) TO PROGRAM 'psql -U owner -d finance -c \"select 1\"'": "COPY",
        "SELECT lo_export(1, '/tmp/x')": "lo_",
        "SELECT pg_read_file('/var/lib/postgresql/data/PG_VERSION')": "pg_read_",
        "CREATE EXTENSION dblink": "CREATE EXTENSION",
        "SELECT dblink_exec('dbname=finance', 'SELECT 1')": "dblink",
        "DO $$BEGIN RAISE EXCEPTION '%', (SELECT string_agg(rolname, ',') FROM pg_authid); END$$": "DO",
        "ALTER TABLE accounts OWNER TO agent": "OWNER",
        "ALTER ROLE agent SUPERUSER": "role statement",
        "SELECT set_config('app.run_id', 'x', true)": "set_config",
        "SET ROLE agent": "SET ROLE",
        "CREATE VIEW x AS SELECT * FROM pg_authid": "security_invoker",
        "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $f$ BEGIN EXECUTE 'COPY (SELECT 1) TO PROGRAM ''id'''; END $f$": "COPY",
    }
    migrations = repo_root() / "db" / "migrations"
    before = {path.name for path in migrations.glob("*.sql")}
    for statement, needle in refused.items():
        reason = proposal_refusal(statement)
        if reason is None or needle not in reason:
            raise SystemExit(f"proposal was not refused ({needle}): {statement!r} -> {reason}")
        try:
            propose_migration("review_exploit", statement, "must not write a file")
        except MigrationError as exc:
            if needle not in str(exc):
                raise SystemExit(f"propose error missing {needle}: {exc}") from exc
        else:
            raise SystemExit(f"propose accepted {needle}")
    after = {path.name for path in migrations.glob("*.sql")}
    if before != after:
        raise SystemExit(f"a refused proposal wrote {after - before}")

    session = "WITH x AS (SELECT set_config('app.run_id','1',true)) SELECT 1"
    if session_control_reason(session) is None:
        raise SystemExit("set_config in a CTE was allowed")
    do_block = "DO $$BEGIN PERFORM set_config('app.run_id','1',true); END$$"
    if session_control_reason(do_block) is None:
        raise SystemExit("set_config in a DO block was allowed")
    try:
        run_sql(session)
    except QueryFailed as exc:
        if "set_config" not in str(exc):
            raise
    else:
        raise SystemExit("sql tool ran set_config")

    mixed = classify_statements(
        ["ALTER TABLE postings ADD COLUMN x int, DISABLE TRIGGER postings_must_balance"]
    )
    if mixed.kind != "destructive":
        raise SystemExit(f"mixed ALTER was {mixed.kind}")
    constraint = classify_statements(["ALTER TABLE postings ADD CONSTRAINT hack CHECK (true)"])
    if constraint.kind != "destructive":
        raise SystemExit("ADD CONSTRAINT was additive")
    replaced = classify_statements(
        ["CREATE OR REPLACE VIEW v_spend WITH (security_invoker = true) AS SELECT 1"]
    )
    if replaced.kind != "destructive":
        raise SystemExit("CREATE OR REPLACE VIEW was additive")
    invoker = classify_statements(
        ["CREATE VIEW v_new WITH (security_invoker = true) AS SELECT 1"]
    )
    if invoker.kind != "additive":
        raise SystemExit(f"security_invoker view was {invoker.kind}: {invoker.notes}")
    numeric = classify_statements(
        ["ALTER TABLE postings ADD COLUMN x numeric(14,2), ADD COLUMN y int"]
    )
    if numeric.kind != "additive":
        raise SystemExit(f"paren comma split failed: {numeric.notes}")
    literal = classify_statements(
        ["ALTER TABLE people ADD COLUMN note text DEFAULT 'do not DROP this'"]
    )
    if literal.kind != "additive":
        raise SystemExit(f"DROP inside a literal was destructive: {literal.notes}")

    from ft_mcp.proposals import _render
    from ft_mcp.classify import Classification

    rendered = _render(
        status="pending",
        classification=Classification("additive"),
        reason="probe",
        up_sql="CREATE TABLE hash_probe (id integer);",
    )
    assert_proposal_hash(rendered)
    tampered = rendered.replace("hash_probe", "hash_probe_edited")
    try:
        assert_proposal_hash(tampered)
    except MigrationError as exc:
        if "sha256" not in str(exc):
            raise
    else:
        raise SystemExit("tampered proposal hash was accepted")


def _assert_posting_count(conn: psycopg.Connection) -> None:
    conn.commit()
    for description, sql_text, needle in (
        (
            "empty",
            "INSERT INTO transactions (date, description, status) VALUES (DATE '2026-10-11', 'case: empty', 'confirmed')",
            "at least 2 postings",
        ),
        (
            "one posting",
            """
            INSERT INTO transactions (date, description, status)
            VALUES (DATE '2026-10-11', 'case: one posting', 'confirmed');
            INSERT INTO postings (transaction_id, account_id, amount)
            SELECT t.id, a.id, 1
            FROM transactions t
            JOIN accounts a ON a.path = 'asset:bank:hdfc'
            WHERE t.description = 'case: one posting';
            """,
            "at least 2 postings",
        ),
    ):
        try:
            with conn.transaction():
                exec_script(conn, sql_text)
        except psycopg.Error as exc:
            if exc.sqlstate != "23514" or needle not in str(exc):
                raise SystemExit(f"{description} got {exc.sqlstate}: {exc}") from exc
        else:
            raise SystemExit(f"{description} transaction was accepted")
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO transactions (date, description, status)
            VALUES (DATE '2026-10-11', 'case: void empty', 'void')
            """
        )


def _assert_migrator(migrator_url: str) -> None:
    with connect(migrator_url) as conn:
        row = conn.execute(
            "SELECT rolsuper, rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()
        if row is None or row[0] or not row[1]:
            raise SystemExit(f"migrator privileges are {row}")
        try:
            with conn.transaction():
                conn.execute("COPY (SELECT 1) TO PROGRAM 'true'")
        except psycopg.Error:
            pass
        else:
            raise SystemExit("migrator COPY TO PROGRAM succeeded")
    print(f"migrator {safe_target(migrator_url)} nosuperuser")


def _assert_temp_shadow(owner: psycopg.Connection, agent_url: str) -> None:
    owner.execute(
        sql.SQL("GRANT TEMPORARY ON DATABASE {} TO agent").format(sql.Identifier(_TEST_DATABASE))
    )
    owner.commit()
    try:
        with connect(agent_url) as agent:
            try:
                with agent.transaction():
                    agent.execute(
                        """
                        CREATE TEMP TABLE postings (
                          transaction_id bigint,
                          account_id bigint,
                          amount numeric
                        )
                        """
                    )
                    agent.execute(
                        "INSERT INTO postings (transaction_id, account_id, amount) VALUES (0, 0, 1), (0, 0, -1)"
                    )
                    exec_script(
                        agent,
                        """
                        INSERT INTO public.transactions (date, description, status)
                        VALUES (DATE '2026-10-11', 'case: shadow balance', 'confirmed');
                        INSERT INTO public.postings (transaction_id, account_id, amount)
                        SELECT t.id, a.id, 999
                        FROM public.transactions t
                        JOIN public.accounts a ON a.path = 'asset:bank:hdfc'
                        WHERE t.description = 'case: shadow balance';
                        """,
                    )
            except psycopg.Error as exc:
                if exc.sqlstate != "23514":
                    raise SystemExit(f"shadow balance got {exc.sqlstate}: {exc}") from exc
            else:
                raise SystemExit("temp postings shadowed the balance check")

            try:
                with agent.transaction():
                    agent.execute(
                        """
                        CREATE TEMP TABLE audit_log (
                          id bigint,
                          "table" text,
                          row_id text,
                          op text,
                          "old" jsonb,
                          "new" jsonb,
                          run_id text,
                          at timestamptz
                        )
                        """
                    )
                    agent.execute(
                        """
                        UPDATE public.accounts
                        SET notes = 'shadow-probe'
                        WHERE path = 'equity:opening'
                        """
                    )
                    found = agent.execute(
                        """
                        SELECT count(*)
                        FROM public.audit_log
                        WHERE "new"->>'notes' = 'shadow-probe'
                        """
                    ).fetchone()[0]
                    if found < 1:
                        raise SystemExit("audit trigger wrote to a temp audit_log")
                    raise _Rollback()
            except _Rollback:
                pass
    finally:
        owner.execute(
            sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM agent").format(
                sql.Identifier(_TEST_DATABASE)
            )
        )
        owner.commit()
    with connect(agent_url) as agent:
        _expect_denied(agent, "CREATE TEMP TABLE agent_temp_again (id integer)")


class _Rollback(Exception):
    pass


def _assert_headerless_refused(owner: psycopg.Connection) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "002_headerless.sql").write_text("CREATE TABLE headerless_probe (id integer);\n")
        try:
            apply_pending_migrations(owner, root, regenerate_schema=False)
        except RuntimeError as exc:
            if "002_headerless.sql" not in str(exc):
                raise
        else:
            raise SystemExit("headerless 002 was applied")
        (root / "002_headerless.sql").unlink()
        (root / "003_marked.sql").write_text(
            "-- bootstrap: true\nCREATE TABLE marked_probe (id integer);\n"
        )
        try:
            apply_pending_migrations(owner, root, regenerate_schema=False)
        except RuntimeError as exc:
            if "003_marked.sql" not in str(exc):
                raise
        else:
            raise SystemExit("bootstrap marker on 003 was accepted")
        (root / "003_marked.sql").unlink()
        (root / "004_copy.sql").write_text(
            "-- status: applied\nCOPY (SELECT 1) TO PROGRAM 'true';\n"
        )
        try:
            apply_pending_migrations(owner, root, regenerate_schema=False)
        except RuntimeError as exc:
            if "COPY" not in str(exc):
                raise
        else:
            raise SystemExit("applied COPY file was accepted")


def _assert_splitwise(agent_url: str, dashboard_url: str) -> None:
    import json

    with connect(agent_url) as agent:
        with agent.transaction():
            person_id = agent.execute(
                """
                INSERT INTO people (name, relation)
                VALUES ('case splitmate', 'flatmate')
                RETURNING id
                """
            ).fetchone()[0]
            agent.execute(
                """
                INSERT INTO transactions (date, description, status)
                VALUES (DATE '2026-10-11', 'case: splitwise', 'confirmed')
                """
            )
            agent.execute(
                """
                INSERT INTO postings (transaction_id, account_id, amount)
                SELECT t.id, a.id, v.amount
                FROM transactions t
                JOIN (VALUES
                  ('asset:bank:hdfc'::text, -100.00::numeric),
                  ('expense:uncategorized'::text, 100.00::numeric)
                ) AS v(path, amount) ON true
                JOIN accounts a ON a.path = v.path
                WHERE t.description = 'case: splitwise'
                """
            )
            first_shares = json.dumps(
                [
                    {"person_id": None, "name": "me", "amount": 40},
                    {"person_id": person_id, "name": "case splitmate", "amount": 60},
                ]
            )
            second_shares = json.dumps(
                [
                    {"person_id": None, "name": "me", "amount": 10},
                    {"person_id": person_id, "name": "case splitmate", "amount": 15},
                ]
            )
            queue_id = agent.execute(
                """
                INSERT INTO splitwise_queue (
                  transaction_id, date, description, total_amount, split_method,
                  shares, my_share, others_owe, notes
                )
                SELECT id, DATE '2026-10-11', 'case: splitwise milk', 100.00, 'equal',
                       %s::jsonb, 40.00, 60.00, 'queued'
                FROM transactions
                WHERE description = 'case: splitwise'
                RETURNING id
                """,
                (first_shares,),
            ).fetchone()[0]
            agent.execute(
                """
                INSERT INTO splitwise_queue (
                  transaction_id, date, description, total_amount, split_method,
                  shares, my_share, others_owe
                )
                SELECT id, DATE '2026-10-11', 'case: splitwise eggs', 25.00, 'exact',
                       %s::jsonb, 10.00, 15.00
                FROM transactions
                WHERE description = 'case: splitwise'
                """,
                (second_shares,),
            )
            pending = agent.execute(
                """
                SELECT description, person_name, person_amount, total_owed
                FROM v_splitwise_pending
                WHERE description LIKE 'case: splitwise %%'
                ORDER BY description
                """
            ).fetchall()
            audited = agent.execute(
                """
                SELECT count(*)
                FROM audit_log
                WHERE "table" = 'splitwise_queue' AND op = 'INSERT'
                """
            ).fetchone()[0]
        if audited < 2:
            raise SystemExit("splitwise_queue insert was not audited")
        if [row[1] for row in pending] != ["case splitmate", "case splitmate"]:
            raise SystemExit(f"splitwise view people are {pending}")
        if [row[2] for row in pending] != [Decimal("15.00"), Decimal("60.00")]:
            raise SystemExit(f"splitwise person amounts are {pending}")
        if any(row[3] != Decimal("75.00") for row in pending):
            raise SystemExit(f"splitwise total owed is {pending}")
        _expect_denied(agent, f"DELETE FROM splitwise_queue WHERE id = {queue_id}")
        try:
            with agent.transaction():
                agent.execute(
                    "UPDATE splitwise_queue SET status = 'nope' WHERE id = %s",
                    (queue_id,),
                )
        except psycopg.Error as exc:
            if exc.sqlstate != "23514":
                raise SystemExit(f"bad splitwise status got {exc.sqlstate}: {exc}") from exc
        else:
            raise SystemExit("bad splitwise status was accepted")
        try:
            with agent.transaction():
                agent.execute(
                    "UPDATE splitwise_queue SET split_method = 'nope' WHERE id = %s",
                    (queue_id,),
                )
        except psycopg.Error as exc:
            if exc.sqlstate != "23514":
                raise SystemExit(f"bad split method got {exc.sqlstate}: {exc}") from exc
        else:
            raise SystemExit("bad split method was accepted")

    with connect(dashboard_url) as readonly:
        readonly.execute("SET default_transaction_read_only TO on")
        readonly.commit()
        try:
            with readonly.transaction():
                readonly.execute(
                    "UPDATE splitwise_queue SET status = 'cancelled' WHERE id = %s",
                    (queue_id,),
                )
        except psycopg.Error as exc:
            if exc.sqlstate != "25006":
                raise SystemExit(
                    f"read-only dashboard update got {exc.sqlstate}: {exc}"
                ) from exc
        else:
            raise SystemExit("read-only dashboard updated splitwise_queue")

    with connect(dashboard_url) as writer:
        writer.execute("SET default_transaction_read_only TO off")
        writer.commit()
        with writer.transaction():
            writer.execute(
                """
                UPDATE splitwise_queue
                SET status = 'added', added_at = now()
                WHERE id = %s
                """,
                (queue_id,),
            )
        marked = writer.execute(
            """
            SELECT status, added_at IS NOT NULL
            FROM splitwise_queue
            WHERE id = %s
            """,
            (queue_id,),
        ).fetchone()
        if marked != ("added", True):
            raise SystemExit(f"dashboard mark-added left {marked}")
        still = writer.execute(
            "SELECT count(*) FROM v_splitwise_pending WHERE id = %s",
            (queue_id,),
        ).fetchone()[0]
        if still != 0:
            raise SystemExit("added splitwise row is still pending")
        _expect_denied(
            writer,
            f"UPDATE splitwise_queue SET description = 'hack' WHERE id = {queue_id}",
        )
        _expect_denied(
            writer,
            f"UPDATE splitwise_queue SET notes = 'hack' WHERE id = {queue_id}",
        )
        _expect_denied(
            writer,
            "UPDATE people SET name = 'dashboard write' WHERE name = 'case splitmate'",
        )
        _expect_denied(writer, "INSERT INTO splitwise_queue (transaction_id, date, description, total_amount, split_method, shares, my_share, others_owe) VALUES (1, DATE '2026-10-11', 'x', 1, 'equal', '[]', 1, 0)")
    print("splitwise queue ok")


def _assert_scratch_dry_run(migrator_url: str) -> None:
    import os
    import shutil

    from ft_mcp.proposals import propose_migration

    previous = os.environ.get("MIGRATOR_DATABASE_URL")
    os.environ["MIGRATOR_DATABASE_URL"] = migrator_url
    path = None
    try:
        result = propose_migration(
            "scratch_probe",
            "CREATE TABLE scratch_probe (id integer);",
            "dry-run probe",
        )
        path = repo_root() / result["file"]
        mode = result["dry_run"]
        if shutil.which("pg_dump") and shutil.which("pg_restore") and mode == "rolled back":
            raise SystemExit("pg_dump is on PATH but dry-run did not use a scratch database")
        if mode not in {"scratch template", "scratch schema", "rolled back"}:
            raise SystemExit(f"unexpected dry-run mode {mode}")
        print(f"dry-run {mode}")
    finally:
        if previous is None:
            os.environ.pop("MIGRATOR_DATABASE_URL", None)
        else:
            os.environ["MIGRATOR_DATABASE_URL"] = previous
        if path is not None and path.is_file():
            path.unlink()


def _assert_dashboard(dashboard_url: str) -> None:
    with connect(dashboard_url) as dash:
        with dash.transaction():
            count = dash.execute("SELECT count(*) FROM v_balances").fetchone()[0]
        if count < 1:
            raise SystemExit("dashboard could not read v_balances")
        _expect_denied(dash, "INSERT INTO people (name) VALUES ('dashboard write')")
    print(f"dashboard {safe_target(dashboard_url)} read-only")


def url_database(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).path.lstrip("/")


def _table_counts(url: str) -> dict[str, int]:
    with connect(url) as conn:
        names = [
            row[0]
            for row in conn.execute(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                ORDER BY 1
                """
            ).fetchall()
        ]
        counts: dict[str, int] = {}
        for name in names:
            counts[name] = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))
            ).fetchone()[0]
        conn.commit()
    return counts


def _count_diff(before: dict[str, int], after: dict[str, int]) -> str:
    lines = ["finance row counts changed during test-db:"]
    for name in sorted(set(before) | set(after)):
        old = before.get(name)
        new = after.get(name)
        if old != new:
            lines.append(f"  {name}: {old} -> {new}")
    return "\n".join(lines)


def _recreate_test_database(admin_url: str) -> None:
    with connect(admin_url) as conn:
        conn.autocommit = True
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(_TEST_DATABASE)
            )
        )
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                sql.Identifier(_TEST_DATABASE)
            )
        )


def _drop_test_database(admin_url: str) -> None:
    with connect(admin_url) as conn:
        conn.autocommit = True
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(_TEST_DATABASE)
            )
        )


def _assert_inactive_category(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO accounts (path, kind, subtype, attributes)
            VALUES ('expense:food:removed_probe', 'expense', 'category', '{}'::jsonb)
            """
        )
    stats = sync_categories(conn)
    with conn.transaction():
        row = conn.execute(
            """
            SELECT is_active FROM accounts WHERE path = 'expense:food:removed_probe'
            """
        ).fetchone()
        still_there = conn.execute(
            "SELECT is_active FROM accounts WHERE path = 'equity:opening'"
        ).fetchone()
        uncategorized = conn.execute(
            "SELECT is_active FROM accounts WHERE path = 'expense:uncategorized'"
        ).fetchone()
        if row is None or row[0] is not False:
            raise SystemExit("removed category was not marked inactive")
        if still_there is None or still_there[0] is not True:
            raise SystemExit("sync touched equity:opening")
        if uncategorized is None or uncategorized[0] is not True:
            raise SystemExit("sync deactivated expense:uncategorized")
        if stats.deactivated < 1:
            raise SystemExit("sync reported no deactivations")
        conn.execute(
            "DELETE FROM accounts WHERE path = 'expense:food:removed_probe'"
        )
    print(
        f"categories: marked {stats.deactivated} inactive "
        "(removed probe was then deleted)"
    )


def _main() -> None:
    try:
        run_db_tests()
    except psycopg.Error as exc:
        print(f"database error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    _main()

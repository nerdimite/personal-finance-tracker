import "server-only";
import { connection } from "next/server";
import pg from "pg";
import { databaseUrlFrom } from "@/lib/database-url";
import { loadEnv } from "@/lib/env";
import { scrubSecrets } from "@/lib/format";

const { Pool, types } = pg;

types.setTypeParser(1082, (value) => value);
types.setTypeParser(1700, (value) => value);

const globalForPg = globalThis as unknown as {
  financeDashboardPool?: pg.Pool;
  financeDashboardWritePool?: pg.Pool;
};

function connectionString(): string {
  loadEnv();
  const raw = process.env.DASHBOARD_DATABASE_URL;
  if (!raw) throw new Error("DASHBOARD_DATABASE_URL is not set");
  return databaseUrlFrom(raw, process.env.DASHBOARD_DB_HOST, process.env.DASHBOARD_DB_PORT);
}

function getPool(): pg.Pool {
  if (!globalForPg.financeDashboardPool) {
    globalForPg.financeDashboardPool = new Pool({
      connectionString: connectionString(),
      max: 5,
      connectionTimeoutMillis: 5000,
      statement_timeout: 15000,
      application_name: "finance-dashboard",
      options: "-c default_transaction_read_only=on",
    });
    globalForPg.financeDashboardPool.on("error", () => {
      // The next query reports a scrubbed failure. Idle client errors stay in-process.
    });
  }
  return globalForPg.financeDashboardPool;
}

function getWritePool(): pg.Pool {
  if (!globalForPg.financeDashboardWritePool) {
    globalForPg.financeDashboardWritePool = new Pool({
      connectionString: connectionString(),
      max: 2,
      connectionTimeoutMillis: 5000,
      statement_timeout: 15000,
      application_name: "finance-dashboard-write",
      options: "-c default_transaction_read_only=off",
    });
    globalForPg.financeDashboardWritePool.on("error", () => {
      // The next statement reports a scrubbed failure. Idle client errors stay in-process.
    });
  }
  return globalForPg.financeDashboardWritePool;
}

async function run<T extends pg.QueryResultRow>(
  pool: pg.Pool,
  text: string,
  params: unknown[],
): Promise<T[]> {
  await connection();
  try {
    const result = await pool.query<T>(text, params);
    return result.rows;
  } catch (error) {
    const message = error instanceof Error ? error.message : "query failed";
    throw new Error(scrubSecrets(message));
  }
}

export async function query<T extends pg.QueryResultRow>(
  text: string,
  params: unknown[] = [],
): Promise<T[]> {
  return run(getPool(), text, params);
}

export async function writeQuery<T extends pg.QueryResultRow>(
  text: string,
  params: unknown[] = [],
): Promise<T[]> {
  return run(getWritePool(), text, params);
}

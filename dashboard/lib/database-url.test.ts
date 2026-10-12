import assert from "node:assert/strict";
import test from "node:test";
import { databaseUrlFrom, safeDatabaseTarget } from "./database-url.ts";

test("rewrites host and port and keeps the credential", () => {
  const next = databaseUrlFrom(
    "postgresql://dashboard:secret@127.0.0.1:5433/finance",
    "postgres",
    "5432",
  );
  const parsed = new URL(next);
  assert.equal(parsed.protocol, "postgresql:");
  assert.equal(parsed.username, "dashboard");
  assert.equal(parsed.password, "secret");
  assert.equal(parsed.hostname, "postgres");
  assert.equal(parsed.port, "5432");
  assert.equal(parsed.pathname, "/finance");
  assert.equal(safeDatabaseTarget(next), "postgres:5432/finance");
});

test("leaves the URL alone when no rewrite is configured", () => {
  const raw = "postgresql://dashboard:secret@127.0.0.1:5433/finance";
  const parsed = new URL(databaseUrlFrom(raw));
  assert.equal(parsed.hostname, "127.0.0.1");
  assert.equal(parsed.port, "5433");
});

test("does not echo an invalid URL", () => {
  assert.throws(
    () => databaseUrlFrom("not a url with secret-token"),
    (error: unknown) => {
      assert.ok(error instanceof Error);
      assert.equal(error.message.includes("secret-token"), false);
      return true;
    },
  );
});

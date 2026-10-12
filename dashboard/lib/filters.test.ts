import assert from "node:assert/strict";
import test from "node:test";
import {
  likeContains,
  parseTransactionFilters,
  transactionHref,
  transactionWhere,
} from "./filters.ts";

test("rejects unsafe filter values", () => {
  const filters = parseTransactionFilters({
    month: "2026-13",
    account: "0",
    tx: "12;drop",
    q: "  rapido  ",
    category: "expense:food",
  });
  assert.equal(filters.month, null);
  assert.equal(filters.accountId, null);
  assert.equal(filters.txId, null);
  assert.equal(filters.q, "rapido");
  assert.equal(filters.category, "expense:food");
});

test("keeps user text in parameters", () => {
  const filters = parseTransactionFilters({ q: "'; drop table transactions; --", category: "100%" });
  const { clause, params } = transactionWhere(filters);
  assert.equal(clause.includes("drop table"), false);
  assert.equal(params.some((value) => String(value).includes("drop table")), true);
  assert.equal(likeContains("a_b"), "%a!_b%");
  assert.match(clause, /ESCAPE '!'/);
});

test("anchors a category prefix so a longer sibling path does not match", () => {
  const { clause, params } = transactionWhere(parseTransactionFilters({ category: "expense:food" }));
  assert.match(clause, /a\.path = \$\d+ OR a\.path LIKE \$\d+ \|\| ':%' ESCAPE '!'/);
  assert.deepEqual(params, ["expense:food", "expense:food"]);

  const wild = transactionWhere(parseTransactionFilters({ category: "expense:food%" }));
  assert.deepEqual(wild.params, ["expense:food%", "expense:food!%"]);
});

test("builds a transaction link without dropping filters", () => {
  const href = transactionHref(
    parseTransactionFilters({ month: "2026-10", q: "rapido" }),
    { txId: "33" },
  );
  assert.equal(href, "/transactions?month=2026-10&q=rapido&tx=33");
});

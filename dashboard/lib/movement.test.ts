import assert from "node:assert/strict";
import test from "node:test";
import { movementOf } from "./movement.ts";

test("labels an expense, an income, and a transfer", () => {
  assert.deepEqual(movementOf("120.00", "0", "240.00"), { kind: "expense", amount: "120.00" });
  assert.deepEqual(movementOf("0", "-500.00", "1000.00"), { kind: "income", amount: "500.00" });
  assert.deepEqual(movementOf("0", "0", "200.00"), { kind: "transfer", amount: "100.00" });
});

test("does not label a mixed expense and income as an expense", () => {
  assert.deepEqual(movementOf("100.00", "-1000.00", "2000.00"), { kind: "mixed", amount: "-900.00" });
  assert.notEqual(movementOf("10.00", "-1.00", "22.00").kind, "expense");
});

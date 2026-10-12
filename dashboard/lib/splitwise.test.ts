import assert from "node:assert/strict";
import test from "node:test";
import { itemLines, parseShares, queueFilter, splitwiseCopyText } from "./splitwise.ts";

test("builds the copy-ready Splitwise block", () => {
  const text = splitwiseCopyText({
    description: "Blinkit: milk, eggs, detergent",
    totalAmount: "609.00",
    paidBy: "me",
    splitMethod: "equal",
    othersOwe: "304.50",
    shares: [
      { personId: null, name: "me", amount: "304.50" },
      { personId: "12", name: "Flatmate", amount: "304.50" },
    ],
  });
  assert.equal(
    text,
    "Blinkit: milk, eggs, detergent\n₹609.00 · paid by me · split equally (2)\nFlatmate owes ₹304.50",
  );
});

test("names the split method and keeps an unnamed remainder", () => {
  const text = splitwiseCopyText({
    description: "Dinner",
    totalAmount: "3000",
    paidBy: "me",
    splitMethod: "exact",
    othersOwe: "1800",
    shares: [
      { personId: null, name: "me", amount: "1200" },
      { personId: null, name: "others", amount: "1800" },
    ],
  });
  assert.match(text, /split by exact amounts \(2\)/);
  assert.match(text, /Others owe ₹1,800.00/);
  assert.equal(text.includes("me owes"), false);
});

test("parses shares and falls back when nobody else is named", () => {
  assert.deepEqual(parseShares([{ person_id: 4, name: " Mom ", amount: 10 }]), [
    { personId: "4", name: "Mom", amount: "10" },
  ]);
  const text = splitwiseCopyText({
    description: "Snacks",
    totalAmount: "40",
    paidBy: "me",
    splitMethod: "shares",
    othersOwe: "20",
    shares: [{ personId: null, name: "me", amount: "20" }],
  });
  assert.match(text, /split by shares \(1\)/);
  assert.match(text, /Others owe ₹20.00/);
  assert.deepEqual(itemLines([{ name: "milk", amount: "30" }, "eggs"]), ["milk · ₹30.00", "eggs"]);
});

test("defaults the queue filter to pending", () => {
  assert.equal(queueFilter(undefined), "pending");
  assert.equal(queueFilter("added"), "added");
  assert.equal(queueFilter("nope"), "pending");
});

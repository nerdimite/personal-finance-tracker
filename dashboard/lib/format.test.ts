import assert from "node:assert/strict";
import test from "node:test";
import { formatAge, formatDuration, inr, scrubSecrets } from "./format.ts";

test("formats rupees with Indian grouping", () => {
  assert.equal(inr(12840), "₹12,840.00");
  assert.equal(inr("100000"), "₹1,00,000.00");
  assert.equal(inr(-1500), "-₹1,500.00");
  assert.equal(inr(0), "₹0.00");
});

test("formats durations", () => {
  assert.equal(formatDuration(4200), "4.2 s");
  assert.equal(formatDuration(800), "800 ms");
  assert.equal(formatDuration(null), "—");
});

test("formats how long a queued item has waited", () => {
  const now = new Date("2026-10-12T12:00:00.000Z");
  assert.equal(formatAge("2026-10-12T11:59:30.000Z", now), "just now");
  assert.equal(formatAge("2026-10-12T10:20:00.000Z", now), "1h");
  assert.equal(formatAge("2026-10-09T12:00:00.000Z", now), "3d");
  assert.equal(formatAge(null), "—");
});

test("strips database URLs from errors", () => {
  const message = scrubSecrets("failed postgresql://dashboard:secret@127.0.0.1:5433/finance");
  assert.equal(message.includes("secret"), false);
  assert.match(message, /postgresql:\/\/…/);
});

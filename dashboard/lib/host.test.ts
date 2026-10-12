import assert from "node:assert/strict";
import test from "node:test";
import { allowedDashboardHost } from "./host.ts";

test("allows only the local dashboard hosts", () => {
  assert.equal(allowedDashboardHost("127.0.0.1:4100", "4100"), true);
  assert.equal(allowedDashboardHost("localhost:4100", "4100"), true);
  assert.equal(allowedDashboardHost(" LocalHost:4100 ", "4100"), true);
  assert.equal(allowedDashboardHost("127.0.0.1:3000", "3000"), true);
});

test("rejects a rebound or missing host", () => {
  assert.equal(allowedDashboardHost("evil.example", "3000"), false);
  assert.equal(allowedDashboardHost("evil.example:3000", "3000"), false);
  assert.equal(allowedDashboardHost("127.0.0.1", "3000"), false);
  assert.equal(allowedDashboardHost("127.0.0.1:3001", "3000"), false);
  assert.equal(allowedDashboardHost("localhost", "3000"), false);
  assert.equal(allowedDashboardHost("[::1]:3000", "3000"), false);
  assert.equal(allowedDashboardHost("127.0.0.1:3000.evil.example", "3000"), false);
  assert.equal(allowedDashboardHost(null, "3000"), false);
  assert.equal(allowedDashboardHost("", "3000"), false);
});

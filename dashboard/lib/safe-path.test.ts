import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { messageFileHref, resolveInside, resolveRunLog } from "./safe-path.ts";

test("message links stay under messages/", () => {
  assert.equal(messageFileHref("messages/900001/receipt.png"), "/files/900001/receipt.png");
  assert.equal(messageFileHref("messages/../.env"), null);
  assert.equal(messageFileHref("notes/a.png"), null);
  assert.equal(messageFileHref("messages/../../etc/passwd"), null);
});

test("resolveInside rejects traversal, directories, and escaping symlinks", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dash-files-"));
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "dash-outside-"));
  const secret = path.join(outside, "secret.txt");
  fs.writeFileSync(secret, "secret-bytes");
  fs.mkdirSync(path.join(root, "900001"));
  fs.writeFileSync(path.join(root, "900001", "receipt.png"), "png");
  fs.symlinkSync(secret, path.join(root, "900001", "escape.png"));

  const ok = resolveInside(root, ["900001", "receipt.png"]);
  assert.equal(ok && fs.readFileSync(ok, "utf8"), "png");
  assert.equal(resolveInside(root, ["..", "secret.txt"]), null);
  assert.equal(resolveInside(root, ["900001", "..", "900001", "receipt.png"]), null);
  assert.equal(resolveInside(root, ["900001"]), null);
  assert.equal(resolveInside(root, ["900001", "missing.png"]), null);
  assert.equal(resolveInside(root, ["900001", "escape.png"]), null);
  assert.equal(resolveInside(root, ["900001", "a\0b"]), null);
});

test("run logs resolve only to a file directly under logs/runs", () => {
  const logs = fs.mkdtempSync(path.join(os.tmpdir(), "dash-logs-"));
  const runs = path.join(logs, "runs");
  fs.mkdirSync(runs);
  fs.writeFileSync(path.join(runs, "000900.jsonl"), "{}\n");
  const outside = path.join(logs, "secret.txt");
  fs.writeFileSync(outside, "nope");
  fs.symlinkSync(outside, path.join(runs, "linked.jsonl"));

  assert.equal(resolveRunLog(logs, "runs/000900.jsonl").status, "ok");
  assert.equal(resolveRunLog(logs, "logs/runs/000900.jsonl").status, "ok");
  assert.equal(resolveRunLog(logs, "000900.jsonl").status, "ok");
  assert.equal(resolveRunLog(logs, "runs/missing.jsonl").status, "missing");
  assert.equal(resolveRunLog(logs, "../secret.txt").status, "rejected");
  assert.equal(resolveRunLog(logs, runs + "/../secret.txt").status, "rejected");
  assert.equal(resolveRunLog(logs, "linked.jsonl").status, "rejected");
  assert.equal(resolveRunLog(logs, null).status, "missing");
});

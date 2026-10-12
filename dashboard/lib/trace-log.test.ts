import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseTraceLog } from "./trace-log.ts";

const fixture = path.join(path.dirname(fileURLToPath(import.meta.url)), "../fixtures/000900.jsonl");

test("parses the sample stream-json log", () => {
  const parsed = parseTraceLog(fs.readFileSync(fixture, "utf8"));
  assert.equal(parsed.sessionId, "fixture-session");
  assert.equal(parsed.model, "claude-sonnet");
  assert.deepEqual(parsed.userTexts, ["Paid Rapido 120 for the ride home"]);
  assert.equal(parsed.turns.length, 2);
  assert.equal(parsed.turns[0]?.reasoning.length, 1);
  assert.match(parsed.turns[0]?.tools[0]?.query ?? "", /bike_taxi/);
  assert.equal(parsed.turns[0]?.tools[0]?.result, "fixture-sql-result-ok");
  assert.equal(parsed.turns[0]?.tools[0]?.isError, false);
  assert.match(parsed.turns[1]?.tools[0]?.query ?? "", /missing_fixture_table/);
  assert.equal(parsed.turns[1]?.tools[0]?.isError, true);
  assert.match(parsed.turns[1]?.tools[0]?.result ?? "", /fixture-sql-result-error/);
  assert.equal(parsed.finalText, "Recorded ₹120 for Rapido.");
  assert.equal(parsed.isError, false);
  assert.equal(parsed.durationMs, 4200);
  assert.equal(parsed.numTurns, 2);
  assert.equal(parsed.costUsd, 0.0123);
  assert.equal(parsed.skippedLines, 2);
  assert.deepEqual(parsed.unknownTypes, ["rate_limit_event"]);
});

test("ignores empty input and unknown shapes", () => {
  const parsed = parseTraceLog('{"type":"nope"}\nnull\n');
  assert.equal(parsed.turns.length, 0);
  assert.deepEqual(parsed.unknownTypes, ["nope"]);
  assert.equal(parsed.skippedLines, 1);
  assert.equal(parseTraceLog("").turns.length, 0);
});

test("keeps a tool result that arrives before its call", () => {
  const parsed = parseTraceLog(
    '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"early","content":"orphan"}]}}\n',
  );
  assert.equal(parsed.turns[0]?.tools[0]?.result, "orphan");
  assert.equal(parsed.turns[0]?.tools[0]?.name, "tool_result");
});

export type TraceTool = {
  id: string;
  name: string;
  input: unknown;
  query: string | null;
  result: string | null;
  isError: boolean;
};

export type TraceTurn = {
  texts: string[];
  reasoning: string[];
  tools: TraceTool[];
};

export type ParsedTrace = {
  sessionId: string | null;
  model: string | null;
  userTexts: string[];
  turns: TraceTurn[];
  finalText: string | null;
  isError: boolean | null;
  durationMs: number | null;
  numTurns: number | null;
  costUsd: number | null;
  skippedLines: number;
  unknownTypes: string[];
  truncated: boolean;
};

export function parseTraceLog(text: string): ParsedTrace {
  const parsed: ParsedTrace = {
    sessionId: null,
    model: null,
    userTexts: [],
    turns: [],
    finalText: null,
    isError: null,
    durationMs: null,
    numTurns: null,
    costUsd: null,
    skippedLines: 0,
    unknownTypes: [],
    truncated: false,
  };

  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    let event: unknown;
    try {
      event = JSON.parse(line);
    } catch {
      parsed.skippedLines += 1;
      continue;
    }
    if (!event || typeof event !== "object" || Array.isArray(event)) {
      parsed.skippedLines += 1;
      continue;
    }
    const record = event as Record<string, unknown>;
    const type = typeof record.type === "string" ? record.type : "";
    if (!type) {
      parsed.skippedLines += 1;
      continue;
    }
    if (type === "system") {
      rememberSession(parsed, record);
      if (typeof record.model === "string") parsed.model = record.model;
      continue;
    }
    if (type === "assistant") {
      const turn: TraceTurn = { texts: [], reasoning: [], tools: [] };
      for (const block of blocksOf(messageOf(record))) fillAssistant(turn, block);
      if (turn.texts.length || turn.reasoning.length || turn.tools.length) parsed.turns.push(turn);
      continue;
    }
    if (type === "user") {
      for (const block of blocksOf(messageOf(record))) fillUser(parsed, block);
      continue;
    }
    if (type === "result") {
      rememberSession(parsed, record);
      if (typeof record.result === "string") parsed.finalText = record.result;
      if (typeof record.is_error === "boolean") parsed.isError = record.is_error;
      if (typeof record.duration_ms === "number") parsed.durationMs = record.duration_ms;
      if (typeof record.num_turns === "number") parsed.numTurns = record.num_turns;
      const cost = record.total_cost_usd ?? record.cost_usd;
      if (typeof cost === "number") parsed.costUsd = cost;
      continue;
    }
    if (!parsed.unknownTypes.includes(type)) parsed.unknownTypes.push(type);
  }

  return parsed;
}

function rememberSession(parsed: ParsedTrace, record: Record<string, unknown>): void {
  if (typeof record.session_id === "string") parsed.sessionId = record.session_id;
}

function messageOf(record: Record<string, unknown>): unknown {
  if (record.message && typeof record.message === "object") return record.message;
  return record;
}

function blocksOf(message: unknown): Array<Record<string, unknown>> {
  if (!message || typeof message !== "object") return [];
  const content = (message as Record<string, unknown>).content;
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (!Array.isArray(content)) return [];
  return content.filter(
    (item): item is Record<string, unknown> =>
      !!item && typeof item === "object" && !Array.isArray(item),
  );
}

function fillAssistant(turn: TraceTurn, block: Record<string, unknown>): void {
  const type = typeof block.type === "string" ? block.type : "";
  if (type === "text" && typeof block.text === "string" && block.text.trim()) {
    turn.texts.push(block.text);
    return;
  }
  if (type === "thinking" || type === "redacted_thinking") {
    const text =
      typeof block.thinking === "string"
        ? block.thinking
        : typeof block.text === "string"
          ? block.text
          : "";
    if (text.trim()) turn.reasoning.push(text);
    return;
  }
  if (type === "tool_use") {
    const name = typeof block.name === "string" ? block.name : "unknown";
    const input = "input" in block ? block.input : null;
    turn.tools.push({
      id: typeof block.id === "string" ? block.id : `tool-${turn.tools.length + 1}`,
      name,
      input,
      query: queryFrom(name, input),
      result: null,
      isError: false,
    });
  }
}

function fillUser(parsed: ParsedTrace, block: Record<string, unknown>): void {
  const type = typeof block.type === "string" ? block.type : "";
  if (type === "text" && typeof block.text === "string" && block.text.trim()) {
    parsed.userTexts.push(block.text);
    return;
  }
  if (type !== "tool_result") return;
  const id = typeof block.tool_use_id === "string" ? block.tool_use_id : "";
  const result = textFromContent(block.content);
  const isError = block.is_error === true;
  const existing = id ? findTool(parsed.turns, id) : null;
  if (existing) {
    existing.result = result;
    existing.isError = isError;
    return;
  }
  parsed.turns.push({
    texts: [],
    reasoning: [],
    tools: [
      {
        id: id || "unmatched",
        name: "tool_result",
        input: null,
        query: null,
        result,
        isError,
      },
    ],
  });
}

function findTool(turns: TraceTurn[], id: string): TraceTool | null {
  for (const turn of turns) {
    const tool = turn.tools.find((item) => item.id === id);
    if (tool) return tool;
  }
  return null;
}

function queryFrom(name: string, input: unknown): string | null {
  if (!input || typeof input !== "object" || Array.isArray(input)) return null;
  const record = input as Record<string, unknown>;
  const query = record.query ?? record.sql;
  if (typeof query !== "string" || !query.trim()) return null;
  const sqlTool = name === "mcp__finance__sql" || name.endsWith("__sql");
  if (sqlTool || "query" in record || "sql" in record) return query;
  return null;
}

function textFromContent(content: unknown, depth = 0): string {
  if (depth > 6 || content == null) return "";
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => textFromContent(part, depth + 1))
      .filter((part) => part.trim())
      .join("\n");
  }
  if (typeof content === "object") {
    const record = content as Record<string, unknown>;
    if (typeof record.text === "string") return record.text;
    if ("content" in record) return textFromContent(record.content, depth + 1);
  }
  return "";
}

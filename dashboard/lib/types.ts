import type { SplitwiseShare } from "@/lib/splitwise";

export type OverviewTotals = {
  thisMonth: string;
  lastMonth: string;
  thisTotal: string;
  lastTotal: string;
  postingCount: number;
};

export type CategoryTotal = {
  topCategory: string;
  thisTotal: string;
  lastTotal: string;
};

export type MerchantTotal = {
  merchant: string;
  total: string;
  transactions: number;
};

export type PersonBalance = {
  personId: string;
  personName: string;
  relation: string | null;
  path: string;
  subtype: string | null;
  balance: string;
};

export type ImportBatch = {
  id: string;
  messageSeq: string | null;
  filePath: string | null;
  accountPath: string | null;
  periodStart: string | null;
  periodEnd: string | null;
  rowCount: number | null;
  newCount: number | null;
  duplicateCount: number | null;
  conflictCount: number | null;
  skippedCount: number | null;
};

export type AccountBalance = {
  accountId: string;
  path: string;
  kind: string;
  subtype: string | null;
  institution: string | null;
  balance: string;
};

export type MovementKind = "expense" | "income" | "transfer" | "mixed";

export type TransactionRow = {
  id: string;
  date: string;
  description: string | null;
  merchant: string | null;
  status: string;
  paymentMethod: string | null;
  tripName: string | null;
  categories: string | null;
  people: string | null;
  kind: MovementKind;
  amount: string;
};

export type PostingRow = {
  id: string;
  amount: string;
  memo: string | null;
  path: string;
  kind: string;
  personName: string | null;
};

export type AttachmentRow = {
  id: string;
  path: string;
  kind: string | null;
  transactionId: string | null;
};

export type MessageRow = {
  seq: string;
  text: string | null;
  receivedAt: Date | string;
  folderPath: string | null;
};

export type RunLink = {
  id: string;
  startedAt: Date | string;
  isError: boolean;
};

export type TransactionDetail = {
  id: string;
  date: string;
  description: string | null;
  merchant: string | null;
  externalRef: string | null;
  paymentMethod: string | null;
  status: string;
  notes: string | null;
  tags: string[];
  sourceMessageSeq: string | null;
  tripName: string | null;
  postings: PostingRow[];
  attachments: AttachmentRow[];
  message: MessageRow | null;
  runs: RunLink[];
  queue: SplitwiseQueueLink[];
};

export type SplitwisePersonOwed = {
  key: string;
  name: string;
  owed: string;
};

export type SplitwiseSummary = {
  pendingCount: number;
  oldestCreatedAt: Date | string | null;
  people: SplitwisePersonOwed[];
};

export type SplitwiseQueueLink = {
  id: string;
  status: string;
  description: string;
  othersOwe: string;
};

export type SplitwiseQueueItem = {
  id: string;
  transactionId: string;
  date: string;
  description: string;
  totalAmount: string;
  paidBy: string;
  splitMethod: string;
  shares: SplitwiseShare[];
  myShare: string;
  othersOwe: string;
  items: string[];
  groupName: string | null;
  status: string;
  createdAt: Date | string;
  addedAt: Date | string | null;
  messageSeq: string | null;
  notes: string | null;
};

export type FilterOptions = {
  months: string[];
  accounts: { id: string; path: string }[];
  people: { id: string; name: string }[];
  trips: { id: string; name: string }[];
  categories: string[];
};

export type RunSummary = {
  id: string;
  startedAt: Date | string;
  durationMs: number | null;
  numTurns: number | null;
  costUsd: string | null;
  isError: boolean;
  resultText: string | null;
  logPath: string | null;
  messageSeq: string | null;
  messageText: string | null;
};

export type RunDetail = RunSummary & {
  sessionId: string | null;
  folderPath: string | null;
  messageReceivedAt: Date | string | null;
};

export type ToolCallRow = {
  seq: number;
  toolName: string;
  input: unknown;
  output: string | null;
  isError: boolean;
  durationMs: number | null;
};

export type AuditRow = {
  id: string;
  tableName: string;
  rowId: string | null;
  op: string;
  oldRow: unknown;
  newRow: unknown;
  at: Date | string;
};

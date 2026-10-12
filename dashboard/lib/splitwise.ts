import { inr } from "./format.ts";

export type SplitwiseShare = {
  personId: string | null;
  name: string;
  amount: string;
};

export type QueueFilter = "pending" | "added" | "cancelled";

const METHODS: Record<string, string> = {
  equal: "split equally",
  exact: "split by exact amounts",
  percent: "split by percent",
  shares: "split by shares",
};

export function queueFilter(value: string | null | undefined): QueueFilter {
  if (value === "added" || value === "cancelled") return value;
  return "pending";
}

export function parseShares(value: unknown): SplitwiseShare[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const name = typeof row.name === "string" ? row.name.trim() : "";
    if (!name) return [];
    const personId = row.person_id == null || row.person_id === "" ? null : String(row.person_id);
    return [{ personId, name, amount: row.amount == null ? "0" : String(row.amount) }];
  });
}

export function itemLines(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (typeof item === "string" && item.trim()) return [item.trim()];
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const name = typeof row.name === "string" ? row.name.trim() : "";
    if (!name) return [];
    if (row.amount == null || row.amount === "") return [name];
    return [`${name} · ${inr(String(row.amount))}`];
  });
}

export function splitwiseCopyText(row: {
  description: string;
  totalAmount: string;
  paidBy: string;
  splitMethod: string;
  shares: SplitwiseShare[];
  othersOwe: string | null;
}): string {
  const others = row.shares.filter((share) => share.name.toLowerCase() !== "me");
  const count = row.shares.length;
  const method = METHODS[row.splitMethod] ?? `split ${row.splitMethod}`;
  const phrase = count > 0 ? `${method} (${count})` : method;
  const lines = [
    row.description,
    `${inr(row.totalAmount)} · paid by ${row.paidBy} · ${phrase}`,
  ];
  for (const share of others) {
    const group = share.name.toLowerCase() === "others";
    lines.push(`${group ? "Others" : share.name} ${group ? "owe" : "owes"} ${inr(share.amount)}`);
  }
  if (others.length === 0 && Number(row.othersOwe) !== 0) {
    lines.push(`Others owe ${inr(row.othersOwe)}`);
  }
  return lines.join("\n");
}

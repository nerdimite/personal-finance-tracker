const MONTHS = new Intl.DateTimeFormat("en-IN", {
  month: "long",
  year: "numeric",
  timeZone: "UTC",
});

const DATES = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const DATE_TIMES = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Kolkata",
});

const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

export function inr(value: string | number | null | undefined): string {
  const amount = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(amount)) return "—";
  return INR.format(amount);
}

export function formatUsd(value: string | number | null | undefined): string {
  if (value == null || value === "") return "—";
  const amount = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(amount)) return "—";
  return USD.format(amount);
}

export function formatMonth(iso: string): string {
  const [year, month] = iso.slice(0, 10).split("-").map(Number);
  return MONTHS.format(new Date(Date.UTC(year, month - 1, 1)));
}

export function formatDate(value: string | Date): string {
  if (value instanceof Date) {
    return DATES.format(
      new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate())),
    );
  }
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  return DATES.format(new Date(Date.UTC(year, month - 1, day)));
}

export function formatDateTime(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return DATE_TIMES.format(date);
}

export function formatAge(value: string | Date | null | undefined, now = new Date()): string {
  if (value == null || value === "") return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const ms = now.getTime() - date.getTime();
  if (ms < 60_000) return "just now";
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return `${days}d`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${rest}s`;
}

export function preview(text: string | null | undefined, max = 90): string {
  const flat = (text ?? "").replace(/\s+/g, " ").trim();
  if (!flat) return "No message text";
  if (flat.length <= max) return flat;
  return `${flat.slice(0, max - 1)}…`;
}

export function scrubSecrets(message: string): string {
  return message
    .replace(/postgres(?:ql)?:\/\/\S+/gi, "postgresql://…")
    .replace(/\bpassword[=:][^\s&]+/gi, "password=…");
}

export function clip(text: string, max = 8000): { text: string; clipped: boolean } {
  if (text.length <= max) return { text, clipped: false };
  return { text: text.slice(0, max), clipped: true };
}

const INR0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });

/** Whole rupees, en-IN grouping: ₹1,20,000. */
export function inr0(value: string | number | null | undefined): string {
  const amount = typeof value === "number" ? value : Number(value ?? 0);
  if (!Number.isFinite(amount)) return "—";
  return INR0.format(Math.round(amount));
}

/** Compact axis label: ₹850, ₹12k, ₹1.2L. */
export function inrShort(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 100000) return `₹${(value / 100000).toFixed(abs >= 1000000 ? 0 : 1)}L`;
  if (abs >= 1000) return `₹${Math.round(value / 1000)}k`;
  return `₹${Math.round(value)}`;
}

/** Month-over-month change for spend: up is bad (stop), down is good (go). */
export function pctChange(thisValue: string | number, lastValue: string | number): { text: string; tone: "up" | "down" | "flat" } {
  const a = Number(thisValue);
  const b = Number(lastValue);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b === 0) return { text: a > 0 ? "new" : "—", tone: "flat" };
  const change = ((a - b) / b) * 100;
  const text = Math.abs(change) < 0.5 ? "0%" : `${change > 0 ? "+" : "−"}${Math.abs(change).toFixed(0)}%`;
  if (Math.abs(change) < 5) return { text, tone: "flat" };
  return { text, tone: change > 0 ? "up" : "down" };
}

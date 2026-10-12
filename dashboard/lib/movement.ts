export function movementOf(
  expenseTotal: string,
  incomeTotal: string,
  absTotal: string,
): { kind: "expense" | "income" | "transfer" | "mixed"; amount: string } {
  const expense = Number(expenseTotal);
  const income = Number(incomeTotal);
  if (expense !== 0 && income !== 0) {
    return { kind: "mixed", amount: money(expense + income) };
  }
  if (expense !== 0) return { kind: "expense", amount: money(expense) };
  if (income !== 0) return { kind: "income", amount: money(Math.abs(income)) };
  const half = Math.round(Number(absTotal) * 100) / 2 / 100;
  return { kind: "transfer", amount: money(half) };
}

function money(value: number): string {
  if (!Number.isFinite(value)) return "0.00";
  return (Math.round(value * 100) / 100).toFixed(2);
}

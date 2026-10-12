"use server";

import { writeQuery } from "@/lib/db";
import { allowedDashboardHost } from "@/lib/host";
import { revalidatePath } from "next/cache";
import { headers } from "next/headers";

export async function setSplitwiseStatus(
  _previous: { error: string } | null,
  formData: FormData,
): Promise<{ error: string } | null> {
  const host = (await headers()).get("host");
  if (!allowedDashboardHost(host)) return { error: "Forbidden" };

  const id = String(formData.get("id") ?? "");
  const status = formData.get("status");
  if (!/^[1-9]\d{0,17}$/.test(id) || (status !== "added" && status !== "cancelled")) {
    return { error: "That update is not allowed." };
  }

  try {
    const rows = await writeQuery<{ id: string }>(
      `
      UPDATE splitwise_queue
      SET status = $2,
          added_at = CASE WHEN $2 = 'added' THEN now() ELSE added_at END
      WHERE id = $1::bigint
        AND status = 'pending'
      RETURNING id::text AS id
    `,
      [id, status],
    );
    if (rows.length === 0) return { error: "That item is no longer pending." };
  } catch {
    return { error: "Could not update the queue." };
  }

  revalidatePath("/");
  revalidatePath("/splitwise");
  revalidatePath("/transactions");
  return null;
}

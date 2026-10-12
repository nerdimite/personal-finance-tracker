"use client";

import { setSplitwiseStatus } from "@/app/splitwise/actions";
import { useActionState } from "react";

export function QueueActions({ id }: { id: string }) {
  const [state, action, pending] = useActionState(setSplitwiseStatus, null);

  return (
    <form action={action} className="queue-form">
      <input type="hidden" name="id" value={id} />
      <button type="submit" name="status" value="added" className="flat" disabled={pending}>
        Mark added
      </button>
      <button type="submit" name="status" value="cancelled" className="text" disabled={pending}>
        Dismiss
      </button>
      {state?.error ? <p className="banner">{state.error}</p> : null}
    </form>
  );
}

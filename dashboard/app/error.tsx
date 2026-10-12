"use client";

import { scrubSecrets } from "@/lib/format";

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <section className="panel">
      <h1>This page could not be loaded</h1>
      <p>{scrubSecrets(error.message)}</p>
      <button type="button" onClick={() => reset()}>Try again</button>
    </section>
  );
}

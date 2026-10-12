"use client";

import { useState } from "react";

export function CopyButton({ text }: { text: string }) {
  const [label, setLabel] = useState("Copy");

  return (
    <button
      type="button"
      className="gold"
      onClick={() => {
        navigator.clipboard.writeText(text).then(
          () => {
            setLabel("Copied");
            window.setTimeout(() => setLabel("Copy"), 1500);
          },
          () => setLabel("Copy failed"),
        );
      }}
    >
      {label}
    </button>
  );
}

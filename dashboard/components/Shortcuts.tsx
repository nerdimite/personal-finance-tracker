"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** `/` focuses search, Esc closes the open detail sheet. */
export function Shortcuts({ closeHref }: { closeHref: string | null }) {
  const router = useRouter();
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA");
      if (event.key === "/" && !typing) {
        const search = document.querySelector<HTMLInputElement>('input[name="q"]');
        if (search) {
          event.preventDefault();
          search.focus();
        }
      } else if (event.key === "Escape" && closeHref) {
        if (typing) (target as HTMLElement).blur();
        router.push(closeHref, { scroll: false });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [closeHref, router]);
  return null;
}

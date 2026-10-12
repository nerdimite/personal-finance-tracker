"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Overview" },
  { href: "/transactions", label: "Transactions" },
  { href: "/splitwise", label: "Splitwise" },
  { href: "/traces", label: "Traces" },
];

export function Nav({ splitwise }: { splitwise: number }) {
  const pathname = usePathname();
  return (
    <nav className="nav" aria-label="Primary">
      {LINKS.map((link) => {
        const current = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        const count = link.href === "/splitwise" && splitwise > 0 ? splitwise : null;
        return (
          <Link key={link.href} href={link.href} aria-current={current ? "page" : undefined}>
            <span>{link.label}</span>
            {count ? <span className="count" aria-label={`${count} pending`}>{count}</span> : null}
          </Link>
        );
      })}
    </nav>
  );
}

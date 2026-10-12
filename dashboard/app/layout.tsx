import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import { Gloock, JetBrains_Mono, Manrope } from "next/font/google";
import { Nav } from "@/components/Nav";
import { loadPendingCounts } from "@/lib/overview-extras";
import "./globals.css";

const serif = Gloock({ weight: "400", subsets: ["latin"], variable: "--font-serif", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], weight: ["400", "500", "700"], variable: "--font-mono", display: "swap" });
const sans = Manrope({ subsets: ["latin"], weight: ["400", "500", "600", "700"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Overview · Ledger", template: "%s" },
  description: "Local ledger overview, transactions, Splitwise queue, and agent traces.",
};

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export default async function RootLayout({ children }: { children: ReactNode }) {
  const pending = await loadPendingCounts();
  return (
    <html lang="en-IN" className={`${serif.variable} ${mono.variable} ${sans.variable}`}>
      <body>
        <a className="skip" href="#content">Skip to content</a>
        <div className="app">
          <aside className="rail">
            <Link className="brand" href="/">
              Ledger
            </Link>
            <Nav splitwise={pending.splitwise} />
          </aside>
          <main className="main" id="content">{children}</main>
        </div>
      </body>
    </html>
  );
}

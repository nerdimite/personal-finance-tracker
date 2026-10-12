# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One user: Bhavesh, the owner of this personal finance tracker. Nobody else views the dashboard. It is private and served on localhost only, so it can use his own shorthand, terminology, and dense detail.

He logs money day to day through a Telegram bot. The dashboard is where he sits down at his laptop, with two rhythms:
- **Quick glance:** a minute or two to see where things stand.
- **Deeper review:** 10–20 minutes, roughly monthly.

His two main jobs on the dashboard:
1. **Monthly money review:** where the money went this month compared with last month, by hierarchical category. Also the cross-cutting views: trips, spend with his girlfriend, and local transport.
2. **Clearing the Splitwise queue:** working through pending shared expenses, copying each into Splitwise by hand (its API is paid), then marking it added or dismissing it.

Secondary, occasional: balances and dues, and agent traces (debugging and improving the agent).

## Product Purpose

The tracker records everything about his personal money correctly, with almost no effort. Capture happens by chatting with an AI agent (Claude Code, headless) over Telegram: free-form text, screenshots, statements. The agent keeps a double-entry ledger in Postgres. The dashboard is the read-and-review surface over that ledger. It succeeds when a quick look answers "how am I doing this month?" and a monthly sit-down shows clearly where money went and leaves nothing pending in the Splitwise queue.

## Positioning

- **The ledger understands his real, messy money flows.** Self-transfers between his own accounts, rent pass-through from a flatmate, Splitwise shares, a salary split with his mother for tax reasons, loan EMIs to an uncle, and investments. None of these get counted as spending by mistake, because every transaction is double-entry: postings sum to zero.
- **Item-level categorisation.** Quick-commerce orders are split by item into categories.
- **Fully local and traced.** Every agent run is traced locally, from message to SQL to rows changed.

## Operating Context

- **Capture is Telegram-first.** The dashboard is not the input surface, except for one write: marking Splitwise queue items added or dismissed.
- **Splitwise ritual:** copy the prepared entry, add it in the Splitwise app, then mark it ✓ Added or Dismiss it.
- **Viewing:** on the laptop, at http://127.0.0.1:4100, as a Docker Compose service next to Postgres and the bot.
- **Money format:** Indian rupees with en-IN grouping (₹1,20,000). Months are in Asia/Kolkata time.

## Capabilities and Constraints

- **Pages today:** Overview (month spend vs last month, top categories, merchants, people balances, account balances, imports awaiting approval, Splitwise summary), Transactions (filters plus a detail drawer with postings, attachments, source message, Splitwise status), Splitwise (actionable queue: copy, ✓ Added, Dismiss; filters for pending/added/cancelled; per-person totals), Traces (agent runs, turns, SQL, audit rows).
- **Stack:** Next.js (App Router, TypeScript) with `pg`, server-rendered. Read-only DB role, plus a column-limited write for Splitwise status only. Host-header allowlist (localhost only).
- **Data model:** accounts tree, where categories are expense/income accounts with paths like `expense:food:ordered_in`; transactions and signed postings; people; trips; postings tagged with a person; tags; `splitwise_queue`; balance checks; agent runs and tool calls. The categories are defined in `workspace/CATEGORIES.md`.
- **Undecided:** the People, Trips, Accounts, and category drill-down pages are planned but not built.

## Evidence on Hand

Real data comes only from his own ledger. It's currently empty after a fresh start; he backfills from statements and screenshots. Don't fabricate sample transactions, balances, or people in the UI. Empty states must be honest.

## Product Principles

1. **Correct before pretty.** A number on screen must match the ledger exactly. Transfers, repayments, and receivables are never shown as spend.
2. **Answer the glance first.** "This month vs last" and "anything pending?" are visible without effort. Depth is one step away.
3. **Clear the queue fast.** The Splitwise flow is copy, act, done, with minimal friction and no ambiguity about what's left.
4. **Private and local.** No external calls, no sharing features, no multi-user concepts.
5. **Traceable.** Any figure can be followed back to its transactions, and any transaction back to the message and agent run that created it.

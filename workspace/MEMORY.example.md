# Memory

Long-term facts for the finance agent. Edit entries in place; keep sections tidy. No passwords, full account numbers, PAN, or addresses.

Copy this file to `MEMORY.md` (gitignored). The agent keeps it up to date as it learns.

## People

- **Mom**: family. Receives half of the salary (see Salary rule).
- **Flatmate name**: flatmate; sends their rent share monthly, which is forwarded to the landlord.
- **Landlord name**: landlord (rent ₹X/month).

## Accounts & aliases

- Bank savings (last4 XXXX) → `asset:bank:<bank>` (create on first use).
- Credit card → `liability:cc:<card>`, paid via CRED.
- UPI transfers between your own accounts are self-transfers.

## Salary rule

Describe how salary arrives and how it should be booked, e.g. part credited to you, part to a family member who sends some back.

## Loans

- **Loan from <person>** → `liability:loan:<person>`. Principal, amount repaid, remaining. EMIs are repayments, not expenses.

## Merchant rules

- Rapido → `expense:transport:local:bike_taxi`
- Uber, Ola → `expense:transport:local:cab`
- Swiggy, Zomato (food delivery) → `expense:food:ordered_in`
- CRED payment from a bank account → credit card bill payment (transfer to `liability:cc:*`), not an expense

## Sharing rules (Splitwise)

- Which recurring costs are split, with whom, and in what ratio.

## Recurring

- Rent, salary, EMIs.

## Preferences

- When tracking starts; which cross-cutting views matter (trips, partner spend, local transport).

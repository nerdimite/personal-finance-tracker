# Design

## World

**NeoPOP**: the CRED app's design language, which the user explicitly chose. Black-first, confident serif figures, monospace numbers, and the raised NeoPOP button that "plunks" when pressed. It's a working dashboard first. The world contributes type, palette, density, and one signature move; navigation and controls stay standard web.

Scene: one person at a laptop, usually in the evening, for a quick glance or a monthly 10–20 minute review. That puts dark first.

## Palette

| Token | Value | Job |
|---|---|---|
| `--ink` | `#0a0a0b` | page ground |
| `--raise` | `#131315` | raised regions (rail, drawers, cards that need separation) |
| `--well` | `#0e0e10` | inset wells (copy blocks, code, inputs) |
| `--line` | `#25252a` | hairlines; regions are divided by rules and space, not boxes |
| `--text` | `#f4f2ed` | primary text, NeoPOP white face |
| `--muted` | `#a7a39b` | secondary text (≥ 7:1 on ink) |
| `--faint` | `#8a867e` | tertiary labels (≥ 4.5:1 on ink) |
| `--gold` / `--gold-edge` | `#ecc767` / `#8f6f1f` | the action colour: primary NeoPOP buttons, current state, focus, selection |
| `--go` / `--go-edge` | `#33d17a` / `#137a46` | money down vs last month, "added", healthy |
| `--stop` / `--stop-edge` | `#ff6f5b` / `#a23322` | money up vs last month, negative balances, errors |
| `--steel` | `#7e8aa3` | the comparison series (last month) |

Colour only does jobs: gold means "needs your action" (the nav count, pending tile numbers, the one primary button). Links are `--text` with a hairline underline on hover. Green/red mark direction only when a change is ≥5%; smaller moves are `--faint`. No decorative colour.

## Type

- **Display:** Gloock, a high-contrast serif, used for exactly three things: the page `h1`, the one headline figure per page, and the detail sheet title. Section titles are Manrope 600 at ~15px.
- **Numbers & data:** JetBrains Mono with tabular numerals for every amount, date, count, and code-like value.
- **Interface text:** Manrope, 15px base, 600 for emphasis.
- Labels (table headers, filter labels, sub-headings) are sentence-case Manrope 12–13px in `--faint`: no uppercase, no tracking.
- Amounts are whole rupees (`inr0`) everywhere except the posting table in the detail sheet and the Splitwise copy blocks, where paise matter.
- No labels above headings: a heading carries its own weight.

## Signature move

**The NeoPOP plunk.** The one primary action in a context is a solid face with two drawn edge faces (right and bottom, as skewed planes, not a flat shadow); on press, the face travels into the edges. Exactly one per context (Copy on a Splitwise card). Secondary actions are flat outlined buttons (`button.flat`: Mark added, Apply), tertiary are text buttons (`button.text`: Dismiss). Data bars are flat thin lines, never extruded.

Headline figures keep their history: the month's spend shows a cumulative daily line against last month, with today marked.

## Minimalism (Linear-like restraint)

Never say a fact twice on one screen. Fold sparse columns into a row's sub-line. Keep filters to Search + Month, with the rest behind "More filters". Detail opens as an overlay sheet (Esc closes, `/` focuses search), so the table never reflows.

## Layout

- Left rail (brand, navigation with the Splitwise pending count), and content to a max of 1280px.
- Sections are separated by hairlines and generous space; boxed panels only for interactive objects (queue cards, the transaction sheet).
- Tables: hairline rows, mono right-aligned amounts, muted secondary lines.

## Motion

One authored moment: category slabs grow in on load (ease-out expo). Buttons plunk. Everything respects `prefers-reduced-motion`.

# PropLock design tokens: "midnight and gold"

Status: **in use since 2026-11-13.** It replaces the first "architectural" (black, zinc and orange) token set.
Code: [`frontend/src/styles/tokens.css`](../../frontend/src/styles/tokens.css). It is imported by `src/main.tsx` before `src/styles.css`. Every screen uses the tokens, including the AG Grid ledger.

## 1. Where the look comes from

The inspiration is a dark fintech landing page that the designer shared on 2026-11-13. We take its **patterns** only. We don't take its name, copy, photos, logo or numbers, and nothing from its subject matter (binary-options trading).

What we take:

1. **Ground:** a deep navy-charcoal page (`#0E131C`) with slightly lighter panels. The hero band has a soft gold glow, and on the sign-in page a faint gold grid.
2. **Gold:** one warm gold (`#E3B962`) for actions, line icons, prices and the small dot under feature titles.
3. **Titles:** a small lowercase line ("how every deal stays honest") above a wide-tracked UPPERCASE title in Montserrat.
4. **Buttons:** uppercase and tracked. The default is a thin gold outline; the main call to action is solid gold with dark text.
5. **Panels:** important cards are framed by a thin gold hairline with a soft gold glow. Tables have gold uppercase headers and zebra rows.
6. **Icons:** gold line icons in thin gold rings, four in a row, each with a short description.

What we change, and why:

| Inspiration | PropLock | Why |
|---|---|---|
| Gold on every highlight | Gold is for **actions only**; verified states stay **green** | Verification is what the product is about (chosen by the user on 2026-11-12) |
| Amber/gold for warnings | **Coral** (`#F2937A`) for "money outstanding / a person must look" | Amber would read as gold, the action colour |
| Dark only | Dark by default, plus an ivory light theme behind the toggle | Daylight readability on phones; the choice is kept per browser |
| Marketing filler ("trusted brokers", fake trades table) | Four true descriptions of Features 1–4, no figures | We never invent data |

## 2. Roles

| Role | Tokens | Used for |
|---|---|---|
| Ground | `--bg --surface --surface-2 --placeholder` | Page, panels, tracks and inputs, image placeholders |
| Hero | `--hero-ground --hero-ink --hero-muted --hero-gold` | Sign-in hero, manager sidebar, broker greeting, photo placeholders. Dark in **both** themes. |
| Inverse | `--inverse --inverse-ink` | Selected segmented tab, the tenant's own chat bubbles, the avatar |
| Text | `--ink --ink-2 --muted` | Headings and body, secondary text, captions |
| Lines | `--line --line-strong --line-gold` | Hairlines, input borders, the gold frames |
| Brand | `--brand` | Line icons, prices, the logo, dots. Large or bold text only. |
| Action | `--action --action-hover --action-ink --action-soft --action-text` | Primary and outline buttons, links, focus rings, the selected slot and nav item |
| Verified | `--accent --accent-ink --accent-soft --accent-text` | LIVE / VERIFIED / PAID / RESOLVED / booked, PASS, money in, trust ring |
| Warn | `--warn --warn-soft --warn-text` | CAN'T CHECK, SENT/UNPAID, a held slot, money out |

Status shapes stay as they were, so colour is never the only signal:

| Status | Shape |
|---|---|
| LIVE, VERIFIED, PAID, RESOLVED, BOOKED | filled green + ✓ |
| PASS, hold released | soft green with a green hairline |
| FAIL, DISPUTED, fee captured | filled `--ink` |
| NEEDS FIX, broker no-show | outlined `--ink` |
| CAN'T CHECK, SENT/UNPAID, HELD, SETTLING | dashed coral border on a soft coral fill |
| DRAFT, CANCELLED, IN REVIEW, expired | `--surface-2` grey |

## 3. Palette

| Token | Dark (default) | Light (`data-theme="light"`) |
|---|---|---|
| `--bg` | `#0E131C` | `#F7F4EC` |
| `--surface` | `#151C28` | `#FFFFFF` |
| `--surface-2` | `#1C2534` | `#EFEADD` |
| `--ink` / `--ink-2` / `--muted` | `#EFE9DC` / `#CBC5B7` / `#9AA3B2` | `#141A24` / `#3A4150` / `#5D6472` |
| `--line` / `--line-strong` | `#263043` / `#6C7689` | `#E2DCCB` / `#8B8473` |
| `--action` / `--action-ink` | `#E3B962` / `#10151F` | `#8A6416` / `#FFFFFF` |
| `--action-soft` / `--action-text` | `#2B2617` / `#F1D28F` | `#F6ECD2` / `#6A4C0E` |
| `--accent` / `--accent-ink` | `#5BD198` / `#062619` | `#1E7A4C` / `#FFFFFF` |
| `--accent-soft` / `--accent-text` | `#12342A` / `#A3EBC6` | `#DCF2E5` / `#175F3B` |
| `--warn` / `--warn-soft` / `--warn-text` | `#F2937A` / `#3A1F1B` / `#F9C2B3` | `#B3442A` / `#FBE6DF` / `#8F3420` |
| `--hero-ink` / `--hero-muted` / `--hero-gold` | `#EFE9DC` / `#B4BCCB` / `#E3B962` | same (the hero band stays dark) |

### Contrast (WCAG 2.1; text needs 4.5:1, control borders 3:1), measured

| Foreground | Background | Need | Dark | Light |
|---|---|---|---|---|
| `--ink` | `--bg` | 4.5 | 15.38 ✓ | 15.88 ✓ |
| `--ink` | `--surface` | 4.5 | 14.13 ✓ | 17.46 ✓ |
| `--ink-2` | `--surface` | 4.5 | 9.94 ✓ | 10.23 ✓ |
| `--muted` | `--bg` | 4.5 | 7.32 ✓ | 5.41 ✓ |
| `--muted` | `--surface` | 4.5 | 6.72 ✓ | 5.95 ✓ |
| `--muted` | `--surface-2` | 4.5 | 6.05 ✓ | 4.95 ✓ |
| `--line-strong` | `--surface` | 3.0 | 3.74 ✓ | 3.72 ✓ |
| `--action-ink` | `--action` | 4.5 | 9.91 ✓ | 5.36 ✓ |
| `--action` | `--surface` | 4.5 | 9.26 ✓ | 5.36 ✓ |
| `--action` | `--bg` | 4.5 | 10.09 ✓ | 4.88 ✓ |
| `--action-text` | `--action-soft` | 4.5 | 10.31 ✓ | 6.73 ✓ |
| `--accent-ink` | `--accent` | 4.5 | 8.48 ✓ | 5.33 ✓ |
| `--accent` | `--surface` | 4.5 | 8.97 ✓ | 5.33 ✓ |
| `--accent-text` | `--accent-soft` | 4.5 | 9.84 ✓ | 6.53 ✓ |
| `--warn` | `--surface` | 4.5 | 7.51 ✓ | 5.56 ✓ |
| `--warn-text` | `--warn-soft` | 4.5 | 9.63 ✓ | 6.52 ✓ |
| `--inverse-ink` | `--inverse` | 4.5 | 9.91 ✓ | 13.58 ✓ |
| `--hero-ink` / `--hero-muted` / gold | hero ground | 4.5 | 14.42 / 9.13 / 9.45 ✓ | same |

## 4. Type

| Use | Font | Notes |
|---|---|---|
| Hero and section titles | Montserrat 700, UPPERCASE, tracking 0.04–0.08em | One word may be gold (`<em>` in `.hero-title`) |
| Page and card titles | Montserrat 700, sentence case | Long listing titles stay readable |
| Eyebrow (`.eyebrow`) | Inter 12–13 px, lowercase, muted | Sits above an uppercase title |
| Labels, buttons, pills, table headers, KPI labels | Inter 600–700, 10.5–12.5 px, UPPERCASE, tracking 0.08–0.14em | |
| Body | Inter 15 px / 1.55 | |
| Money | Montserrat, tabular figures, gold for prices and totals | |

## 5. Shape, depth and motion

- Radius: `--radius-pin` 14 px (cards, dialogs), `--radius` 10 px, `--radius-sm` 6 px (buttons, inputs, pills, chips: crisper, like the inspiration).
- Depth comes from hairlines and the gold glow (`--glow`) on framed panels, hovered cards, the primary button and the selected slot. Shadows are deep but quiet.
- Motion: 150 ms for colour and press, 300 ms for card lift, 500 ms for photo zoom on hover. All of it is 0 ms under `prefers-reduced-motion`.

## 6. Theme switch

`ThemeToggle` (in `components.tsx`) sits on the sign-in page, in the manager sidebar and in the broker header. It sets `data-theme="light"` on `<html>` and saves the choice in `localStorage` (`proplock.theme`). A small script in `index.html` applies the saved choice before the first paint. Storage access is wrapped in try/catch, so a blocked storage means the theme simply lasts for one page.

## 7. AG Grid

`LedgerGrid.tsx` keeps `themeQuartz.withParams`, with `var(--surface)`, `var(--ink)`, `var(--surface-2)` and `var(--line)` for the grid, `var(--action-text)` for the headers, `var(--action)` as the accent, and `var(--action-soft)` for the selected row. `browserColorScheme: "inherit"` makes its scrollbars follow the theme.

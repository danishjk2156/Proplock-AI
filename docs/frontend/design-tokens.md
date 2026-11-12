# PropLock design tokens (Phase 0)

Status: **proposed 2026-11-12**, waiting for approval before Phase 1.
Code: [`frontend/src/styles/tokens.css`](../../frontend/src/styles/tokens.css). Preview: [`token-preview.html`](token-preview.html).

## 1. Where the look comes from

Inspiration: a single-page real-estate design ("AURA Estates, architectural realty") that the designer collected and generated from Pinterest real-estate pins. It is recorded in the `<inspiration>` element of [`frontend-prompt.xml`](frontend-prompt.xml). We take its **patterns**. We do not take its name, copy, photos or fake numbers.

What we take:

1. **Palette:** a matte black (`#09090B`) and zinc-grey ground (`#FAFAFA`, `#F4F4F5`, `#E4E4E7`) with **one orange** for the call to action. Orange appears sparingly: buttons, a dot in the logo, small markers.
2. **Type:** Plus Jakarta Sans at 700–800 with tight tracking for headings and prices. Inter for body text. Tiny uppercase "eyebrow" labels (11 px, 0.14em tracking). Tabular figures for money.
3. **Cards:** white, rounded 16 px, with a 1 px hairline border and almost no shadow. Hover darkens the border, lifts the card with a large soft shadow and zooms the photo slightly (scale 1.05, 500 ms).
4. **Over the photo:** a dark gradient at the bottom, a matte-black price tag at bottom left, and small uppercase badges at top left (dark, or white on the photo).
5. **Chrome:** a sticky white top bar with a blur. A segmented control (black "selected" pill on a grey track) for tabs and filters. Chips are rounded 12 px, black when selected and outlined otherwise. There is a dark "hero" band with a search console inside it.

What we change, and why:

| Inspiration | PropLock | Why |
|---|---|---|
| Orange `#F97316` on buttons with white text | Buttons use `#C2410C`. The bright orange is decorative only. | White on `#F97316` is 2.80:1, which fails WCAG AA. White on `#C2410C` is 5.18:1. |
| Orange for every highlight, including "verified" | **Green** for verified states (LIVE, PASS, PAID, RESOLVED, trust); orange for actions only | Verification is what the product is about, and orange reads as a warning. It would also collide with amber "money outstanding". The user chose this on 2026-11-12. |
| "Saved" heart, wishlist | Not used | We don't invent features without an API (plan §8). |
| Muted grey `#71717A` | `#65656D` | `#71717A` on `#F4F4F5` is 4.40:1, which fails. `#65656D` is 5.26:1. |
| Input borders `#E4E4E7` | `#8A8A93` (`--line-strong`) | Form controls need 3:1 against their background. |
| Light only | Light and dark (matte black ground) | Plan §4. |

## 2. Roles

| Role | Tokens | Used for |
|---|---|---|
| Ground | `--bg --surface --surface-2 --placeholder` | Page, cards, tracks and tiles, image placeholders |
| Inverse | `--inverse --inverse-ink --inverse-muted` | Matte-black hero band, selected chips and segmented items, tooltips |
| Text | `--ink --ink-2 --muted` | Headings and body, secondary text, captions |
| Lines | `--line --line-strong` | Hairlines and dividers, input borders |
| Brand | `--brand` | Logo dot and small markers. **Never text and never a fill behind text.** |
| Action | `--action --action-hover --action-ink --action-soft --action-text` | Primary button, links, focus ring, active tab, the "+ Create" button |
| Verified | `--accent --accent-hover --accent-ink --accent-soft --accent-text` | LIVE / VERIFIED / PAID / RESOLVED (filled, tick), PASS (soft), trust ring |
| Warn | `--warn --warn-soft --warn-text` | CAN'T CHECK, SENT/UNPAID (dashed), needs a person, the DEMO banner |

Status shapes stay as they are today (plan F8), so colour is never the only signal:

| Status | Shape |
|---|---|
| LIVE, VERIFIED, PAID, RESOLVED | filled `--accent` + ✓ |
| PASS | `--accent-soft` fill, `--accent-text` + ✓ |
| FAIL | filled `--ink` + ✕ |
| NEEDS FIX | outlined `--ink` |
| CAN'T CHECK, SENT, UNPAID | dashed `--warn` border, `--warn-soft` fill |
| DISPUTED | filled `--ink` + ⚠ |
| DRAFT, CANCELLED, IN REVIEW | `--surface-2` grey |

## 3. Palette

| Token | Light | Dark |
|---|---|---|
| `--bg` | `#FAFAFA` | `#09090B` |
| `--surface` | `#FFFFFF` | `#18181B` |
| `--surface-2` | `#F4F4F5` | `#27272A` |
| `--ink` | `#09090B` | `#FAFAFA` |
| `--ink-2` | `#3F3F46` | `#D4D4D8` |
| `--muted` | `#65656D` | `#A1A1AA` |
| `--line` | `#E4E4E7` | `#27272A` |
| `--line-strong` | `#8A8A93` | `#71717A` |
| `--inverse` / `--inverse-ink` | `#09090B` / `#FAFAFA` | `#FAFAFA` / `#09090B` |
| `--brand` | `#F97316` | `#F97316` |
| `--action` / `--action-ink` | `#C2410C` / `#FFFFFF` | `#FB923C` / `#09090B` |
| `--action-soft` / `--action-text` | `#FFEDD5` / `#9A3412` | `#431407` / `#FED7AA` |
| `--accent` / `--accent-ink` | `#15803D` / `#FFFFFF` | `#4ADE80` / `#052E16` |
| `--accent-soft` / `--accent-text` | `#DCFCE7` / `#166534` | `#14532D` / `#BBF7D0` |
| `--warn` | `#A16207` | `#FACC15` |
| `--warn-soft` / `--warn-text` | `#FEF9C3` / `#854D0E` | `#422006` / `#FDE68A` |

### Contrast (WCAG 2.1; text needs 4.5:1, UI borders 3:1)

| Foreground | Background | Need | Light | Dark |
|---|---|---|---|---|
| `--ink` | `--bg` | 4.5:1 | 19.06 ✓ | 19.06 ✓ |
| `--ink` | `--surface` | 4.5:1 | 19.90 ✓ | 16.97 ✓ |
| `--ink-2` | `--surface` | 4.5:1 | 10.44 ✓ | 11.99 ✓ |
| `--muted` | `--bg` | 4.5:1 | 5.53 ✓ | 7.76 ✓ |
| `--muted` | `--surface` | 4.5:1 | 5.78 ✓ | 6.91 ✓ |
| `--muted` | `--surface-2` | 4.5:1 | 5.26 ✓ | 5.81 ✓ |
| `--line-strong` | `--surface` | 3.0:1 | 3.42 ✓ | 3.67 ✓ |
| `--action-ink` | `--action` | 4.5:1 | 5.18 ✓ | 8.79 ✓ |
| `--action` | `--surface` | 4.5:1 | 5.18 ✓ | 7.83 ✓ |
| `--action` | `--bg` | 4.5:1 | 4.96 ✓ | 8.79 ✓ |
| `--action-text` | `--action-soft` | 4.5:1 | 6.38 ✓ | 11.56 ✓ |
| `--accent-ink` | `--accent` | 4.5:1 | 5.02 ✓ | 8.55 ✓ |
| `--accent` | `--surface` | 4.5:1 | 5.02 ✓ | 10.17 ✓ |
| `--accent-text` | `--accent-soft` | 4.5:1 | 6.49 ✓ | 7.52 ✓ |
| `--warn` | `--surface` | 4.5:1 | 4.92 ✓ | 11.57 ✓ |
| `--warn-text` | `--warn-soft` | 4.5:1 | 6.38 ✓ | 11.70 ✓ |
| `--inverse-ink` | `--inverse` | 4.5:1 | 19.06 ✓ | 19.06 ✓ |
| `--inverse-muted` | `--inverse` | 4.5:1 | 7.76 ✓ | 7.41 ✓ |

Text over photos (price tags, badges) always sits on a solid `rgba(9,9,11,0.9)` tag or a white tag, never directly on the image.

## 4. Type

| Step | Size / line height | Font, weight | Use |
|---|---|---|---|
| Hero | 44 / 1.1 (32 on phones) | Jakarta 800, -0.02em | Sign-in and explore hero |
| 2xl | 32 / 1.15 | Jakarta 800 | Page title (desktop), big money figures |
| xl | 24 / 1.2 | Jakarta 800 | Page title (phone), KPI values |
| lg | 20 / 1.25 | Jakarta 700 | Section titles, price on the close-up |
| md | 17 / 1.3 | Jakarta 700 | Pin titles, dialog titles |
| base | 15 / 1.5 | Inter 400/500 | Body |
| sm | 13 / 1.45 | Inter 500 | Secondary text, table cells |
| xs | 12 / 1.4 | Inter 500 | Captions, helper text |
| eyebrow | 11 / 1.3 | Inter 600, uppercase, 0.14em | Labels above fields, badge text, KPI labels |

Money uses `font-variant-numeric: tabular-nums`. Fonts load from Google Fonts, which `index.html` already does.

## 5. Shape, depth and motion

- Radius: `--radius-pin` 16 px (pins, cards, dialogs), `--radius` 12 px (buttons, inputs, chips, tags), `--radius-sm` 8 px (badges, segmented items), `--radius-pill` for the search bar and avatars.
- Borders do the work and shadows stay quiet: `--shadow-xs` at rest, `--shadow-lg` on hover and on dialogs.
- Spacing is a 4 px grid (`--sp-1` … `--sp-14`). Tap targets are at least `--tap` (44 px).
- Motion: `--dur-fast` 150 ms (colour, press scale 0.99), `--dur` 300 ms (card lift, sheet), `--dur-slow` 500 ms (photo zoom). Easing is `--ease`. All durations are 0 under `prefers-reduced-motion`.

## 6. Dark theme

The dark theme uses a matte-black ground (`#09090B`) with zinc surfaces and the same roles. Action, verified and warn colours move to their lighter tints so they pass contrast on black. The OS setting applies it unless the user picks a theme. The avatar-menu toggle sets `data-theme` on `<html>`.

## 7. AG Grid

`LedgerGrid.tsx` keeps `themeQuartz.withParams` with `var(--surface)`, `var(--ink)`, `var(--surface-2)`, `var(--line)` and `var(--accent-soft)`. Phase 1 changes `accentColor` to `var(--action)` and `wrapperBorderRadius` to 16, so the grid follows both themes.

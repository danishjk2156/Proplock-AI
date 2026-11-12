# Frontend redesign: Pinterest-inspired PropLock web app

Wireframes (structure, content, behaviour): [`docs/frontend/wireframe.html`](../../docs/frontend/wireframe.html). Open it in a browser; screens are S00–S19.
Implementation prompt for a coding agent: [`docs/frontend/frontend-prompt.xml`](../../docs/frontend/frontend-prompt.xml).
Owner: frontend developer. Backend: unchanged except the one optional endpoint in §6.

Status: **DRAFT 2026-11-11.** Decisions marked *(proposed)* need a yes before Phase 1.

---

## 1. Goal (one paragraph)

Restyle and restructure the whole web app (tenant, broker and agency manager, across Feature 1 listing audit and Feature 4 invoice ledger) with a **Pinterest-inspired** look: image-first masonry "pins", a close-up page, boards, one big rounded search bar, a "+ Create" button and a mobile bottom tab bar. The **visual language** (colour, type, radius, imagery, motion) comes from the Pinterest inspiration the designer collects. The **structure, content and behaviour** come from the wireframes. Nothing the app does today may be lost, and nothing may be faked.

## 2. Two inputs, two jobs

| Input | Decides | Never decides |
|---|---|---|
| Pinterest inspiration (pasted into the prompt) | palette, type scale, radius, shadows, image treatment, hover/motion, density, iconography mood | what a screen shows, statuses, money rules, which data is real |
| `wireframe.html` | screens, layout regions, content, states, interactions, data source per screen | colours, fonts, exact spacing |

**Never copy Pinterest's brand:** no logo, no "Pinterest red" `#E60023`, no name, no copied icons or illustrations. Take the *patterns* only.

## 3. Screens

| # | Screen | Route | Role | Data (existing API) | Today's file | Change |
|---|---|---|---|---|---|---|
| S00 | App shell: top nav, search/copilot, "+", tab bar | all | all | `/me`, `/manager/reviews` (badge) | `manager/Layout.tsx`, `components.tsx` AppBar | **new shell** |
| S01 | Sign in | `/` | broker, manager | `GET /me` | `Login.tsx` | restyle + collage |
| S02 | Explore verified flats (masonry feed) | `/explore` | tenant | **new** `GET /public/listings` | — | **new, behind flag** |
| S03 | Listing close-up | `/l/:id` | tenant | `GET /public/listings/{id}`, `/public/photos/{id}` | `public/PublicListing.tsx` | redesign |
| S04 | Is this genuine? | `/v/:auditId` | tenant | `GET /public/audits/{id}/verify` | `public/Verify.tsx` | restyle |
| S05 | Book a visit (Feature 3) | sheet from S03 | tenant | none yet | — | "coming soon" sheet |
| S06 | Broker home and status boards | `/b` | broker | `/broker/listings`, `/broker/mandates`, `/me` | `broker/Home.tsx` | redesign (boards + masonry) |
| S07 | Check-in photo | `/b/listings/:id/checkin` | broker | `POST/GET/DELETE …/photos` | `broker/Checkin.tsx` | restyle |
| S08 | Write the ad + pre-check | `/b/listings/:id/ad` | broker | `POST /broker/precheck`, `POST …/submit` | `broker/Draft.tsx` | redesign (preview pin + coach) |
| S09 | Audit result | `/b/audits/:id` | broker | `GET /audits/{id}` | `broker/AuditResult.tsx` | restyle |
| S10 | Live listing | `/b/listings/:id` | broker | `confirm-availability`, `mark-rented`, `/ledger/closable` | `broker/ListingStatus.tsx` | restyle + "Lock for a tenant" |
| S11 | Broker money ledger | `/b/ledger` | broker | `/ledger`, `/ledger/config`, `/ledger/copilot` | `ledger/Pages.tsx`, `Cockpit.tsx` | restyle |
| S12 | Flats and mandates | `/m` | manager | `/manager/properties`, `/mandates`, `/brokers`, photos | `manager/Flats.tsx` | redesign (flat pins + panel) |
| S13 | Register a flat (modal) | from "+" | manager | `POST /manager/properties` | `manager/Flats.tsx` | move into modal |
| S14 | Review queue | `/m/reviews` | manager | `/manager/reviews`, `POST /manager/reviews/{id}` | `manager/Reviews.tsx` | restyle |
| S15 | Money ledger cockpit | `/m/ledger` | manager | `/ledger`, `/ledger/config`, `/ledger/copilot` | `ledger/Cockpit.tsx`, `LedgerGrid.tsx` | copilot moves into nav search |
| S16 | Closing and invoice drawer | drawer | both | `/closings/{id}`, `/refresh`, `/qr`, `/cancel`, `/dev/disputes/simulate` | `ledger/ClosingDrawer.tsx` | restyle, focus trap |
| S17 | Close a flat (modal) | from "+", S10 | both | `/ledger/closable`, `…/close`, `/dev/ledger/simulate-capture` | `Cockpit.tsx` CloseFlat | move into modal |
| S18 | Disputes | `/m/disputes` | manager | `/manager/disputes`, `/draft`, `/approve` | `ledger/Disputes.tsx` | evidence as masonry, citation links |
| S19 | Empty, loading, error states | all | all | — | `components.tsx` | skeleton pins |

## 4. Architecture

- **Keep:** Vite + React 18 + TypeScript strict + React Router 6, `api.ts` (`X-API-Key`, readable errors), `types.ts`, `format.ts`, `useLoad`, AG Grid Community 33.3.2 for the ledger.
- **Design tokens** in `src/styles/tokens.css` as CSS custom properties, filled from the inspiration: `--bg --surface --surface-2 --ink --muted --line --accent --accent-ink --accent-soft --warn --warn-soft --radius-pin --radius-sm --shadow --display --body` + a type scale + spacing scale. Dark values under `prefers-color-scheme: dark` **and** `[data-theme="dark"]`; a theme toggle in the avatar menu.
- **The AG Grid theme** reads the same tokens (`themeQuartz.withParams({...var(--…)})`), as `LedgerGrid.tsx` does today.
- **New components** (`src/ui/`): `Masonry` (CSS columns; keeps DOM order = reading order), `Pin` (image + overlay slots + caption), `Board` (2×2 collage cover), `CloseUp` (image/detail split → sheet on phones), `TopNav`, `TabBar`, `SearchBar` (search or copilot mode), `CreateMenu`, `Sheet`/`Drawer`/`Modal` (one accessible dialog primitive: focus trap, Esc, restore focus), `Skeleton`, `StatusPill` (replaces `Pill`, same status map), `MoneyRow`.
- **Layouts:** `PublicLayout` (S02–S05), `BrokerLayout` (phone-first, tab bar), `ManagerLayout` (top nav desktop, tab bar < 760 px).
- **No new runtime dependencies** besides what exists *(proposed)*. Masonry is CSS `columns`, not a JS library, so there is no layout shift and no bundle cost. Images use `loading="lazy"` with width/height to reserve space.

## 5. Phases

Each step ends with `npm run build` passing and a browser check (light and dark, 375 px and 1280 px), plus a short entry in `docs/BUILD_LOG.md`.

### Phase 0: Inspiration → tokens (S)
- Collect 8–15 Pinterest pins; paste them (or their URLs and a written description) into `<inspiration>` in the prompt.
- Output: `docs/frontend/design-tokens.md` (palette with contrast ratios, type scale, radius, shadow, motion), and `tokens.css`.
- Verified when: every text/background pair is at least 4.5:1 (3:1 for large text), in both themes.

### Phase 1: Shell and primitives (M)
- S00 `TopNav`, `TabBar`, `SearchBar`, `CreateMenu`, avatar menu; the dialog primitive; `StatusPill`, `Skeleton`, `Masonry`, `Pin`.
- Verified when: every route renders inside the right layout; Tab order and Esc work; no horizontal scroll at 375 px.

### Phase 2: Tenant (M)
- S03 close-up (carousel, move-in total, commute rows, broker card, share); S04 verify; S05 "coming soon" sheet.
- S02 behind a `VITE_EXPLORE` flag, waiting on §6.
- Verified when: a shared `/l/:id` link works signed out on a phone, and the verify page shows both states (tamper one in a scratch DB as `scripts/demo.py` does).

### Phase 3: Broker (M)
- S06 boards + "needs you now" masonry; S07; S08 with a live preview pin and "Apply" on suggestions; S09; S10 with "Lock for a tenant"; S11.
- Verified when: the Feature 1 story works by hand: dishonest ad → NEEDS FIX → apply suggestions → LIVE → share link.

### Phase 4: Manager (M)
- S12 flat pins + detail panel; S13 and the mandate form as modals; S14; S15 with the copilot in the nav search; S16 drawer; S17 modal; S18 with an evidence masonry and clickable citations.
- Verified when: the Feature 4 story works by hand: simulate token → close → invoice drawer ($70,000 + $35,000 − $15 = $104,985) → copilot question → simulate dispute → draft → approve.

### Phase 5: Polish and accessibility (S)
- S19 states everywhere; `prefers-reduced-motion`; alt text; focus-visible rings; tap targets ≥ 44 px; `lang`, titles per route.
- Verified when: a keyboard-only pass of both stories succeeds, and there are no console errors.

### Phase 6: Screenshots and README (S)
- Replace `docs/screenshots/*` and add Feature 4 shots.

## 6. Backend change needed (optional, S02 only)

`GET /public/listings?city=&max_rent=&furnishing=&cursor=` → LIVE listings only, each with: id, title, rent, furnishing, city, first owner photo URL and its width/height, the best passing commute claim, broker trust score. It must reuse the same "this listing's own signed audit" rule as `/public/listings/{id}`. Until it exists, S02 stays behind its flag. *(proposed: the backend is added by us, not the frontend developer)*

## 7. Decision log

| # | Decision | Proposed | Why |
|---|---|---|---|
| F1 | Inspiration vs wireframe | Inspiration = look; wireframe = structure | Two clear jobs, no arguments mid-build |
| F2 | Brand | Never Pinterest's logo, red, name or assets | It's inspiration, not imitation |
| F3 | Ledger UI | Stays a real AG Grid table, styled; not pins | Money needs columns; sponsor alignment |
| F4 | Masonry | CSS columns, no library | Zero dependencies, no layout shift, keeps DOM order |
| F5 | Search bar | One field: flat search (tenant) / ledger copilot (broker, manager) | Pinterest's central search; surfaces the AI |
| F6 | Explore feed | Built behind a flag until `GET /public/listings` exists | Never fake a feed |
| F7 | Accent colour | One "verified" accent from the palette, used only for trust/PASS/LIVE/PAID | Status must stay readable at a glance |
| F8 | Status shapes | Keep today's shape language (filled / outlined / dashed / ink) as well as colour | Colour-blind safe |
| F9 | Dependencies | None new | Small bundle, fewer risks before the deadline |

## 8. Always / never

- **Always:** use the existing API exactly as it is; show the backend's error text; money as strings formatted by `format.ts` (₹ Indian grouping, $ US grouping); keep every status, warning and "can't check" state; keep `FAKE-` / demo labels visible.
- **Never:** invent data, counts, photos or features (no "Save" heart without an API); hide that something couldn't be checked; let the copilot look like it can change things; break the `RequireRole` redirects; add a backend call that moves money from the UI without the existing confirm step.

## 9. Not yet specified

- Real photos for demo flats (today: owner photos uploaded by the manager; placeholders otherwise).
- Feature 2 (AI qualification) and Feature 3 (visit booking) screens beyond the S05 stub.
- Where the app is hosted (Render/Vercel) and `VITE_API_BASE`.

## Progress

| Phase | Status |
|---|---|
| 0 Tokens | done 2026-11-12: `docs/frontend/design-tokens.md`, `frontend/src/styles/tokens.css`, merged into `main` |
| 1 Shell | started 2026-11-12: tokens wired into the whole app (all screens incl. Features 2-3, AG Grid); shell components (TopNav, TabBar, dialogs, Masonry, Pin) not started |
| 2 Tenant | not started |
| 3 Broker | not started |
| 4 Manager | not started |
| 5 Polish | not started |
| 6 Screenshots | not started |

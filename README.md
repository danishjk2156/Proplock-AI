# PropLock AI: Listing Audit (F1), Visit Booking (F2), Visit Escrow (F3) and Invoice Ledger (F4)

PropLock checks a broker's rental ad against facts the broker can't change before it goes live.
It checks the owner's agreed terms, rush-hour routes, state deposit rules, and photos taken at the flat.
It then signs the result and publishes the verified listing as a **PayPal Catalog product**.

**Feature 4** closes the deal on that verified listing. When a tenant locks the flat, a **PayPal invoice** goes out for the verified deposit and brokerage, minus the visit token already captured. Every money event lands in an **append-only ledger** shown in an AG Grid cockpit that you can question in plain English. When a payer disputes, a copilot drafts a cited response from PropLock's own signed records, and a person approves it.
**Feature 2** turns a tenant's questions into a booked visit. A booking assistant answers only from the listing's signed audit and collects budget, move-in date and profile. The tenant then holds a slot for 15 minutes and authorises a small visit fee with PayPal. Brokers and managers watch it all on a live visit calendar.
**Feature 3** makes that fee a real **PayPal Orders v2 authorization** (held, not charged) and referees the visit at the door. The tenant shows a 4-digit PIN that the broker types in, and both phones can check in by GPS. Fixed rules then decide, from that proof alone, whether PayPal **voids** the hold or **captures** it. A captured fee from a locked flat is the token Feature 4's invoice deducts.

Built for the PayPal AI Hackathon. Background: [docs/PropLock_Solution_Overview.pdf](docs/PropLock_Solution_Overview.pdf).
Step-by-step build notes: [docs/BUILD_LOG.md](docs/BUILD_LOG.md).

## Screenshots

| Broker home | Pre-check with suggested fixes | Audit result: needs fix |
|---|---|---|
| ![Broker home](docs/screenshots/broker-home.jpg) | ![Pre-check](docs/screenshots/broker-precheck.jpg) | ![Needs fix](docs/screenshots/broker-needs-fix.jpg) |

| Live listing | Tenant: verified listing | Tenant: is this genuine? |
|---|---|---|
| ![Live listing](docs/screenshots/broker-live.jpg) | ![Tenant listing](docs/screenshots/tenant-listing.jpg) | ![Verify](docs/screenshots/tenant-verify.jpg) |

**Agency manager (web, dark theme)**

![Manager: flats and mandates](docs/screenshots/manager-flats.jpg)

![Manager: review queue](docs/screenshots/manager-reviews.jpg)

## How an ad is judged

```text
broker submits ──► mandate gate (403 without one)
                    │
                    ▼
            claim extractor (Groq LLM reads the title + description)
                    │
     ┌──────────────┼──────────────┬──────────────┐      in parallel (LangGraph)
     ▼              ▼              ▼              ▼
 A · photos     B · commute    C · deposit    D · owner terms
 GPS ≤100 m,    rush-hour      state rules    rent range, deposit,
 ≤30 days,      route, ×1.2    with citation  furnishing, brokerage
 no reuse,      or +5 min
 same flat
     └──────────────┴──────┬───────┴──────────────┘
                           ▼
       any FAIL → NEEDS_FIX (evidence + suggested fix)
       any UNVERIFIABLE → IN_REVIEW (a manager decides)
       all PASS → signed (HMAC) → outbox → PayPal product → LIVE
```

"Couldn't check" (no key, no route data, an unsure vision model) is **never** turned into a pass or into a made-up number.

## Quick start (Windows, Python 3.11)

From `backend/`:

```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -e ".[dev]"
```
```bash
cp .env.example .env
```
Edit `.env`: set `GROQ_API_KEY`, `GROQ_TEXT_MODEL=openai/gpt-oss-120b`, `GROQ_VISION_MODEL=qwen/qwen3.8-27b`, and for a demo `DEMO_MODE=true`.
```bash
.venv/Scripts/alembic upgrade head
```
```bash
.venv/Scripts/python -m app.db.seed
```
This prints three API keys (manager, an honest broker, a dishonest broker) and saves them to `demo_keys.json`.
```bash
.venv/Scripts/python -m uvicorn app.main:app --reload
```
Open http://localhost:8000/docs, click **Authorize**, and paste a key.

Optional background worker (retries PayPal publishing, expires listings that weren't confirmed for 7 days):
```bash
.venv/Scripts/python -m app.services.outbox
```

### The web app (Node 20+)

With the backend running on port 8000, from `frontend/`:

```bash
npm install
```
```bash
npm run dev
```
Open http://localhost:5173 and paste a key from `demo_keys.json`. A broker key opens the broker app; the manager key opens the agency desk. Vite forwards `/api/*` to the backend, so no CORS setup is needed in development. A deployed build sets `VITE_API_BASE` to the API's URL, and the backend's `CORS_ORIGINS` must list the site's origin.

| Who | Screens |
|---|---|
| Broker (phone-first) | My listings · check-in photo (GPS distance to the flat) · write the ad with pre-check · audit result with fixes · live listing (share, "still available", rented) |
| Manager (web) | Flats & mandates (register a flat, owner photos, give or revoke mandates) · review queue (approve or send back, with a note) |
| Tenant (no login) | `/l/{listing id}`: the verified listing and move-in total · `/v/{audit id}`: is this genuine? |

## The whole story in one command

```bash
.venv/Scripts/python -m scripts.demo
```

On a throwaway database, the demo runs these steps and prints each result:
1. A dishonest ad gets **NEEDS_FIX** with evidence.
2. The pre-check suggests the corrected wording.
3. The resubmitted ad is **VERIFIED → LIVE** as a PayPal product.
4. The public page is shown.
5. Someone edits the database, and `/verify` catches it twice.

The script exits non-zero if any step behaves differently. Add `--offline` to skip Groq.

## Feature 2: from question to booked visit

```text
tenant opens /l/{id} → "Book a visit" (/l/{id}/visit)
        │
        ▼
booking assistant (Groq) ── answers ONLY from the signed audit; refuses discounts, off-listing
        │                    facts and prompt injection; collects budget, move-in, profile, time
        ▼
our rules re-check "ready": budget ≥ verified rent, move-in not past, time within 3 days
        │ (the model can only be overruled to "not ready", never the other way)
        ▼
open slots: 1 h, 09:00–18:00 IST, next 3 days (PayPal honours an authorization for 3 days),
            minus the broker's visits and a 30-min travel gap around visits at other flats
        │ fee: base $15, −20% if ≥ 48 h ahead, +30% if < 12 h ahead
        ▼
SOFT_HOLD (15 min; the broker's row is locked while the calendar is checked, so two
           tenants can never take one slot)  ──► EXPIRED (sweep) / RELEASED (tenant)
        │ authorise
        ▼
PayPal JS SDK button → Orders v2 (intent=AUTHORIZE) → authorize → COMMITTED + HOLD_AUTHORIZED
        (approved after the soft hold ran out → voided at once)            ──► Feature 3 below
```

If Groq isn't configured or fails, the page says the assistant is offline and still lets the tenant pick a time; it never shows a made-up answer.

| Who | Screens |
|---|---|
| Tenant (no login) | `/l/{id}/visit`: chat, "before you book" checklist, slot picker with fees, hold countdown, authorise |
| Broker | **My visits** (`/b/visits`, the calendar icon on the home screen): own visits, live |
| Manager | **Visit calendar** (`/m/visits`): every broker's lanes with travel gaps, outcomes and the referee's reasons |

| API | |
|---|---|
| Public | `POST /public/listings/{id}/assistant` · `GET /public/listings/{id}/slots` · `POST /public/listings/{id}/visits` (returns a one-time hold token) · `GET /public/visits/{id}` · `POST /public/visits/{id}/authorize` · `POST /public/visits/{id}/release` (all three with `X-Hold-Token`) |
| Broker / manager | `GET /visits` |

## Feature 3: the visit fee, refereed at the door

```text
booked visit (fee authorised, ~3-day PayPal honor period) ── tenant sees a 4-digit PIN
        │
        ▼  proof, append-only (visit_checkins): the broker types the PIN · GPS from either phone
        │  (≤150 m from the flat's verified pin, 30 min either side of the slot) · tenant photo
        │  taps: tenant Pass / Lock / "broker didn't come" · broker approve lock / claim no-show
        ▼
referee (pure rules, no LLM) re-derives the verdict from the rows on every tap:
   A visited + Pass, or nobody locks by the deadline ...................... void
   B visited + Lock + broker approves ...................................... capture → Feature 4 token
   C broker's GPS at the flat, no tenant proof, 30 min after start, claimed . capture (no-show fee)
   D tenant GPS + live photo, no broker proof or PIN, 30 min after start ... void, listing off, trust −10
   - nothing proved by the deadline (end + 12 h, inside the honor period) .. void
        │  a correct PIN makes both no-show claims impossible
        ▼
visit SETTLING + one outbox job (PayPal-Request-Id from the visit id) ──► capture or void ──► CAPTURED / VOIDED
ledger HOLD_CAPTURED / HOLD_VOIDED (same keys as the webhooks, so each counts once)
webhooks PAYMENT.CAPTURE.COMPLETED / DENIED, PAYMENT.AUTHORIZATION.VOIDED confirm or correct; they never decide
```

The PIN is never stored: it is derived from the tenant's hold token, and the server keeps only an HMAC to check what the broker types (5 tries). The worker settles visits past their deadline. A no-show fee compensates the broker and is never offered as a closing token.

| Who | Screens |
|---|---|
| Tenant | `/l/{id}/visit` after booking: PayPal button (a plain demo button without keys), door PIN, "I'm at the flat", Not for me / Lock, "the broker didn't come" with a camera photo. The visit survives a reload (hold token in local storage). |
| Broker | **At the door** (`/b/visits/{id}`, from My visits): PIN pad, "I'm here", approve the lock, claim a no-show |
| Manager | Visit calendar: outcome badges and reasons, "Settle now" (the referee's default, applied now) |

| API | |
|---|---|
| Tenant (`X-Hold-Token`) | `GET /public/payments/config` · `POST /public/visits/{id}/paypal-order` · `POST …/authorize` · `POST …/checkin` · `POST …/photo` · `POST …/decision` (`PASS` / `LOCK` / `BROKER_ABSENT`) |
| Broker | `POST /broker/visits/{id}/pin` · `…/checkin` · `…/approve-lock` · `…/claim-no-show` |
| Manager | `POST /manager/visits/{id}/settle-now` |
| PayPal | `POST /webhooks/paypal` (capture completed / denied, authorization voided, order approved) |

```bash
.venv/Scripts/python -m scripts.demo_feature3
```
On a throwaway database, five tenants book visits and each ends a different way (A–D and no proof), with demo PayPal ids; it exits non-zero if any step behaves differently.

Spike: `python -m scripts.spike_orders` creates two sandbox AUTHORIZE orders and waits while you approve them with a **sandbox buyer** in your browser, then authorizes, captures one and voids the other (`--create-only` checks only the keys and the order shape).

## Feature 4: from locked flat to paid invoice

```text
Feature 3 capture of a locked visit's fee (PayPal Orders; DemoOrders FAKE- ids in DEMO_MODE)
        │ HOLD_CAPTURED $15
        ▼
close the LIVE listing ──► invoice = signed deposit + signed brokerage − token
        │                  (other amounts: manager override + note, recorded)
        │  one commit: closing + signed invoice + ledger entry + outbox job
        ▼
outbox ──► PayPal: create → send → reminders (due−1, +1, +3; none once paid)
        ▼
verified webhook / "Check PayPal now" ──► INVOICE_PAID, once ──► closing PAID
        ▼
dispute webhook ──► evidence pack (audit, check-in, invoice, ledger, trust; each with a source id)
                ──► cited draft (Groq, else a template) ──► manager approves the text ──► PayPal
```

The rules: the ledger is append-only (the ORM refuses updates and deletes); balances are summed from entries, never stored; every PayPal call goes through the outbox with a fixed `PayPal-Request-Id`; an unverified webhook changes nothing; the LLM only writes query objects and drafts, never a money-moving call.

```bash
.venv/Scripts/python -m scripts.demo_feature4
```
Runs the whole story on a throwaway database, using demo PayPal invoicing (`FAKE-INV-` ids) and webhooks signed by the script. Add `--offline` to skip Groq.

| Who | Screens |
|---|---|
| Manager | **Money ledger** (`/m/ledger`): KPIs, AG Grid of closings and money events, "Ask the ledger", invoice drawer (breakdown, pay link, QR, timeline, cancel), close a flat with an optional override · **Disputes** (`/m/disputes`): evidence pack, draft, approve |
| Broker | **My money ledger** (`/b/ledger`, the ledger icon on the home screen): own closings only; close a flat with a captured token |

| API | |
|---|---|
| Broker | `POST /broker/listings/{id}/close` |
| Manager | `POST /manager/listings/{id}/close` (override) · `POST /closings/{id}/cancel` · `GET /manager/disputes` · `POST /manager/disputes/{id}/draft` · `POST …/approve` |
| Both | `GET /ledger` · `GET /ledger/closable` · `GET /closings/{id}` · `POST /closings/{id}/refresh` · `GET /closings/{id}/qr` · `POST /ledger/copilot` |
| PayPal | `POST /webhooks/paypal` |
| Demo only | `POST /dev/ledger/simulate-capture` · `POST /dev/disputes/simulate` |

Spikes: `python -m scripts.spike_invoicing` (real sandbox invoice) and `python -m scripts.spike_ledger_copilot` (10 questions through Groq).

## Configuration

| Variable | Needed for | Without it |
|---|---|---|
| `GROQ_API_KEY`, `GROQ_TEXT_MODEL` | reading claims in ad text; the Feature 2 booking assistant | claims check UNVERIFIABLE → review; the assistant says it is offline and the tenant picks a time directly |
| `GROQ_VISION_MODEL` | comparing extra photos with the owner's | extra photos UNVERIFIABLE → review |
| `MAPS_PROVIDER=osm` | real routes from OpenStreetMap: free, no key or card | — (default `auto`: Google if keyed, else the demo table in `DEMO_MODE`) |
| `GOOGLE_MAPS_API_KEY` | real rush-hour routes with live traffic (Routes API, needs billing) | `DEMO_MODE` route table, else UNVERIFIABLE |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` | sandbox Catalog products | `DEMO_MODE` `FAKE-` ids, else the listing waits as VERIFIED |
| `DEMO_MODE` | demo route table and fake PayPal ids; Feature 4's simulated visit token and demo-signed webhooks | off: missing services are UNVERIFIABLE |
| `PAYPAL_WEBHOOK_ID` | trusting PayPal webhooks (verified with PayPal) | every webhook is rejected (in `DEMO_MODE`, only script-signed ones pass) |
| `INVOICE_CURRENCY`, `INVOICE_DUE_DAYS`, `INVOICE_REMINDER_OFFSETS` | Feature 4 invoices | `USD`, 7 days, `-1,1,3` |
| `VISIT_FEE_BASE`, `VISIT_WINDOW_DAYS`, `VISIT_SOFT_HOLD_MINUTES`, `VISIT_TRANSIT_BUFFER_MINUTES`, `VISIT_HOURS`, `VISIT_UTC_OFFSET_MINUTES` | Feature 2 visit booking | $15 fee (−20% ≥ 48 h ahead, +30% < 12 h), 3 days, 15 min, 30 min, 9-18, 330 (IST) |
| `AUDIT_SECRET` | signing audits | dev default (refused when `APP_ENV=production`) |
| `DATABASE_URL`, `UPLOAD_DIR` | storage | `sqlite:///./proplock.db`, `uploads/` |

## API by role

| Role | Endpoints |
|---|---|
| Manager | `POST/GET /manager/properties` · `POST /manager/properties/{id}/photos` (owner photos) · `GET /manager/brokers` · `POST/GET /manager/mandates` · `POST /manager/mandates/{id}/revoke` · `GET /manager/reviews` · `POST /manager/reviews/{audit_id}` · `POST /manager/outbox/run` |
| Broker | `POST /broker/listings` · `POST/GET /broker/listings/{id}/photos` · `DELETE …/photos/{photo_id}` · `POST /broker/precheck` · `POST /broker/listings/{id}/submit` · `POST …/confirm-availability` · `POST …/mark-rented` |
| Both | `GET /me` · `GET /audits/{id}` (brokers see only their own) |
| Public | `GET /public/listings/{id}` · `GET /public/photos/{id}` · `GET /public/audits/{id}/verify` |

Every request carries an `X-API-Key` header except `/public/*` and `/health`.

## Project layout

```text
backend/
  app/
    domain/     pure rules: ground truth (D), commute (B), deposits (C), photos (A), decision, signing;
                visit_rules (Feature 2: fee, slots, clashes, hold status, the assistant's guardrail)
    adapters/   Groq (text + vision), Maps, PayPal, images; each with a Fake for tests
    agents/     LangGraph pipeline: extract → checks in parallel → decide; visit_agent (booking assistant)
    services/   audits, photos, outbox, listings (availability/expiry), visits, trust score, rate limit
    api/        manager, broker, audits, public, visits
    rules/      deposit_caps.yaml, commute_matrix.yaml (demo routes)
    db/         SQLAlchemy models, seed
  alembic/      migrations
  scripts/      demo, spike_groq, spike_paypal
  tests/        unit/ and api/; no test calls the network
frontend/      Vite + React + TypeScript single-page app
  src/pages/    broker/, manager/, public/ screens
  src/api.ts    fetch wrapper (sends X-API-Key, shows the backend's error messages)
```

## Tests

```bash
.venv/Scripts/python -m pytest -q
```
```bash
npm run build
```
(from `frontend/`: a strict TypeScript check plus a production build)

## Known limits

- **Not yet tested against the real services:** no Google Maps or PayPal sandbox call has been made (no keys). OpenStreetMap routing has been called for real.
- **OpenStreetMap has no live traffic or transit:** walking claims are judged fully; a driving claim that fits the empty-road time, or any transit claim, goes to a manager. Both adapters are tested against mock servers. Run `python -m scripts.spike_paypal` once PayPal keys exist.
- **The Tamil Nadu deposit cap is unverified:** it is labelled "not yet verified against the official text" until someone checks the gazette by hand. Karnataka's limits are PropLock policy, not law.
- **Check-in GPS can be faked:** it comes from the upload request (or the photo's EXIF data), so an API client can send any coordinates until the frontend has its own camera page.
- **Single-process limits:** the pre-check rate limit (10/min per broker) is kept in memory, and photo-reuse detection scans every photo. Both are fine for a demo, not for scale.
- **Sign-in is an API key:** the web app keeps it in the browser's local storage. Fine for a demo, but a real login would use a session cookie.
- **The frontend has no automated tests yet:** it was checked by hand in a browser (see the build log).
- **Feature 3 against real PayPal:** creating sandbox AUTHORIZE orders is verified (`spike_orders --create-only`, idempotent). Approve → authorize → capture/void needs a sandbox buyer, so run the full spike yourself; the adapter is tested against a mock server. The PayPal button uses the JS SDK with the sandbox client id.
- **Door GPS can be spoofed**, like check-in photo GPS. The PIN is the strong proof; GPS-only outcomes (no-shows) show the referee's reason to the manager.
- **A broker no-show takes the listing off the public site** (`EXPIRED`) and costs 10 trust points; the broker can re-confirm it. There is no separate manager review step for it yet.
- **No-show fees are not paid out** to brokers (PayPal Payouts is out of scope); they stay with the agency and are marked in the ledger.
- **Visit times are in one time zone** (`VISIT_UTC_OFFSET_MINUTES`, India by default), and the booking rate limits are kept in memory like the pre-check's.
- **Feature 4 against real PayPal:** Invoicing is verified in the sandbox (`scripts/spike_invoicing` passes). Webhooks need a public URL and `PAYPAL_WEBHOOK_ID`; until then, "Check PayPal now" reads the invoice status directly. Dispute evidence submission is tested only against a mock server. `scripts/demo_feature4` always uses `FAKE-INV-` invoices.
- **Invoices are in USD** in the sandbox (PayPal has no domestic INR between Indian accounts), using the ₹ figures verified in the listing.
- **AG Grid Community has no row grouping**, so closings and their money events are two views plus a detail drawer.

## Contributors

| Name | GitHub | Work |
|---|---|---|
| Muhammad Danish J | [@danishjk2156](https://github.com/danishjk2156) | Backend, Features 1–4, original web app |
| Natasha P A | [@natashapa07](https://github.com/natashapa07) | Frontend redesign (design tokens, app shell, screens) |

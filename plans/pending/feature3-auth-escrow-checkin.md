# Feature 3: PayPal Auth-Escrow & Check-In Referee

Source of truth: `docs/PropLock_Solution_Overview.pdf` §3.1 (Feature 3) and §6.1 "The four ways a visit can end" (the anti-cheat matrix).
Builds on: Feature 2 (visit booking, uncommitted on `feature/4-invoice-ledger`) and Feature 4 (ledger, closing invoice).

Status: **BUILT 2026-11-12** (full plan). D1–D12 taken as written, with these answers to §10: (1) the full plan; (2) no-show fees stay with the agency, recorded in the ledger, no Payouts; (3) outcome D requires the live photo; (4) Lock doesn't reserve the listing (Feature 4's closing does); (5) built on the same branch as Feature 2, uncommitted.

Changes from the plan: the PIN is derived from the tenant's hold token instead of being generated and stored (only an HMAC is kept); a broker no-show sets the listing `EXPIRED` (the review queue is audit-based, so `IN_REVIEW` would have no way back); no-show captures are never offered as closing tokens. Phase 0 ran only `--create-only` (approval needs a sandbox buyer).

> **Deadline:** submissions closed on **12 November 2026**. If anything is still being judged, §9 "Hackathon cut" is the smallest slice that demos the referee for real (about a day). Everything else is roadmap.

---

## 1. What we are building (one paragraph)

Feature 2 ends when a tenant has held a slot and "authorised" a $15 visit fee. Today that authorization is simulated (`FAKE-AUTH-`), and a manager button pretends the fee was captured. Feature 3 makes both steps real. **The authorization comes from PayPal Orders v2 (`intent=AUTHORIZE`)**, approved by the tenant with the PayPal JS SDK. **At the door, PropLock referees the visit**: the tenant gets a 4-digit PIN that the broker types in on arrival, and both phones can record GPS at the flat. A pure rules engine then decides, from that proof alone, whether PayPal **voids** the hold (the tenant pays nothing) or **captures** it. A capture happens when the tenant wants the flat, or when the tenant didn't show and the broker proves they were there. Every capture and void goes through the outbox and lands in the ledger. A capture becomes the token Feature 4's invoice deducts. Neither side can move the money on their own word.

## 2. The four outcomes (from §6.1, made exact)

| # | What happened | Proof the engine needs | PayPal | Side effects |
|---|---|---|---|---|
| A | Tenant visited, passes | Correct PIN entered by the broker during the slot, **or** tenant GPS at the flat during the slot; then tenant taps **Pass** (or nobody taps Lock before the settle deadline) | **void** | Slot freed; no fee |
| B | Tenant wants the flat | Check-in as in A, **and** tenant taps **Lock**, **and** broker approves | **capture** | Listing stays LIVE until Feature 4 closes it; close form pre-filled (already built) |
| C | Tenant didn't show | Broker GPS at the flat during the slot, **no** tenant check-in, and the slot start + 30 min grace has passed; broker taps **Claim no-show** | **capture** | Broker compensated (see D9) |
| D | Broker didn't show, or fake flat | Tenant GPS at the flat during the slot + a live geotagged photo, **no** broker check-in or PIN by start + 30 min; tenant taps **Broker didn't come** | **void** | Listing → `IN_REVIEW` for a manager, broker trust −10 |
| — | No proof from anyone | Nothing recorded by the settle deadline | **void** | The safe default: money goes back to the tenant |

Rules that make cheating useless:
- Once a valid PIN is entered, **C is impossible**: the tenant was provably there.
- C needs the **broker's own GPS at the flat**: a broker can't claim a no-show from home.
- D releases the money **without the broker's permission**.
- B needs **both** parties: a broker can't capture by tapping Lock for the tenant.
- The engine only reads stored proof. Taps are requests; the engine decides.

## 3. Target architecture

```text
 Feature 2: visit SOFT_HOLD ──(tenant clicks PayPal button)──► Orders v2 create (intent=AUTHORIZE)
                                                                   │ tenant approves in PayPal popup
                                                                   ▼
                                            POST /v2/checkout/orders/{id}/authorize
                                                   → authorization_id, honor period ~3 days
                                                                   │
                                  visit COMMITTED ── ledger HOLD_AUTHORIZED (real id)
                                                                   │
            ┌──────────── check-in evidence (append-only rows) ────┴──────────────┐
            │ PIN entered by broker · broker GPS · tenant GPS · tenant photo        │
            │ tenant: Pass / Lock / Broker didn't come · broker: approve / no-show │
            └──────────────────────────────────┬───────────────────────────────────┘
                                               ▼
                          referee (pure): evidence + clock → Outcome or "wait"
                                               │ decided
                                               ▼
                    visit SETTLING ── outbox job capture_hold / void_hold (PayPal-Request-Id = visit id)
                                               │
                     PayPal /v2/payments/authorizations/{id}/capture | /void
                                               │
             ledger HOLD_CAPTURED / HOLD_VOIDED ── visit CAPTURED / VOIDED
                                               │
       webhooks PAYMENT.CAPTURE.COMPLETED / PAYMENT.AUTHORIZATION.VOIDED → reconcile (idempotent)
                                               │
                         Feature 4: capture_ref → closing invoice deducts the token
```

## 4. Decisions (proposed)

- **D1. Orders v2, server-side.** The server creates the order, the browser only renders PayPal's button and returns the approved `orderID`, and the server calls `authorize`. The client never sends an amount: the fee comes from the visit row. `PayPal-Request-Id` = `visit:{id}:create` / `visit:{id}:authorize`, so retries are safe.
- **D2. Currency USD** in the sandbox, like Feature 4 (no domestic INR between Indian accounts). The UI keeps showing $ for the fee.
- **D3. The 15-minute soft hold must cover the PayPal popup.** The order is created when the tenant clicks the button. If `authorize` arrives after `expires_at`, the server still authorizes, then **voids it straight away** and tells the tenant the time ran out. That way we never hold money for a slot we gave away.
- **D4. Booking window stays 3 days** (Feature 2's `VISIT_WINDOW_DAYS`) so every capture happens inside PayPal's honor period, with no reauthorization. The settle deadline is `ends_at + 12 h`, capped at `authorized_at + 70 h`. *Verify the honor period and validity in the Phase 0 spike.*
- **D5. PIN:** 4 random digits per visit, generated at COMMITTED. It's shown only to the tenant (with their hold token) and stored as an HMAC with the audit secret, never in plain text. The broker enters it at the door. 5 wrong tries locks PIN entry for that visit and leaves GPS as the only proof.
- **D6. GPS proof:** a check-in counts when it is within **150 m** of the property's verified coordinates (reusing `images.haversine_m`), during `starts_at − 30 min … ends_at + 30 min`, with reported accuracy ≤ 100 m. Like Feature 1's check-in photos it can be spoofed (known limit); the PIN is the strong proof.
- **D7. Evidence is append-only** (`visit_checkins`), the same idea as the ledger: nothing is edited, and the referee re-derives the outcome from the rows every time.
- **D8. The referee is pure and deterministic** (`app/domain/referee.py`): `decide(visit, evidence, now) -> Decision(outcome | WAIT, reason, cites=[evidence ids])`. No LLM anywhere in the money path.
- **D9. Who gets a captured no-show fee:** the merchant account is the agency's. Feature 3 records `beneficiary=broker` on the ledger note, but paying brokers out (PayPal Payouts) is **out of scope**.
- **D10. Every PayPal call goes through the outbox** (new kinds `authorize_hold` retry, `capture_hold`, `void_hold`), committed together with the visit's `SETTLING` status. A capture after a successful void (or the reverse) is impossible: the state machine allows one settlement only.
- **D11. Webhooks reconcile, they don't decide.** `PAYMENT.CAPTURE.COMPLETED`, `PAYMENT.CAPTURE.DENIED`, `PAYMENT.AUTHORIZATION.VOIDED`, `CHECKOUT.ORDER.APPROVED` go through the existing verified `webhook_service`. They confirm or correct what the outbox did, idempotently by PayPal id.
- **D12. Demo mode keeps working without PayPal:** a `DemoOrders` adapter (like `DemoInvoicing`) returns `FAKE-ORDER-` / `FAKE-AUTH-` / `FAKE-CAP-` ids. Once this lands, the demo-only shortcuts are removed (see §8).

## 5. Data model

`visits` (extend; one migration):

| Column | Type | Note |
|---|---|---|
| `paypal_order_id` | str(100) unique, null | set when the button is clicked |
| `authorized_at` | datetime, null | |
| `pin_hash` | str(64), null | HMAC of the PIN (D5) |
| `pin_attempts` | int, default 0 | |
| `outcome` | enum `PASSED / LOCKED / TENANT_NO_SHOW / BROKER_NO_SHOW / NO_PROOF`, null | |
| `outcome_reason` | text, null | the referee's sentence, shown to both sides |
| `settled_at` | datetime, null | |

`VisitStatus` gains `SETTLING`, `CAPTURED`, `VOIDED`. Transitions (extends `visit_rules.TRANSITIONS`):
`SOFT_HOLD → COMMITTED | EXPIRED | RELEASED` (unchanged) · `COMMITTED → SETTLING` · `SETTLING → CAPTURED | VOIDED` · the rest are terminal.

`visit_checkins` (new, append-only, ORM refuses update/delete like `LedgerEntry`):
`id, visit_id, party (TENANT|BROKER), kind (PIN_OK|PIN_BAD|GPS|PHOTO|PASS|LOCK|APPROVE_LOCK|CLAIM_NO_SHOW|BROKER_ABSENT), latitude, longitude, accuracy_m, distance_m, photo_id (listing_photos), created_at`.

Postgres: keep Feature 2's exclusion constraint. Add `CAPTURED` to its `WHERE` so a captured visit's slot also stays blocked.

## 6. API

| Who | Endpoint | Does |
|---|---|---|
| Tenant (`X-Hold-Token`) | `POST /public/visits/{id}/paypal-order` | create the AUTHORIZE order → `{order_id}` for the JS SDK |
| Tenant | `POST /public/visits/{id}/authorize` | *changes:* body `{order_id}`; calls PayPal authorize (replaces the demo shortcut) |
| Tenant | `GET /public/visits/{id}` | *adds* PIN (once COMMITTED), check-in state, outcome |
| Tenant | `POST /public/visits/{id}/checkin` | GPS `{lat, lon, accuracy}` (+ optional photo upload) |
| Tenant | `POST /public/visits/{id}/decision` | `{choice: PASS \| LOCK \| BROKER_ABSENT}` |
| Broker | `POST /broker/visits/{id}/pin` | `{pin}` → ok / wrong (attempts left) / locked |
| Broker | `POST /broker/visits/{id}/checkin` | GPS |
| Broker | `POST /broker/visits/{id}/approve-lock` · `POST /broker/visits/{id}/claim-no-show` | |
| Both staff | `GET /visits` | *adds* evidence summary, outcome, settle deadline |
| Manager | `POST /manager/visits/{id}/settle-now` | runs the referee immediately (demo/ops) |
| PayPal | `POST /webhooks/paypal` | new handlers (D11) |

Every tap endpoint records evidence, then runs the referee. If it decides, the visit goes to `SETTLING` and the outbox job is queued in the same commit. Then `try_ref_now` runs, as Feature 4 does.

## 7. Phases (each ends green: tests + build)

**Phase 0: Sandbox spike** (`scripts/spike_orders.py`, like `spike_invoicing`)
Create an AUTHORIZE order, approve it with a sandbox buyer, authorize, capture one, void another, and read both back. Record the real honor period/validity and the webhook event names. *Acceptance:* the script passes against the sandbox; the facts in D4/D11 are confirmed or corrected here.

**Phase 1: Referee rules (pure)**
`app/domain/referee.py` + `tests/unit/test_referee.py`: every row of §2, plus the edges: PIN after the window, GPS 151 m away, broker claims no-show at minute 29, tenant Lock without broker approval, both no-show claims, evidence after settlement (ignored), settle deadline reached with partial proof. *Acceptance:* about 30 table-driven cases, no DB.

**Phase 2: Model + migration**
Columns, `visit_checkins` with the append-only guard, extended transitions, Postgres constraint update. *Acceptance:* migration up/down test; state-machine unit tests extended.

**Phase 3: Orders adapter**
`app/adapters/paypal_orders.py`: `Orders` protocol (`create_authorize_order`, `authorize`, `capture`, `void`, `get_authorization`), `PayPalOrders` (reuses `PayPalCatalog`'s OAuth + `_fail`), `FakeOrders` (fail_times), `DemoOrders`, `get_orders()`. *Acceptance:* unit tests against a mock HTTP server (pattern from `test_paypal_invoicing`), request ids asserted.

**Phase 4: Real authorization**
`paypal-order` + new `authorize` path; D3 late-approval void; ledger `HOLD_AUTHORIZED` with the real id. *Acceptance:* API tests with `FakeOrders`: happy path, double authorize = one PayPal call, approval after expiry → voided, client-sent amounts ignored.

**Phase 5: Check-in & PIN**
PIN generation at COMMITTED, broker PIN entry with lockout, GPS check-ins for both, tenant photo (reuse `photo_service` upload + EXIF/GPS). *Acceptance:* API tests for every evidence kind, rejection outside window/radius, the PIN never in any broker response.

**Phase 6: Settlement**
Tap endpoints → referee → `SETTLING` + outbox `capture_hold`/`void_hold` (handlers in a new `services/escrow_jobs.py`, registered in `outbox._handlers`). Worker sweep: settle visits past the deadline (NO_PROOF → void). Broker-no-show → listing `IN_REVIEW` + trust event `broker_no_show (−10)`. *Acceptance:* PayPal down → job retries, nothing settles twice; capture → Feature 4 `/ledger/closable` shows it pre-filled (existing test extended).

**Phase 7: Webhooks**
Handlers for D11 in `webhook_service.HANDLERS`. *Acceptance:* forged event refused; replay = duplicate; a void webhook for an already-voided visit changes nothing; a `CAPTURE.DENIED` marks the visit for manager attention.

**Phase 8: Frontend**
- *Tenant* `/l/{id}/visit`: PayPal JS SDK buttons replace "Authorise" (sandbox client id from a new `GET /public/config`); after booking: PIN card, "I'm at the flat" (geolocation), Pass / Lock, and "Broker didn't come" (camera photo) after the grace period. The hold token is kept in `localStorage` per visit so the page survives a reload (wrapped in try/catch like `api.ts`).
- *Broker* `/b/visits/{id}` (phone): "I'm here" GPS, PIN pad, approve Lock, Claim no-show (enabled only after grace + own GPS).
- *Manager calendar*: new statuses and outcome badges, evidence timeline per visit, "Settle now".
*Acceptance:* `npm run build`; full click-through in the browser for outcomes A–D with the demo adapter.

**Phase 9: Demo + docs**
`scripts/demo_feature3.py` runs all four outcomes on a throwaway DB (DemoOrders, clock injected). `demo_feature4` step 2 uses the referee instead of `simulate-capture`. README section, build log, this plan's status.

## 8. What gets removed once Feature 3 lands (no duplicates)

- `POST /dev/ledger/simulate-capture`'s `visit_id` branch → replaced by real settlement (DemoOrders in demo mode). Its `listing_id` branch stays only as a test helper, or moves into `tests/f4_helpers.py`.
- Feature 2's `FAKE-AUTH-` shortcut inside `visit_service.authorize` → replaced by the Orders adapter.
- The manager calendar's "Tenant visited · capture fee (demo)" button → replaced by the broker's real check-in flow (and "Settle now").
- README "Feature 3 is not built" limit and the ledger docstring note.

## 9. Hackathon cut (≈1 day)

Phases 1, 2, 3 (Fake + Demo only), 5 (PIN + GPS, no photo), 6 (no worker sweep: "Settle now" instead), and a minimal Phase 8 (PIN card + Pass/Lock for the tenant; PIN pad + no-show for the broker). Skip the real sandbox spike, webhooks and outcome D's photo. It demos A, B and C end to end, with real rules and fake PayPal ids.

## 10. Open questions (need your answer before Phase 1)

1. **Full plan or the hackathon cut?**
2. **No-show money (D9):** keep it with the agency and just record it, or plan PayPal Payouts to the broker?
3. **Outcome D:** require the live photo, or accept tenant GPS alone (simpler, weaker)?
4. **Lock (B):** should a capture also mark the listing "reserved" so nobody else books a visit, or leave that to Feature 4's closing?
5. **Ship order:** commit Feature 2 first as its own PR, then branch `feature/3-auth-escrow` from it?

## 11. Risks and known limits

- **GPS can be spoofed** by a determined client, as with Feature 1 check-in photos. The PIN is the strong signal; GPS-only outcomes (C, D) are the weaker ones and are logged for manager review.
- **PayPal timing facts are from memory** until Phase 0: the honor period (~3 days), authorization validity (~29 days) and webhook names.
- **JS SDK in the sandbox** needs a sandbox buyer account, and the popup can be blocked; the page must explain that and keep the hold timer visible.
- **Clock-based rules need an injected `now`** everywhere (the services already take `now`); the worker sweep must be idempotent.
- **Single-process rate limits and worker** remain, as in Features 2 and 4.

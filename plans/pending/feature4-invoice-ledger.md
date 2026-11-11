# Feature 4: Token-to-Invoice Ledger & Dispute Defense Copilot

Source of truth: `docs/PropLock_Solution_Overview.pdf` §3.1 and the PropLock AI Product Blueprint, Feature 4 ("Closing & ledger").
Branch: `feature/4-invoice-ledger` (from `main` at `36b37ab`).
Implementation prompt for a coding agent: [`feature4-prompt.xml`](feature4-prompt.xml).

Status: **BUILT 2026-11-11.** Proposed decisions D1-D10 taken as written. All phases done; the real Invoicing spike passes against the sandbox (webhooks and dispute evidence not yet exercised for real).

> **Deadline:** hackathon submissions close **12 November 2026**. The full plan below is several days of work. §7 "Hackathon cut" lists the smallest slice that demos the idea in about a day; everything else is roadmap.

---

## 1. What we are building (one paragraph)

When a tenant locks a flat, the visit's ₹500 / $15 PayPal hold is captured (Feature 3). Feature 4 turns that moment into a **formal PayPal invoice** for the security deposit and brokerage, with **the captured token already deducted**, sent to the tenant with a payment link and QR code. Every money event (hold authorised, captured, voided, invoice drafted, sent, paid, reminded, cancelled, refunded, disputed) is written to one **append-only ledger**, which the broker and the agency see in an **AG Grid cockpit** and can **ask questions of in plain English** ("which deposits are unpaid after 3 days?"). Reminders go out on a schedule. When a payer opens a **PayPal dispute**, a copilot assembles an **evidence pack** from records PropLock already has (the signed listing audit, the door check-in proof, the invoice and the payment trail) and drafts a response that a person reviews and submits.

## 2. Problems it solves

| Today | With Feature 4 | Blueprint stage |
|---|---|---|
| Token, deposit and brokerage are collected by cash/UPI, tracked in notebooks and screenshots | One PayPal invoice per closing, every rupee in one ledger | 8 · Tracking |
| The tenant pays the token, then has to trust that it is credited | The token shows on the invoice as a deduction, so a serious tenant loses nothing | 5 · Commitment |
| Follow-ups are forgotten; warm leads go cold | Scheduled reminders through PayPal, visible in the ledger | 7 · Follow-up |
| Hidden charges surface at signing | The invoice can only contain the amounts verified in Feature 1 (deposit ≤ owner and state caps, brokerage ≤ cap) | 9 · Compliance |
| Chargebacks are one person's word against another's | An evidence pack from signed, timestamped records | 6 · Trust |

## 3. Target architecture

```text
 Feature 3 (visit escrow)            Feature 1 (verified listing)
 hold AUTHORIZED / CAPTURED / VOIDED      signed audit: rent, deposit, brokerage
            │                                         │
            ▼                                         ▼
   ┌────────────────── ledger (append-only) ──────────────────┐
   │ ledger_entries: kind, amount, currency, paypal_ref,       │
   │ idempotency_key UNIQUE, occurred_at, source, raw_event    │
   └───────────────┬──────────────────────┬───────────────────┘
                   │                      │
       closing service              PayPal webhooks  ◄── PayPal (sandbox)
   build invoice from the           verify signature,     INVOICING.INVOICE.PAID
   audit + captured token           dedupe by event id    CUSTOMER.DISPUTE.CREATED …
                   │                      │
                   ▼                      ▼
        outbox jobs (existing pattern: commit first, then call PayPal, retry safely)
        create_invoice → send_invoice → remind_invoice … provide_dispute_evidence
                   │
                   ▼
            PayPal Invoicing v2 · Customer Disputes v1
                   │
   ┌───────────────┴────────────────────────────────────────────┐
   │ API: /broker/closings, /broker/invoices, /ledger, /copilot  │
   │ Frontend: AG Grid ledger cockpit, invoice screen,           │
   │           dispute pack review, tenant invoice link          │
   └─────────────────────────────────────────────────────────────┘
        Copilot (LLM): NL question → structured, read-only ledger query → answer + rows
        Dispute copilot (LLM): evidence pack (facts from DB) → draft response → human submits
```

### Components

| Part | Responsibility | Files (proposed) |
|---|---|---|
| Ledger | Append-only record of every money event; balances per closing are *derived*, never stored | `app/domain/ledger.py`, `app/services/ledger_service.py` |
| Invoice rules | Build the invoice from the signed audit: deposit + brokerage − captured token; reject amounts above the verified caps | `app/domain/invoice_rules.py` |
| PayPal invoicing adapter | OAuth (reuse), `generate-next-invoice-number`, create draft, send, remind, cancel, get, QR code; Fake/Demo twins | `app/adapters/paypal_invoicing.py` |
| Webhooks | `POST /webhooks/paypal`: verify with PayPal's `verify-webhook-signature`, store raw event, dedupe by event id, map to ledger entries | `app/api/webhooks.py`, `app/services/webhook_service.py` |
| Reminders | Outbox jobs scheduled from the invoice due date; at most N reminders; stop when paid | extend `app/services/outbox.py` |
| Disputes | Ingest dispute webhooks; build an evidence pack; LLM drafts the narrative; a person approves; outbox submits evidence | `app/services/dispute_service.py`, `app/agents/dispute_copilot.py` |
| Ledger copilot | NL → a validated `LedgerQuery` (filters, sort, group); runs our own read-only query; never writes | `app/agents/ledger_copilot.py` |
| Frontend | AG Grid Community ledger with status renderers and an NL filter bar; invoice create/review; dispute pack review | `frontend/src/pages/ledger/*` |

### Data model (new tables)

| Table | Key columns | Notes |
|---|---|---|
| `closings` | id, listing_id, version_id, audit_id, tenant_name, tenant_email, status (`OPEN`, `INVOICED`, `PAID`, `CANCELLED`, `DISPUTED`) | One per locked flat; ties the money to the verified listing version |
| `ledger_entries` | id, closing_id, kind, amount (Numeric 12,2), currency, direction (`in`/`out`/`info`), paypal_ref, idempotency_key **UNIQUE**, occurred_at, source (`feature3`, `webhook`, `api`, `manual`), raw_json | Append-only: no UPDATE or DELETE in code; corrections are new entries |
| `invoices` | id, closing_id, paypal_invoice_id **UNIQUE**, number, items_json, deposit, brokerage, token_deduction, total, currency, status, due_date, sent_at, paid_at, reminders_sent, signature | Invoice body is canonical JSON, HMAC-signed like audits |
| `webhook_events` | paypal_event_id **PK**, event_type, verified, received_at, processed_at, error | Dedupe + replay safety |
| `disputes` | id, paypal_dispute_id **UNIQUE**, closing_id, reason, amount, status, evidence_pack_json, draft_response, approved_by, submitted_at | Draft is never sent without `approved_by` |

### Money rules

- `invoice.total = deposit + brokerage − token_captured` (never negative; if the token exceeds it, the excess is refunded as a separate ledger entry).
- `deposit` and `brokerage` **must equal the signed audit's** `terms.deposit` / `terms.brokerage_fee` for that version. Any other amount needs a manager override with a note, recorded in the ledger.
- The token is shown as an **invoice-level discount** labelled "Visit token already paid (PayPal capture {id})". *(To confirm in the sandbox spike: Invoicing v2 `amount.breakdown.discount.invoice_discount`.)*
- Currency: **USD in the sandbox** for the demo (PayPal does not support domestic INR between Indian accounts); the UI shows ₹ only as market framing. *(proposed, D3)*

### LLM boundaries (same principle as Feature 1: the AI reads, code decides)

- **Ledger copilot:** the model only turns a question into a `LedgerQuery` (Pydantic-validated: allowed fields, operators, ≤ 500 rows). Our code runs it. It has **no write tools**. An invalid query → "I couldn't turn that into a ledger search", never a guess.
- **Dispute copilot:** facts come from the DB; the model writes prose around them and must cite each fact's record id. A person approves before anything is sent to PayPal.
- **PayPal MCP server (`@paypal/agent-toolkit`):** optional, read-only (`list_invoices`, `get_invoice`) for the cockpit's "check live in PayPal" button. Money-moving tools are not exposed to the model. *(proposed, D6)*

---

## 4. Phases

Each step ends with passing tests and a `docs/BUILD_LOG.md` entry, as in Feature 1.

### Phase 0: Spike and contract
Verified when: a sandbox invoice with a discount line is created, sent and read back, and a webhook signature verifies.

#### Step 0.1: Invoicing spike (S)
- Acceptance: `scripts/spike_invoicing.py` creates a draft invoice (deposit + brokerage, invoice discount for the token), sends it, fetches it, generates a QR code, cancels it. Records the exact JSON shapes in BUILD_LOG.
- Verify: `python -m scripts.spike_invoicing` → exit 0 (exit 2 without keys).
- Blocked by: PayPal sandbox keys.

#### Step 0.2: Feature 3 contract (S)
- Acceptance: `LedgerEvent` schema for `HOLD_AUTHORIZED`, `HOLD_CAPTURED`, `HOLD_VOIDED`; a dev-only `POST /dev/ledger/simulate-capture` (manager role, `DEMO_MODE` only) so Feature 4 is testable before Feature 3 exists.
- Verify: `pytest tests/unit/test_ledger_contract.py`.

### Phase 1: Ledger
Verified when: entries are append-only, duplicates are rejected by idempotency key, and balances are derived correctly.

#### Step 1.1: Models + migration (M)
- Acceptance: the five tables above; Alembic up/down; enums with CHECK constraints; money as `Numeric(12,2)`.
- Verify: migration test + `tests/unit/test_ledger_models.py`.

#### Step 1.2: Ledger domain + service (S)
- Acceptance: `record(entry)` idempotent; `balance(closing)` = Σ in − Σ out; no update/delete paths; corrections via reversing entries.
- Verify: `tests/unit/test_ledger.py`.

### Phase 2: Closing → invoice
Verified when: locking a flat produces one PayPal invoice whose total is deposit + brokerage − token, sent once even if the worker runs twice.

#### Step 2.1: Invoice rules (S)
- Acceptance: build `InvoiceDraft` from the signed audit + captured token; refuse amounts that differ from the audit (unless a manager override); refuse a negative total.
- Verify: `tests/unit/test_invoice_rules.py`.

#### Step 2.2: PayPal invoicing adapter (M)
- Acceptance: create / send / remind / cancel / get / QR code with `PayPal-Request-Id`; errors raise `PayPalError` with `debug_id`; Fake and Demo twins (Demo ids prefixed `FAKE-INV-`).
- Verify: `tests/unit/test_paypal_invoicing.py` (mock HTTP server).

#### Step 2.3: Closing endpoint + outbox jobs (M)
- Acceptance: `POST /broker/listings/{id}/close {tenant_name, tenant_email, capture_ref}` → closing + ledger entry + `create_invoice` job in one commit; job chain create → send; invoice HMAC-signed.
- Verify: `tests/api/test_closing.py`.

### Phase 3: Webhooks
Verified when: a signed `INVOICING.INVOICE.PAID` webhook marks the invoice paid and the closing PAID, exactly once.

#### Step 3.1: Webhook endpoint (M)
- Acceptance: verify via `/v1/notifications/verify-webhook-signature` (`PAYPAL_WEBHOOK_ID` setting); unverified → 400 and stored with `verified=false`; dedupe by event id; handlers for invoice paid/cancelled/refunded and dispute created/updated/resolved.
- Verify: `tests/api/test_webhooks.py` with recorded payloads.

### Phase 4: Ledger cockpit
Verified when: the broker sees every closing and money event in AG Grid, filters it, and the totals match the ledger.

#### Step 4.1: Ledger API (S)
- Acceptance: `GET /ledger?filters…` (broker: own closings; manager: all), `GET /closings/{id}` with entries and invoice.
- Verify: `tests/api/test_ledger_api.py`.

#### Step 4.2: AG Grid cockpit (M)
- Acceptance: `ag-grid-react` (Community, MIT) with status cell renderers matching Feature 1 pills, amount columns in ₹/$, row grouping by closing, quick filter; invoice detail drawer with PayPal link and QR code; works at phone width (cards fallback).
- Verify: `npm run build`; manual browser check (light/dark, phone/desktop).

### Phase 5: Reminders
Verified when: an unpaid invoice gets reminders on schedule and none after it is paid.

#### Step 5.1: Reminder jobs (S)
- Acceptance: reminders at due − 1 day, due + 1, due + 3 (configurable); max 3; cancelled when paid; each reminder is a ledger `info` entry.
- Verify: `tests/unit/test_reminders.py` with a fake clock.

### Phase 6: Ledger copilot
Verified when: "unpaid deposits older than 3 days" returns the right rows; a write request is refused.

#### Step 6.1: NL → LedgerQuery (M)
- Acceptance: Groq JSON mode → `LedgerQuery` (Pydantic); one retry; refusal for out-of-scope or write intents; the answer quotes the rows it used.
- Verify: `tests/unit/test_ledger_copilot.py` (FakeLLM) + a real Groq spike for 10 sample questions.

### Phase 7: Dispute defense
Verified when: a dispute webhook creates a dispute with an evidence pack; the draft cites record ids; nothing is submitted without approval.

#### Step 7.1: Evidence pack (M)
- Acceptance: pack = signed audit (+ `/verify` result), check-in proof (Feature 3: PIN/GPS timestamps), invoice + payment trail, broker trust history; each item has a source id.
- Verify: `tests/unit/test_evidence_pack.py`.

#### Step 7.2: Draft, approve, submit (M)
- Acceptance: LLM drafts a response citing ids; manager edits/approves; outbox job calls Disputes v1 `provide-evidence`; status tracked from webhooks.
- Verify: `tests/api/test_disputes.py` (fake PayPal).

### Phase 8: Demo + docs
- Acceptance: `scripts/demo_feature4.py` runs: lock → invoice (token deducted) → sent → paid webhook → ledger shows PAID → a dispute → evidence pack → approved response. README section + screenshots.

---

## 5. Decision log

| # | Decision | Proposed | Why |
|---|---|---|---|
| D1 | Where money state lives | Append-only ledger; balances derived | Auditable; no silent edits; matches dispute needs |
| D2 | Invoice amounts | Must equal the signed Feature 1 audit; override needs a manager + note | Stops hidden charges at closing |
| D3 | Currency | USD in sandbox for the demo; ₹ shown as framing | PayPal has no domestic INR in India |
| D4 | Token on the invoice | Invoice-level discount with the capture id | Visible to the tenant; confirm shape in Step 0.1 |
| D5 | Feature 3 dependency | Contract + dev-only simulated capture until F3 ships | Feature 4 testable on its own |
| D6 | MCP server | Optional, read-only tools only | No model-initiated money movement |
| D7 | Copilot | NL → validated query object, never raw SQL, never writes | Same "AI reads, code decides" rule as Feature 1 |
| D8 | Webhook trust | Verify every event with PayPal; dedupe by event id | Forged or replayed webhooks can't mark invoices paid |
| D9 | Disputes | Human approval before any submission | Legal/financial weight; LLM drafts only |
| D10 | Grid | AG Grid Community (MIT) | Sponsor prize alignment; no licence key needed |

## 6. Always / Never

- **Always:** every PayPal call through the outbox with a fixed `PayPal-Request-Id`; every money event a ledger entry; tests never hit the network; amounts as `Decimal`.
- **Never:** update or delete a ledger entry; let the LLM call a money-moving tool; mark an invoice paid from an unverified webhook; invent a PayPal id outside `DEMO_MODE`.

## 7. Hackathon cut (about one day, for 12 November)

Do, in this order: **0.2 → 1.1 → 1.2 → 2.1 → 2.2 (Fake + Demo first, real if keys) → 2.3 → 4.1 → 4.2 (grid only)**.
That demos: simulated capture → invoice with the token deducted → ledger in AG Grid. Webhooks, reminders, the copilot and disputes go on the roadmap slide.

## 8. Not yet specified

- Feature 3's real capture payload (fields, ids): fog until Feature 3 is built.
- Tenant identity: email only, or a tenant account? *(proposed: email on the closing for now)*
- Refund flow when the token exceeds the amount due (rare): separate refund entry, details TBD.
- Hosting the webhook URL publicly for the sandbox (Render, or a tunnel for local dev).

## Progress

| Phase | Status |
|---|---|
| 0 Spike + contract | **done**: real sandbox spike passes (create, retry, read back total 1035.00 with discount -15.00, send, QR, cancel); LedgerEvent contract + DEMO_MODE simulate-capture |
| 1 Ledger | **done**: 5 tables + migration; append-only enforced by ORM listeners; idempotent record(); derived balances |
| 2 Closing → invoice | **done**: invoice from the signed audit minus the token, manager override with note, signed invoice, outbox create → send with fixed request ids |
| 3 Webhooks | **done**: verified via PayPal (demo HMAC in DEMO_MODE), raw events stored, deduped by event id and ledger key |
| 4 Cockpit | **done**: AG Grid Community, closings + money-event views, drawer, cards on phones, light/dark |
| 5 Reminders | **done**: due-1/+1/+3, max 3, none once paid |
| 6 Ledger copilot | **done**: Groq to LedgerQuery, write intents refused by rule and by the model; real spike 10/10 |
| 7 Dispute defense | **done**: evidence pack with source ids, citation-checked draft (template fallback), approval before submit |
| 8 Demo + docs | **done**: `scripts/demo_feature4.py` (online + `--offline`), README section, BUILD_LOG |

### Deviations from the plan

- **Invoice retries:** the real API does not replay a repeated `PayPal-Request-Id` for invoices; it returns `422 DUPLICATE_INVOICE_NUMBER`. `create_draft` then finds the invoice by its fixed number (`search-invoices`) and treats it as created.

- **Row grouping** is AG Grid Enterprise-only, so the cockpit shows closings and money events as two views plus a detail drawer.
- **Token label:** Invoicing v2's invoice discount has no label field, so the capture id is written in the invoice note.
- **Invoice number** is ours (`PL-xxxxxxxx`), not `generate-next-invoice-number`, so retries always send the same number.
- **POST /closings/{id}/refresh** was added: it reads the invoice from PayPal directly, so PAID can be shown without a public webhook URL (deduplicated with the webhook).
- **Disputes simulate** (`POST /dev/disputes/simulate`, DEMO_MODE) was added so the dispute screen can be demoed.

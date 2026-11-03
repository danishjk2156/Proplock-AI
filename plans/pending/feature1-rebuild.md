# Feature 1 rebuild: Multimodal Listing Audit & Commute-Truth Agent

Source of truth for the design: `docs/PropLock_Feature1_Review.pdf`, section 10 ("Target architecture") and section 11 ("Integrating the broker workflow").
Old implementation: deleted on 2026-10-28; backup at `D:\hackathon\_archive\proplock_feature1_old_2026-10-28.zip`.

Status: **APPROVED 2026-10-28, executing.** Gate 1 answered 2026-10-28 (keys, photos, frontend, pace).

---

## What we are building (one paragraph)

A FastAPI backend where a **manager** registers flats and gives **brokers** a mandate to list them. A broker submits a listing (title, description, rent, deposit, furnishing, brokerage fee, commute claims, a GPS check-in photo). An AI step extracts every claim from the ad text, then four checks run in parallel (A photos, B commute, C deposit law, D owner ground truth). Each check returns PASS, FAIL or UNVERIFIABLE with evidence. Any FAIL gives NEEDS_FIX, any UNVERIFIABLE gives IN_REVIEW for a manager, all PASS gives a signed audit (HMAC) that is saved first and then minted as a PayPal Catalog product by an outbox worker. A public endpoint shows the verified listing and can re-verify the signature at any time.

## Target layout

```text
paypal/
  backend/
    pyproject.toml            # pinned deps, pytest config
    .env.example
    app/
      main.py                 # FastAPI app, routers, lifespan
      config.py               # Settings (pydantic-settings), all keys + DEMO_MODE
      db/
        base.py               # engine, session, Base
        models.py             # ORM tables
        seed.py               # demo owners, flats, brokers, mandates
      auth.py                 # API-key auth + role dependency
      schemas/                # Pydantic request/response models
        listing.py  audit.py  common.py
      domain/                 # pure logic, no I/O (easy to unit test)
        check_result.py       # CheckResult(verdict, evidence, source)
        ground_truth.py       # Check D
        commute_rules.py      # tolerance maths
        compliance_rules.py   # rule lookup + verdict
        decision.py           # PASS/FAIL/UNVERIFIABLE -> status
        signing.py            # canonical JSON + HMAC
      adapters/               # everything that talks to the outside world
        llm.py                # Groq (real) + FakeLLM (tests)
        maps.py               # Google Routes (real) + table (demo) + Fake
        paypal.py             # PayPal Catalog (real) + Fake
        images.py             # pHash, EXIF/GPS helpers
      agents/
        graph.py              # LangGraph: extract -> fan-out A-D -> decide
        nodes.py              # one async node per check
      services/
        audit_service.py      # submit -> run graph -> persist -> enqueue mint
        outbox.py             # mint worker with retries
      api/
        manager.py            # properties, mandates, reviews
        broker.py             # drafts, precheck, submit, confirm-availability
        public.py             # listing page data, verify signature
      rules/
        deposit_caps.yaml     # versioned rules with source + effective date
        commute_matrix.yaml   # demo routes when no Maps key
    tests/
      unit/  api/  e2e/
  plans/pending/feature1-rebuild.md   # this file
  docs/BUILD_LOG.md                   # "what happened technically" per step
```

Why this shape: `domain/` is pure Python (no network, no DB), so every rule is unit-testable in milliseconds. `adapters/` hides each external service behind a small interface with a **Fake** twin, so tests never call Groq, Google or PayPal, and the real ones switch on only when a key is present. This is what fixes the old "silent fallback" bugs (M3, M5, D1): a missing key gives UNVERIFIABLE, never a guess.

---

## Phases

Each step ends with a short explainer in chat **and** an entry in `docs/BUILD_LOG.md`: what changed, why, the key concept, and how to see it working.

### Phase 0: Foundations and risk spikes
Verified when: `pytest` runs green on an empty suite, `GET /health` returns 200, and each external key you provide is proven with one real call.

#### Step 0.1: Repo, environment, app skeleton (Size: S)
- Acceptance: `git init` in `paypal/`; venv; pinned `pyproject.toml`; FastAPI app with `/health`; `.env.example`; `.gitignore` (db, .env, caches).
- Verify: `python -m pytest -q` and `uvicorn app.main:app` → `curl /health`.
- Files: `backend/pyproject.toml`, `app/main.py`, `app/config.py`, `.gitignore`.
- Concepts explained: virtual envs, dependency pinning, FastAPI app factory, settings from env.
- Blocked by: none.

#### Step 0.2: External-service spikes (Size: S)
- Acceptance: a script per provided key makes one real call: Groq: JSON claim extraction with a text model **and** a vision request with two images (baseline + listing photo), Routes API matrix, PayPal OAuth + create one sandbox product (≤256-char description). Results recorded in BUILD_LOG.
- Verify: `python -m scripts.spike_groq` / `spike_maps` / `spike_paypal`.
- Files: `backend/scripts/spike_*.py`.
- Concepts: OAuth2 client-credentials, structured LLM output, idempotency keys.
- Blocked by: 0.1. Skipped per service if no key (then that service runs as Fake/demo).

### Phase 1: Data model, auth, seed
Verified when: seeded DB has 2 flats, 2 brokers, mandates; a broker key cannot call manager endpoints (403).

#### Step 1.1: ORM models + migrations (Size: M)
- Acceptance: tables `users`, `properties`, `baseline_photos`, `mandates`, `listings`, `listing_versions`, `listing_photos`, `commute_claims`, `audits` (with `report_json`, `signature`, `status`), `outbox_jobs`, `trust_events`. Alembic migration up and down both work.
- Verify: `alembic upgrade head && alembic downgrade base && alembic upgrade head`; `pytest tests/unit/test_models.py`.
- Files: `app/db/*`, `alembic/`.
- Concepts: SQLAlchemy 2.0 typed models, foreign keys, one listing → many versions (audit history), migrations.
- Blocked by: 0.1.

#### Step 1.2: API-key auth with roles + seed (Size: S)
- Acceptance: `X-API-Key` header → user with role MANAGER/BROKER; `require_role()` dependency; broker id always comes from the key, never the body (fixes M6). Seed script with demo data.
- Verify: `pytest tests/api/test_auth.py`.
- Files: `app/auth.py`, `app/db/seed.py`.
- Concepts: FastAPI dependencies, authentication vs authorization.
- Blocked by: 1.1.

#### Step 1.3: Manager endpoints: properties + mandates (Size: S)
- Acceptance: `POST /manager/properties`, `POST /manager/mandates` (min/max rent, max deposit, brokerage cap, valid_until) persisted in DB.
- Verify: `pytest tests/api/test_manager.py`.
- Files: `app/api/manager.py`, `app/schemas/*`.
- Blocked by: 1.2.

### Phase 2: First vertical slice — submit → Check D → decision → stored audit
Verified when: a broker submits a listing with inflated rent and gets NEEDS_FIX with evidence; a correct one gets VERIFIED (mint stubbed); both audits readable back exactly as stored.

#### Step 2.1: CheckResult + Check D + decision (Size: S)
- Acceptance: `CheckResult(verdict, evidence[], source)`; Check D compares rent range, deposit, furnishing rank, brokerage cap against the mandate (fixes M1); decision maps results to NEEDS_FIX / IN_REVIEW / VERIFIED.
- Verify: `pytest tests/unit/test_ground_truth.py tests/unit/test_decision.py`.
- Files: `app/domain/check_result.py`, `ground_truth.py`, `decision.py`.
- Concepts: pure functions, three-state results, why "unknown" must never become "pass".
- Blocked by: 1.3.

#### Step 2.2: LangGraph pipeline with D only + submit endpoint (Size: M)
- Acceptance: graph `START → extract(stub) → fan-out [D] → decide → END`; `POST /broker/listings/{id}/submit` checks mandate (403 if none), runs graph, saves version + audit + `report_json` in one transaction (fixes M7); `GET /audits/{id}` returns the stored report.
- Verify: `pytest tests/api/test_submit_slice.py`.
- Files: `app/agents/*`, `app/services/audit_service.py`, `app/api/broker.py`.
- Concepts: LangGraph state, nodes, parallel fan-out and fan-in, DB transactions.
- Blocked by: 2.1.

### Phase 3: Claim extraction (Groq)
Verified when: a listing whose title says "2 MIN WALK TO METRO" with no structured claims produces an extracted commute claim (fixes M2).

#### Step 3.1: LLM adapter + extractor node (Size: S)
- Acceptance: `LLM.extract_claims(title, description) -> ExtractedClaims` via a Groq text model in JSON mode, validated with a Pydantic schema (one retry on invalid JSON, then UNVERIFIABLE); model name from `GROQ_TEXT_MODEL`; FakeLLM for tests; merged with structured claims; missing key → claims from form only plus a flag in the report.
- Verify: `pytest tests/unit/test_extractor.py` (fake) and `scripts/spike_groq` (real).
- Files: `app/adapters/llm.py`, `app/agents/nodes.py`.
- Concepts: structured output, schema validation, prompt injection limits.
- Blocked by: 2.2.

### Phase 4: Check B commute
Verified when: claim 5 min vs 22 min → FAIL; claim 5 vs 7 → PASS; unknown destination with no Maps data → UNVERIFIABLE, never an invented number (fixes M3, D5).

#### Step 4.1: Tolerance rules + Maps adapter + node (Size: M)
- Acceptance: `max(claimed×1.2, claimed+5)`; real adapter geocodes `"{dest}, {city}"` and asks Routes API for next weekday 08:30 IST; demo adapter reads `commute_matrix.yaml` with exact key match; cache per (property, destination, mode).
- Verify: `pytest tests/unit/test_commute_rules.py tests/unit/test_maps_adapter.py`.
- Files: `app/domain/commute_rules.py`, `app/adapters/maps.py`, `app/rules/commute_matrix.yaml`.
- Concepts: departure-time traffic models, epoch timestamps, caching.
- Blocked by: 3.1.

### Phase 5: Check C compliance
Verified when: Chennai 6-month deposit → FAIL with citation; Bengaluru 4-month → PASS with advisory shown in the report.

#### Step 5.1: Rules file + node (Size: S)
- Acceptance: `deposit_caps.yaml` entries `{state, max_months, kind: law|platform_policy, citation, section, source_url, effective_date, verified: bool}`; unverified entries are labelled as such in evidence (fixes D6).
- Verify: `pytest tests/unit/test_compliance_rules.py`.
- Files: `app/domain/compliance_rules.py`, `app/rules/deposit_caps.yaml`.
- Blocked by: 2.2 (can run in parallel with Phases 3–4).

### Phase 6: Check A photos
Verified when: a check-in photo taken 2 km away → FAIL; a photo identical to another property's → FAIL; no Groq key or no vision model → UNVERIFIABLE (fixes D1–D3).

#### Step 6.1: Upload + GPS check-in + duplicate check (Size: M)
- Acceptance: `POST /broker/listings/{id}/photos` (1–20 images, ≤8 MB, jpeg/png/webp, `Image.MAX_IMAGE_PIXELS` set); GPS from the request (browser geolocation) or EXIF must be within 100 m of the property; pHash stored and compared with all other properties.
- Verify: `pytest tests/unit/test_images.py tests/api/test_photos.py`.
- Files: `app/adapters/images.py`, `app/api/broker.py`.
- Blocked by: 2.2.

#### Step 6.2: Same-room + watermark/furnishing via Groq vision (Size: S)
- Acceptance: one Groq vision call per extra photo vs baseline (model from `GROQ_VISION_MODEL`, images resized and base64-encoded under Groq's size limit): `same_room`, `watermark`, `furniture_level`, `confidence`; below threshold → UNVERIFIABLE.
- Verify: `pytest tests/unit/test_photo_node.py` (fake LLM).
- Files: `app/agents/nodes.py`, `app/adapters/llm.py`.
- Blocked by: 6.1, 3.1.

### Phase 7: Seal and mint
Verified when: VERIFIED audit has an HMAC signature over everything checked; changing one stored field makes `/verify` fail; PayPal product created once even if the worker runs twice (fixes M4, M5).

#### Step 7.1: Canonical JSON + HMAC signing + verify endpoint (Size: S)
- Acceptance: signature covers full version (all claims, broker photo pHashes), owner snapshot, check results, ruleset + model versions, audit id.
- Verify: `pytest tests/unit/test_signing.py`.
- Files: `app/domain/signing.py`, `app/api/public.py`.
- Concepts: hashing vs HMAC, canonical serialisation, why order matters.
- Blocked by: 2.2.

#### Step 7.2: Outbox + PayPal adapter (Size: M)
- Acceptance: audit saved as PENDING_MINT; worker mints with `id = PL-{version_id}` and `PayPal-Request-Id`; description ≤256 chars; non-2xx logged with `debug_id` and retried; never a fake id outside DEMO_MODE.
- Verify: `pytest tests/unit/test_outbox.py` (fake PayPal) and `scripts/spike_paypal` (sandbox).
- Files: `app/services/outbox.py`, `app/adapters/paypal.py`.
- Concepts: dual-write problem, outbox pattern, idempotency.
- Blocked by: 7.1.

### Phase 8: Broker workflow features
Verified when: the e2e demo script runs the full story: dishonest submit → NEEDS_FIX → pre-check suggests fixes → resubmit → VERIFIED → public page → tamper → verify fails.

#### Step 8.1: Review queue (Size: S)
- Acceptance: `GET /manager/reviews`, `POST /manager/reviews/{audit_id}` approve/reject with note; approve continues to signing.
- Blocked by: 7.2.

#### Step 8.2: Pre-check endpoint (Size: S)
- Acceptance: `POST /broker/precheck` runs extract + B + C + D without saving or minting; returns suggested wording; rate-limited.
- Blocked by: 4.1, 5.1.

#### Step 8.3: Public listing + availability + trust score (Size: M)
- Acceptance: `GET /public/listings/{id}` shows this version's audit only and the agreed photos; `POST /broker/listings/{id}/confirm-availability`; EXPIRED after 7 days; trust events recorded.
- Blocked by: 7.2.

#### Step 8.4: E2E demo script + README (Size: S)
- Acceptance: `python -m scripts.demo` prints the full story; README with setup and run commands.
- Blocked by: 8.1–8.3, 6.2.

---

## Decision log

| # | Decision | Proposed | Why |
| --- | --- | --- | --- |
| D1 | Language/framework | Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2.0, LangGraph | Matches blueprint; all already installed |
| D2 | Database | SQLite file now, Postgres-compatible models (assumed) | Zero setup; switch by `DATABASE_URL` for Render |
| D3 | Migrations | Alembic from day one | Rollback-friendly; cheap to start, painful to add later |
| D4 | Auth | API key per user with role (assumed) | Enough for a demo; JWT adds nothing judges see |
| D5 | Photo approach | **Chosen:** manager photos shown + broker GPS check-in photo + optional extra photos checked by a Groq vision model | Closes fake-photo hole without building a camera app |
| D14 | LLM provider | **Groq** (official `groq` Python SDK, OpenAI-style chat API); text and vision model names set in `.env`, not hard-coded | Your choice; models on Groq change often, so the names must be config, and the Phase 0 spike proves which current models support JSON output and image input |
| D13 | Missing keys | Maps → demo table + DEMO_MODE label; PayPal → Fake adapter, product ids prefixed `FAKE-` so they can never be mistaken for real | You only have a Groq key today |
| D6 | External services | Real adapter when key present, Fake in tests, DEMO_MODE table/fake only when explicitly on | Fixes the silent-fallback class of bugs |
| D7 | Manual review | DB status `IN_REVIEW` + review endpoints (not LangGraph `interrupt`) | Simpler, survives restarts without a checkpointer |
| D8 | Commute tolerance | `max(claimed×1.2, claimed+5)` | Kept from old design; fair on short walks |
| D9 | Deposit rules | TN 3 months = law (hard); Karnataka >3 advisory, >6 hard = **platform policy**; all marked `verified: false` until checked against official text | Honest labelling; legal figures not yet verified |
| D10 | Rent rule | Rent must be within mandate `[min_rent, max_rent]`; commission only via `brokerage_fee` ≤ cap | Real-world brokerage is a separate fee |
| D11 | Frontend | **Chosen:** out of this plan; Swagger UI + demo script for now | Backend correctness first; UI gets its own plan |
| D12 | Pace and explanations | **Chosen:** pause after each phase with an explainer; BUILD_LOG entry per step | You asked to understand what happens while I code |
| D15 | Extraction failure | Missing key / API error / invalid JSON twice → `claims` check UNVERIFIABLE → IN_REVIEW (not just a flag); commute claims → UNVERIFIABLE until Check B exists | Unread text could hide claims; matches "missing key gives UNVERIFIABLE, never a guess" |

## Not yet specified

- ~~Phase 0: which Groq models to use~~ **Resolved 2026-10-28:** text `openai/gpt-oss-120b`, vision `qwen/qwen3.8-27b`; two images per request works. Original question: the spike must confirm (a) a text model that returns valid JSON for claim extraction, and (b) a vision model that accepts **two images in one request**. If (b) fails, Step 6.2 compares photos one at a time against a text description of the baseline, or returns UNVERIFIABLE → IN_REVIEW.

- Phase 0: keys available = **Groq only** (changed from Gemini on 2026-10-28). Maps runs on the demo route table (`commute_matrix.yaml`, unknown routes → UNVERIFIABLE). PayPal runs on the Fake adapter until a sandbox app exists; the real adapter is still built and unit-tested against recorded responses.
- Phase 6: exact GPS source for the check-in photo when there is no frontend (proposal: lat/lng sent in the upload request, EXIF as backup). Fog until a UI exists.
- Phase 8: hosting (Render) and the background worker process model — fog, decided when we deploy.
- Features 2–4 integration points (escrow, invoice) — fog, out of this plan.

## Always / Never

- Always: every check returns PASS / FAIL / UNVERIFIABLE with evidence and source; tests never hit the network; one commit per step; each step ends with its verification command passing.
- Always: secrets only in `.env`, never committed.
- Never: invent data when a service is missing (no fake times, fake product ids, fabricated reports) outside an explicit `DEMO_MODE`.
- Never in this plan: frontend UI, Features 2–4, reverse image search, Postgres deployment, real KYC.

## Progress

| Phase | Status |
| --- | --- |
| 0 Foundations | **done** — Groq verified: text `openai/gpt-oss-120b`, vision `qwen/qwen3.8-27b` (two images OK) |
| 1 Data + auth | **done** — 11 tables + migration, API-key roles, seed, manager endpoints; 28 tests |
| 2 First slice (Check D) | **done** — Check D, decision, LangGraph pipeline, open + submit listing, stored audits; 60 tests |
| 3 Claim extraction | **done** — Groq extractor (JSON mode + Pydantic, one retry), merged form/text claims, text furnishing → Check D, commute placeholder; 78 tests |
| 4 Check B | not started |
| 5 Check C | not started |
| 6 Check A | not started |
| 7 Seal and mint | not started |
| 8 Broker workflow + demo | not started |

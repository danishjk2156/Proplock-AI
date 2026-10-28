# Build log: Feature 1 rebuild

One entry per step: what changed, why, the key idea, and how to see it working.
Plan: `plans/pending/feature1-rebuild.md`.

---

## Step 0.1: Repo, environment, app skeleton (2026-10-28)

**What changed**
- `git init` in `paypal/` (branch `main`). `.gitignore` keeps out the virtualenv, caches, `*.db` files and `.env`.
- `backend/.venv`: a private Python 3.11 environment for this project only.
- `backend/pyproject.toml`: every library pinned to an exact version (`==`), plus pytest settings.
- `backend/app/config.py`: one `Settings` class that reads environment variables or `backend/.env`.
- `backend/app/main.py`: `create_app()` builds the FastAPI app with a `GET /health` endpoint.
- `backend/tests/api/test_health.py`: first test.

**Why it's built this way**
- *Virtualenv:* the project's libraries don't collide with other projects on your machine, and a judge can recreate the exact setup.
- *Exact pins:* the old project's `requirements.txt` used `>=` and was missing two libraries (issue M8). With `==`, everyone installs the same versions and nothing upgrades under you mid-hackathon.
- *Settings in one place:* code never reads `os.environ` directly. Every key, model name and flag lives in `Settings`, so tests can override them and nothing is hard-coded. An empty model name means "not configured", which later makes a check return UNVERIFIABLE instead of guessing.
- *App factory (`create_app`)*: tests build a fresh app each time instead of sharing one global, which keeps tests independent.

**Key idea: FastAPI request flow**
`uvicorn` is the web server. It receives the HTTP request, hands it to the FastAPI `app`, FastAPI matches the path (`/health`) to a Python function, calls it, and turns the returned dict into JSON. FastAPI also generates the interactive docs at `/docs` (Swagger UI) from the same functions; that's how you'll test the API without a frontend.

**How to see it**
```bash
cd backend
.venv/Scripts/python -m pytest -q
.venv/Scripts/python -m uvicorn app.main:app --reload
```
Then open http://localhost:8000/health and http://localhost:8000/docs.

**Verified:** `pytest` → 1 passed; `/health` → `{"status":"ok","env":"development"}`; `/docs` → 200.

---

## Step 0.2: Groq spike script (2026-10-28)

**What changed**
- `backend/scripts/spike_groq.py`: a one-off probe that makes three real Groq calls:
  1. list the models your key can use (proves the key works),
  2. claim extraction with a text model in **JSON mode**, validated against a Pydantic schema,
  3. a vision model given **two images in one request** (an empty room and the same room with a sofa), answering in JSON.
- It prints the model ids that passed, ready to paste into `backend/.env`.

**Why a spike before building**
The riskiest unknown in the whole plan is whether a Groq vision model accepts two images at once (Check A's same-room comparison depends on it). Finding out now costs one script; finding out in Phase 6 would mean redesigning a finished feature. That's "risk first": prove the dependency, then build on it.

**Key ideas**
- *JSON mode* (`response_format={"type": "json_object"}`) makes the model reply with JSON, but not necessarily **our** JSON shape. That's why every reply is passed through a Pydantic model (`ExtractedClaims`, `SameRoom`). If validation fails, we know the model can't be trusted for that job.
- *Images over an API*: the image bytes are base64-encoded into a `data:image/jpeg;base64,...` URL inside the chat message. Each image is just another item in the message's `content` list next to the text.
- The drawn test rooms only prove the **plumbing** (two images accepted, JSON returned). Whether the model is *accurate* on real flat photos is tested in Phase 6 with real images.
- *Exit codes* (0 pass, 1 a check failed, 2 no key) let scripts and CI read the result without parsing text.

**How to run it**
1. Copy `backend/.env.example` to `backend/.env` and set `GROQ_API_KEY=...`.
2. From `backend/`: `.venv/Scripts/python -m scripts.spike_groq`
3. Paste the printed `GROQ_TEXT_MODEL` and `GROQ_VISION_MODEL` lines into `.env`.

**Verified (real calls, 2026-10-28):**
- Key works. Models available to it include `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b` (no Llama 4 vision models on this account).
- Text extraction with `openai/gpt-oss-120b`: PASS. From "2 MIN WALK TO INDIRANAGAR METRO ... 10 mins drive to Bagmane Tech Park ... Fully furnished" it returned both commute claims with the right modes plus the furnishing claim, all matching our schema.
- Vision with `qwen/qwen3.8-27b`, two images in one request: PASS. Empty room vs same room with a sofa → `same_room: true` (it ignored the furniture as told). Extra check: window and door swapped → `same_room: false`, with the swap named as the reason.
- Chosen and set in `backend/.env`: `GROQ_TEXT_MODEL=openai/gpt-oss-120b`, `GROQ_VISION_MODEL=qwen/qwen3.8-27b`.

**What this changes in the plan:** nothing structural. The two-image same-room design for Check A works, so the fallback (comparing against a text description) is not needed. Accuracy on real flat photos is still to be measured in Phase 6.

---

## Step 1.1: Database tables and migrations (2026-10-28)

**What changed**
- `app/db/base.py`: the database engine, a session factory, `get_session()` for FastAPI, and `UTCDateTime`.
- `app/db/models.py`: 11 tables — `users`, `properties`, `baseline_photos`, `mandates`, `listings`, `listing_versions`, `listing_photos`, `commute_claims`, `audits`, `outbox_jobs`, `trust_events`.
- `alembic/` + `alembic.ini`: migrations. First migration `initial feature1 schema` creates all tables.
- Tests: `tests/conftest.py` (fresh database per test), `tests/unit/test_models.py`, `tests/unit/test_migrations.py`.

**How the tables fit together**
```text
users (MANAGER / BROKER)
  └─ properties (the flat; owner facts; verified GPS) ── baseline_photos (manager's photos)
       └─ mandates (this broker may list this flat: rent range, max deposit, brokerage cap, expiry)
            └─ listings (one broker's ad; status DRAFT → … → LIVE)
                 └─ listing_versions (every edit = new numbered version; old ones kept)
                      ├─ listing_photos (check-in + extra photos, with GPS and pHash)
                      ├─ commute_claims (from the form or EXTRACTED by the AI)
                      └─ audits (one per version: status, report_json, signature, PayPal id)
                           ├─ outbox_jobs (work after saving, e.g. create PayPal product)
                           └─ trust_events (+/- points for the broker)
```

**Why it's built this way**
- *ORM (SQLAlchemy):* each table is a Python class; a row is an object. `Mapped[Decimal]` etc. are type hints SQLAlchemy reads to create the right column types.
- *Versions instead of editing in place:* a broker can't silently change a verified ad. An edit creates version 2, which needs its own audit, and version 1 stays as evidence for disputes.
- *Money as `Numeric(12,2)` → `Decimal`:* floats can't store 0.10 exactly (`0.1 + 0.2 != 0.3`). For rent and deposits we need exact values.
- *Enums with CHECK constraints:* statuses are a fixed list (`DRAFT`, `NEEDS_FIX`, …). The database itself rejects anything else, so a typo like `VERIFED` fails loudly instead of creating a listing stuck in a status no code understands.
- *API keys stored as a hash:* the `users` table keeps only SHA-256 of each key. If the database leaks, the keys still can't be used.
- *`UTCDateTime`:* SQLite throws away timezone information. Without this, a stored time comes back "naive" and comparing it with `datetime.now(UTC)` crashes. The custom type stores UTC and re-attaches the timezone when reading.
- *SQLite foreign keys:* SQLite ignores foreign keys unless `PRAGMA foreign_keys=ON` is set for each connection. `make_engine` does that, otherwise a listing could point at a flat that doesn't exist.

**Key idea: migrations**
A migration is a versioned script that changes the database schema (`upgrade`) and can undo it (`downgrade`). Alembic compares the models with the database and *autogenerates* the script; we read and commit it. Later changes become new migrations stacked on top, so every database (yours, a teammate's, Render's) can be brought to the same schema with `alembic upgrade head`, and a bad change can be rolled back.

**How to see it**
```bash
cd backend
.venv/Scripts/alembic upgrade head      # creates proplock.db with all tables
.venv/Scripts/alembic downgrade base    # removes them again
.venv/Scripts/python -m pytest -q
```

**Verified:** migration up → down → up runs cleanly; 7 tests pass (exact money, timezone-aware dates, duplicate version rejected, foreign keys enforced, unknown status rejected by the database, migration up/down).

---

## Step 1.2: API-key login with roles + demo seed (2026-10-28)

**What changed**
- `app/auth.py`: `current_user` (reads the `X-API-Key` header, finds the user), `require_role(...)` (blocks the wrong role), `hash_key`, `generate_key`.
- `app/api/me.py`: `GET /me` returns who you are, handy for checking which key Swagger is using.
- `app/db/seed.py`: demo data — manager "XYZ Properties Desk", brokers **Rajesh** (honest) and **QuickCash Rentals** (our future liar), flats `PROP-BLR-001` (Bengaluru, Unfurnished) and `PROP-CHN-002` (Chennai, Semi-Furnished), and three mandates with rent ranges and deposit caps.
- `demo_keys.json` (git-ignored) stores the generated keys. Tests: `tests/api/test_auth.py`, `tests/unit/test_seed.py`; `tests/conftest.py` gained a `client` and `make_user` fixture.

**Key idea: FastAPI dependencies**
`def me(user: User = Depends(current_user))` tells FastAPI: "before calling `me`, run `current_user` and pass me its result." `current_user` itself depends on the header and a database session. FastAPI builds that chain for every request. If any link raises `HTTPException`, the endpoint never runs. That's why auth lives in one place: every protected endpoint just adds `Depends(require_role(Role.MANAGER))`.

**401 vs 403**
- **401 Unauthorized**: "I don't know who you are" (no key, or a key that matches nobody).
- **403 Forbidden**: "I know who you are, and you're not allowed" (a broker calling a manager endpoint).

**Why the broker id comes from the key, never the request body**
In the old code anyone could send `"broker_id": "someone-else"` (issue M6). Now the server decides who you are from your key, so a broker can only ever act as themselves.

**Why hash the keys**
Like passwords: the database keeps `sha256(key)`, not the key. When a request arrives we hash its key and look for that hash. A stolen database gives an attacker nothing they can log in with. (A random 32-character key doesn't need slow password hashing like bcrypt; SHA-256 is enough because keys can't be guessed.)

**Key idea: dependency overrides in tests**
`app.dependency_overrides[get_session] = lambda: db_session` swaps the real database for the test's throwaway one. The endpoints don't know or care; they just ask for "a session".

**How to see it**
```bash
cd backend
.venv/Scripts/alembic upgrade head
.venv/Scripts/python -m app.db.seed          # prints 3 keys, saves demo_keys.json
.venv/Scripts/python -m uvicorn app.main:app --reload
```
Open http://localhost:8000/docs → **Authorize** → paste a key → try `GET /me`.

**Verified:** 14 tests pass. Live server: Rajesh's key → `/me` returns Rajesh (BROKER); no key → 401. Seeding twice leaves the data unchanged.

---

## Step 1.3: Manager endpoints — flats and mandates (2026-10-28)

**What changed**
- `app/schemas/manager.py`: Pydantic shapes for requests and responses (`PropertyIn/Out`, `MandateIn/Out`, `BrokerOut`).
- `app/api/manager.py`, all MANAGER-only:
  - `POST /manager/properties`, `GET /manager/properties`
  - `GET /manager/brokers`
  - `POST /manager/mandates`, `GET /manager/mandates?property_id=…`, `POST /manager/mandates/{id}/revoke`
- `tests/api/test_manager.py`: 14 tests.

**Key idea: validation happens before your code runs**
FastAPI reads the JSON body into `MandateIn`. Pydantic checks every rule declared on it — rent > 0, `max_rent ≥ min_rent`, brokerage between 0 and 3 months, `valid_until` in the future **and with a timezone** — and if any fails, returns **422** with the exact field and reason. The endpoint function only ever sees valid data. Rules that need the database (does this flat exist? is this user really a broker? is there already an active mandate?) are checked in the endpoint and return 404 / 422 / 409.

**Status codes used**
| Code | Meaning here |
|---|---|
| 201 | created |
| 403 | a broker tried a manager action |
| 404 | the flat doesn't exist |
| 409 | conflict: flat id already used, or broker already has an active mandate for this flat |
| 422 | the data itself is invalid |

**Why money comes back as a string (`"35000.00"`)**
JSON numbers are floats in most languages, and floats can't hold every decimal exactly. Sending Decimals as strings keeps them exact end to end. Clients convert them when displaying.

**Why the mandate matters**
It's the real-world "owner gave this broker the keys" step, written down with limits. From Phase 2 on, a broker without an active, unexpired mandate can't submit a listing for that flat (403), and Check D compares every listing with these limits.

**How to see it**
In `/docs`: Authorize with the manager key from `demo_keys.json` → `GET /manager/brokers` → copy Rajesh's id → `POST /manager/properties` to add a flat → `POST /manager/mandates` for Rajesh. Then Authorize with Rajesh's key and try the same call: 403.

**Verified:** 28 tests pass. Live: manager lists both brokers; Rajesh calling `/manager/brokers` → 403; `PROP-BLR-001` shows its two seeded mandates (₹32,000–35,000).

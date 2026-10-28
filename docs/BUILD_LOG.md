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

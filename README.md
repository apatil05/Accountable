# Accountable

Know who you're paying. Accountable takes your (mock) Capital One transactions from the Nessie API,
finds the merchants you buy from, researches their ethical record, scores them against **your**
values, maps how their products are made and reach you, and suggests better-aligned alternatives.

**Team plan, architecture and who-owns-what: [`docs/PLAN.md`](docs/PLAN.md)**

## Run it locally

Runs fully offline on mock data. No API keys needed to start.

**Backend** (Python 3.11+)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```
API docs at http://localhost:8000/docs

**Frontend** (Node 20+), in a second terminal
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173. Vite proxies `/api` to the backend.

## Layout

```
backend/
  app/
    main.py            routes
    models.py          API contract (mirrored in frontend/src/api/types.ts)
    config.py          env vars
    store.py           user profile storage (JSON file → DB later)
    services/
      nessie.py        Nessie client + mock fallback          (Person A)
      aggregation.py   merchant spend summary                 (Person A)
      ethics.py        research engine, Gemini goes here      (Person B)
      validation.py    validate LLM claims                    (Person B)
      scoring.py       personalized score                     (Person B)
      supply_chain.py  supply chains + alternatives           (Person D)
    data/              mock data in the same shape as the real sources
  scripts/seed_nessie.py  load mock data into the Nessie sandbox
frontend/
  src/
    api/               typed client + types
    pages/             Dashboard, Values, MerchantDetail      (Person C), MapPage (Person D)
    components/        WorldMap (Person D), ScoreBadge, MockBanner
docs/PLAN.md
```

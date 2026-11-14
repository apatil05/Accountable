# Accountable

Know who you're paying. Accountable takes your (mock) Capital One transactions from the Nessie API,
finds the merchants you buy from, researches their ethical record, scores them against **your**
values, maps how their products are made and reach you, and suggests better-aligned alternatives.

**Team plan, architecture and who-owns-what: [`docs/PLAN.md`](docs/PLAN.md)**

## Run it locally

Runs on mock Nessie data. You need a **MongoDB Atlas** connection string (see below), but no
Nessie or Gemini keys.

**One-time: MongoDB Atlas** (the team shares one free cluster and one database, `accountable`)
1. Ask the team lead for the `MONGODB_URI` (it contains the password; never commit it).
2. Your IP must be in the cluster's **Network Access** list. If the backend says it cannot
   connect, add your IP in Atlas (Security -> Network Access).
3. Optional: install [MongoDB Compass](https://www.mongodb.com/products/tools/compass) and paste
   the same URI to browse the data like a SQL client.

Creating the cluster yourself: Atlas -> create a free M0 cluster -> Database Access: add a user
with read/write -> Network Access: allow your IPs -> Connect -> Drivers -> copy the string.

**Backend** (Python 3.11+)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then set MONGODB_URI in .env
uvicorn app.main:app --reload --port 8000
```
On first start the backend loads the mock customer into Atlas. Re-sync any time with
`python -m scripts.sync`. Over HTTP, `POST /api/sync` needs the `X-Admin-Token` header matching
`ADMIN_TOKEN` (and is limited to one run per 30 seconds); it's disabled when `ADMIN_TOKEN` is empty.

The API only serves the configured customer. Set `ALLOW_CUSTOMER_OVERRIDE=true` locally if you
need `?customer_id=` or `/api/customers`; keep it `false` on any public deploy.
API docs at http://localhost:8000/docs

**Tests** (from `backend/`): `pytest`. Tests that need a database run against a throwaway
`accountable_test_*` database on the same cluster, and are skipped when `MONGODB_URI` is unset.

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
    store.py           user profile storage (MongoDB `profiles`)
    db/                Atlas client, indexes, per-collection helpers   (Person A)
    services/
      nessie.py        Nessie fetch-only client + mock fallback        (Person A)
      normalize.py     merchant names -> canonical brands              (Person A)
      ingest.py        Nessie -> normalize -> MongoDB sync             (Person A)
      aggregation.py   spend summary (Mongo aggregation pipeline)     (Person A)
      ethics.py        research engine, Gemini goes here      (Person B)
      validation.py    validate LLM claims                    (Person B)
      scoring.py       personalized score                     (Person B)
      supply_chain.py  supply chains + alternatives           (Person D)
    data/              mock data in the same shape as the real sources,
                       plus brands.json / category_map.json (add aliases here)
  scripts/seed_nessie.py  load mock data into the Nessie sandbox
  scripts/sync.py         pull a customer from Nessie into MongoDB
  tests/                  pytest suite
frontend/
  src/
    api/               typed client + types
    pages/             Dashboard, Values, MerchantDetail      (Person C), MapPage (Person D)
    components/        WorldMap (Person D), ScoreBadge, MockBanner
docs/PLAN.md
```

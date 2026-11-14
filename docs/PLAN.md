# Accountable: Team Plan

> Know who you're paying. Accountable reads your (mock) Capital One transactions, figures out which
> merchants you spend with, researches each company's ethical record, scores it against **your**
> values, shows where its products come from on a world map, and suggests better-aligned alternatives.

## 1. What we're building (from the whiteboard)

| # | Feature | Status in shell |
|---|---------|-----------------|
| 1 | Summarize merchants + purchase frequency + money total | ✅ working on mock Nessie data |
| 2 | User inputs ethical / moral / political stances (form → JSON doc) + sources they trust | ✅ form saves `UserProfile` JSON |
| 3 | Research the web for each merchant's ethical stance | 🟡 stub, returns mock; **Gemini goes here** |
| 4 ★ | Validate the LLM's output (scored, backed by real research) | 🟡 stub rule: claim needs a source + confidence ≥ 0.6 |
| 5 | Suggest local / values-aligned alternatives | 🟡 static list by merchant category |
| + | Global map of how products are made and reach you | ✅ map works on mock supply chains |

**Stack:** Python (FastAPI) backend · React (Vite + TypeScript) frontend · JSON file store now →
Postgres or Mongo later · GoDaddy domain for the demo. (GCP object store was crossed out.)

## 2. Architecture

```
 Nessie API ──► services/nessie.py ──► services/aggregation.py ──► GET /api/spending/summary ──► Dashboard
 (or mock JSON)                                    │
 UserProfile (store.py) ──────────────────────────►│ services/scoring.py (weights × category scores)
                                                   │
 Gemini + Google Search ──► services/ethics.py ──► services/validation.py ──► GET /api/merchants/{id}/ethics ──► Merchant page
 (TODO)                                            
 Gemini + geocoder ──► services/supply_chain.py ──► GET /api/merchants/{id}/supply-chain, /api/supply-chains ──► WorldMap
 (TODO)                           └──────────────► GET /api/merchants/{id}/alternatives
```

**The contract** lives in `backend/app/models.py` and is mirrored in `frontend/src/api/types.ts`.
If you change a shape, change both files in the same PR and tell the team in chat.

Every piece of mock data has the same shape the real source will return, so each owner can swap
their mock for the real thing without touching anyone else's code.

### API (all under `/api`)

| Method | Path | Returns | Owner |
|---|---|---|---|
| GET | `/health` | `{ok, mock_data, gemini_configured}` | A |
| GET | `/customers` | Nessie customers | A |
| GET | `/spending/summary` | `SpendingSummary` (merchants sorted by spend, with personalized score) | A |
| GET | `/spending/transactions` | `Purchase[]` | A |
| GET/PUT | `/profile` | `UserProfile` | A (storage) / C (form) |
| GET | `/merchants/{id}/ethics` | `EthicsReport` (category scores, claims w/ sources, validated flags) | B |
| GET | `/merchants/{id}/supply-chain` | `SupplyChain` (ordered stages with lat/lng) | D |
| GET | `/supply-chains` | `SupplyChain[]` for everything the user buys | D |
| GET | `/merchants/{id}/alternatives` | `Alternative[]` | D |

## 3. Who does what

Four tracks, each with one owner and clear files. Names TBD; claim one in the group chat.

### Person A: Data & Backend (Nessie, storage, deploy)
**Owns:** `services/nessie.py`, `services/aggregation.py`, `store.py`, `scripts/seed_nessie.py`, `main.py`, deployment.
1. Get a Nessie API key, run `python -m scripts.seed_nessie` to load our mock merchants + purchases into the sandbox, set `USE_MOCK_DATA=false`. Fix whatever the real responses break.
2. Merchant name normalization (Nessie names can be messy; map "AMZN Mktp" → "Amazon"), since research quality depends on it. **Done:** `services/normalize.py` + `data/brands.json`. Every Nessie merchant resolves to a brand; **`merchant_id` in the API is now the brand slug** (e.g. `amazon`, `traderjoes`), so B and D key their data by brand slug. To teach it a new brand, add an entry with aliases to `data/brands.json`.
3. **DB: MongoDB Atlas (decided).** One free M0 cluster, one shared database `accountable`. Collections: `brands`, `merchants`, `customers`, `purchases`, `profiles`, `sync_runs`, and `ethics_reports` / `supply_chains` (keyed by brand slug) for B and D to cache into. `store.py` now reads/writes `profiles`. Data flows Nessie → `ingest.py` (normalize + upsert) → Atlas; routes read from Atlas.
4. Spending insights for the dashboard: per-month trend, "top category", spend by score band.
5. Deploy: backend (Render / Railway / Cloud Run), frontend (Vercel / Netlify), point the GoDaddy domain at it.

### Person B: Research engine (Gemini + validation + scoring) ★ highest risk, start first
**Owns:** `services/ethics.py`, `services/validation.py`, `services/scoring.py`.
1. **Gemini research** in `research_with_gemini()` using the `google-genai` SDK with the **Google Search grounding** tool. One call per merchant, prompt asks for each of the 6 categories: score 0-100, 2-sentence summary, 2-4 claims each with a source URL. Pass the user's `trusted_sources` and `stances` as context.
   - If the model won't combine search grounding with a strict JSON schema, do two calls: (a) grounded research in free text, (b) "convert this to JSON matching this schema."
   - Pull real URLs from the response's grounding metadata rather than trusting URLs written in the text. Grounding URLs may be redirect links; resolve them before validating.
2. **Validation (feature 4, starred):** the LLM never sets `validated`. For each claim: URL resolves (200), page text mentions the merchant, domain not on a blocklist, bonus if domain is in the user's trusted sources. Stretch: a second "judge" Gemini call that reads the fetched page and answers "does this page support the claim? yes/no + quote."
3. **Scoring:** a category's score should get less weight when most of its claims are unverified. Decide what weight 5 ("deal-breaker") means (e.g. cap the overall score if that category < 40).
4. **Caching:** research is slow and costs money. Cache per merchant (Person A's DB) and pre-warm the demo merchants before judging.
5. Keep a small eval set: 5 merchants where we know the right answer, to sanity-check the prompt.

### Person C: Frontend & UX
**Owns:** `frontend/src/pages/Dashboard.tsx`, `Values.tsx`, `MerchantDetail.tsx`, `App.tsx`, `styles.css`.
1. Onboarding flow: first visit → "pick your values" → dashboard. Make the values form feel quick (presets like "Climate-first", "Workers-first").
2. Dashboard: spend-over-time chart, filter/sort by score, "biggest misalignment" callout (high spend + low score).
3. Merchant page: loading state for real research (it'll take seconds), source links, "why this score" explainer, Verified/Unverified badges.
4. Responsive / mobile check, empty and error states, final visual polish for the demo.
5. Own the demo script + slides with everyone's input.

### Person D: Map & Alternatives
**Owns:** `frontend/src/components/WorldMap.tsx`, `frontend/src/pages/MapPage.tsx`, `services/supply_chain.py`.
1. **Real supply chains:** ask Gemini for the stages of each merchant's most likely purchased product (stage + place name only), then **geocode the place names** ourselves (Nominatim or a lookup table of common hubs) so the LLM never invents coordinates.
2. **Map upgrades:** animate a "package" traveling along the route, step-through mode (click stage 1 → 2 → 3 with a side panel), color stages by issue (e.g. a mining stage flagged red if labor risks are found). Optional: swap to a 3D globe (`react-globe.gl`) if we have time; the current SVG map works offline and is the safe fallback.
3. **Alternatives (feature 5):** Gemini (+ a places search near the user's location) for local / better-aligned options, ranked by the user's weights and `prefer_local`.
4. Link alternatives back to the map ("this one is made 30 miles away").

## 4. Timeline

| Phase | Goal | Done when |
|---|---|---|
| 0. Shell ✅ | Everyone can run the app locally on mock data | `npm run dev` + `uvicorn` show 4 working pages |
| 1. Real data | Each owner replaces their mock behind the same contract | Nessie live (A), Gemini report for 1 merchant (B), 1 real geocoded chain (D), onboarding (C) |
| 2. Integrate | All 10 demo merchants researched + cached, validation badges real | Full flow works end-to-end with keys |
| 3. Polish + demo | Deployed on the domain, demo script rehearsed | 3-minute demo runs without a network hiccup (cached data) |

Sync every few hours: 5 minutes, each person says what's done, what's blocked, and any contract change.

## 5. Ground rules
- **Branches:** `a/nessie-live`, `b/gemini-research`, etc. PR into `main`; anyone can review; keep `main` runnable.
- **Secrets** only in `backend/.env` (gitignored). Never commit keys. Share them in DMs.
- **Contract changes** = `models.py` + `types.ts` in the same PR.
- **Mock stays working.** `USE_MOCK_DATA=true` runs with no Nessie or Gemini keys (mock customer is loaded into Atlas on first start). It does need `MONGODB_URI`. Tests use a throwaway `accountable_test_*` database and never touch `accountable`.
- Ethics output about real companies must show sources and the Verified/Unverified badge. No unsourced claims in the UI.

## 6. Open questions
- ~~Postgres or Mongo?~~ Decided: MongoDB Atlas (see Person A, item 3).
- How do we present political spending without the app itself taking a side? (Suggest: report facts like donation totals by recipient, let the user's own stated stances do the judging.)
- Which product do we map for big-box stores (Walmart, Amazon) that sell everything? Top purchase category, or let the user pick?
- Demo persona: whose values do we show live?

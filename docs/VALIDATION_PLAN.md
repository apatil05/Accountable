# Validation plan: verifying Gemini research

Owner: Person B (Research engine). Branch: `gemini-research` (same PR as the research work).

## Why

A live run on Starbucks (2026-11-14) produced 18 claims and all 18 came back `validated=True`. The old rule in `validation.py` was "has a URL and confidence >= 0.6", and Gemini set confidence itself (17 of 18 were 0.95), so in practice the LLM decided `validated`. Nothing fetched the page or checked that it supported the claim. One validated claim cited a reddit thread; others cited a union campaign site and a plaintiff law firm press release as plain fact.

## Status

| Step | Status |
| --- | --- |
| 1. Domain tiers + blocklist | Done |
| 2. Fetcher (HTML + PDF, SSRF guard) | Done |
| 3. Judge call (per source page, passages only, 30s cap) | Done |
| 4. Decision logic + per-claim logging | Done |
| 5. Rescore on validated claims, shrink toward 50, null when none | Done; replaced by 11 |
| 6. Scoring / verdict skip null categories | Done |
| 7. Research prompt: 4-6 claims per area, source steering | Done |
| 8. Caching: don't cache incomplete reports; profile-independent validated cache | Done (in memory; persistence still TODO) |
| 9. Evaluation set + script | Done: 22 Starbucks claims, `python -m scripts.eval_validation` |
| 10. Pre-warm script for demo merchants | Not started |
| 11. Code-computed category scores (direction x severity x tier x recency) + `eval_scoring`, `eval_stability` | Done |
| Frontend null score + hide empty claim list | Done, not type-checked (no Node on the dev machine; run `npm run build`) |

## Decisions

| Topic | Decision |
| --- | --- |
| Bar for `validated=True` | Page fetched, page mentions the merchant, AND a Gemini judge reading the page says it supports the claim, with a verbatim quote that code finds in the page |
| Failed claims | Hidden from the report and excluded from scoring |
| Source quality | Blocklist forums/social; tier the rest. Tiers only affect wording and confidence, not scoring |
| Low-tier sources (company, advocacy, law firm, unknown) | Validated if supported, but the judge rewrites the claim with attribution ("A lawsuit alleges...", "Starbucks says...") |
| "supported" vs "partial" | Rewording or adding attribution is still "supported". "partial" only when a specific detail (number, date, scope, actor) had to be removed or corrected; the rewritten claim must be fully supported |
| Category fit | The judge names the category each claim is evidence about. "none" (not about the merchant's conduct) fails the claim; a different category moves it there |
| Fetch blocked / paywalled / JS / PDF | httpx + pypdf first; if that fails, the judge reads the page via Gemini's `url_context` tool. If neither can read it, the claim is dropped |
| `url_context` verdicts | Accepted for any non-blocked domain (code can't check the quote on this path). Logged as `method=url_context` so eval can measure it; restrict to tier 1-2 if precision is poor |
| Judge input | Only the page passages most relevant to the claims (~9k chars, neighbors included), not the whole page. The quote check still runs against the full page |
| Judge time cap | 30s per judge call on both paths. On timeout the claims are dropped and the report is still complete (cached) |
| Judge batching | One judge call per source URL, parallel, global cap of 8 concurrent calls |
| Judge model | Same `GEMINI_MODEL` |
| Judge failure (rate limit, outage, not a timeout) | Claims hidden, report NOT cached so the next request retries; research stays cached |
| Category score | Computed by code (`scoring.category_score`), not Gemini. The judge labels each claim's direction (positive / negative) and severity (minor / notable / major); weight = severity x tier x recency; `score = (50*2 + sum(w*target)) / (2 + sum(w))`, target 100 positive / 0 negative. Tier 3 self-praise weighs 0.4, tier 3 self-admitted harm 1.0. See PIPELINE.md step 4 |
| Neutral claims | Judge direction `neutral` (context with no ethical weight) fails the claim, like category `none` |
| Category summary | Written by one Gemini call (`summarize`) from validated claims only, marked (+)/(-), keeping the claims' attribution. It does not score |
| Category with no validated claims | `score = None`, summary "Not enough verified evidence was found for this category.", skipped by overall/personalized/deal-breakers/verdict |
| Summary failure | Code scores still apply; show research summaries with only validated claims; don't cache, so the next request retries |
| Recency | The judge extracts the event year (internal `Claim._event_year`, not serialized); full weight up to 3 years old, then half-life 5 years; unknown year 0.7 |
| Validation details (quote, tier, direction, severity, reason, timing) | Never exposed by the API. Logged only (private `Claim` attrs) |
| Domain tiers / blocklist / aliases | Python constants in `validation.py` |
| Mock mode | No fetch, judge, scoring from claims, or summary call. Placeholder claims are unsourced, so they are hidden; mock scores stay so the offline demo still has a dashboard and verdicts |
| Research variability | Results differ between research runs; rely on caching (one stable result per merchant), re-research only on demand |
| Evaluation | Hand-labeled set of ~30 claims across 3 merchants plus planted fakes; script reports judge precision/recall |

## Pipeline

```text
research_with_gemini            (grounded search, 4-6 claims/area, steered sources)
  -> validation.validate_claims
       1. group claims by source URL
       2. per URL, in parallel (cap 8):
            fetch (httpx 10s, redirects checked, PDF via pypdf)
            -> domain tier on the final URL (blocked -> fail)
            -> judge call (30s cap) on relevant passages, or url_context if fetch failed
            -> per claim: verdict, quote, rewritten text, event year, category, direction, severity
       3. code decides validated:
            fetched by us: supported/partial AND merchant (or alias) in page AND quote in page
            url_context:   supported/partial
            unreadable / timeout / blocked: not validated
          then: category "none" or direction "neutral" fails; another category moves the claim;
                partial or tier 3-4 -> use the judge's rewritten (attributed) text
  -> drop unvalidated claims
  -> scoring.category_score (code): severity x tier x recency weights, pulled toward 50
     (no claims -> score None)
  -> summarize (Gemini, no search, validated claims only) -> summary text
  -> scoring.overall / personalized / verdict (skip None)
```

Profile-independent results (research, then validated + scored categories) are cached per merchant, so changing weights in `PUT /api/profile` only recomputes scores and the verdict.

## Domain tiers

- **Blocked:** reddit, x/twitter, facebook, instagram, tiktok, quora, medium, youtube, glassdoor, indeed, pinterest, linkedin, threads, wikipedia
- **Tier 1, government/court:** `.gov`, `.mil`, `.gov.uk`, `.europa.eu`, `.gc.ca`, `.gov.au`, `europa.eu`, `uscourts.gov`, `un.org`, `oecd.org`, `ilo.org`
- **Tier 2, established news / trade press / ratings:** ~90 domains (US, international, Industry Dive trade press, security press, ratings orgs). See `TIER2` in `validation.py`
- **Tier 3, company self-report:** the domain contains the merchant's name or an alias (e.g. `about.starbucks.com`)
- **Tier 4, everything else** (advocacy, law firms, blogs, unknown)

## API contract change

- `backend/app/models.py`: `CategoryScore.score: Optional[float] = None`
- `frontend/src/api/types.ts`: `score: number | null`
- `frontend/src/pages/MerchantDetail.tsx`: renders a null score as "—" with a "Not enough verified evidence" tooltip; hides the claim list when empty. (Person C: heads-up, minimal change in your file.)

`Claim.confidence` is now set by code from tier and verdict (supported > partial; tier 1 > tier 4), not by the LLM.

## Measured (Starbucks, single merchant)

| Run | Change | Claims | Validated | Validation | Rescore |
| --- | --- | --- | --- | --- | --- |
| 0 | old stub | 18 | 18 (nothing checked) | - | - |
| 1 | steps 1-4 | 23 | 21 | not timed | - |
| 2 | stricter partial, category fit, tier-2 list | 21 | 12 | 96s | - |
| 3 | 4-6 claims, steering, 25s url_context cap | 31 | 16 (10 lost to a timeout bug, fixed) | 80s | - |
| 4 | passages only, steps 5-6 | 29 | 16 | 54s | 16s |

Research itself takes 80-100s per merchant and is the other half of the latency. Pre-warming hides both for the demo.

## Evaluation

`backend/scripts/eval_claims.json`: 22 real Starbucks claims from the research runs above, labeled by reading each source page: 15 `supported`, 3 `partial`, 4 `not_supported`. A `partial` claim counts as correct if it is rejected or validated with its text rewritten. Claims whose pages block every automated reader (about.starbucks.com, fastcompany.com, theindependent.sg, Scribd) were dropped, so the `url_context` path is barely covered.

`python -m scripts.eval_validation` (from `backend/`) runs validation 3 times, prints false positives/negatives and unstable claims, and exits 1 if mean precision is below 90%. Recall is reported, not gated.

First result (2026-11-14): **precision mean 93%, worst 93%; recall mean 88%, worst 81%. PASS.**

- Only false positive: `sbux-02` (NakedPact, an auto-generated privacy rating site whose page also lists data brokers as a concern). Kept as an eval case, not blocked.
- `sbux-18` is a consistent false negative by design: the page supports it, but the judge says an ADA lawsuit over the non-dairy surcharge fits none of the six categories, so it is dropped.
- `sbux-04`, `sbux-16`, `sbux-24` flip between runs (judge quotes don't always match multi-fragment HTML exactly; partial rewrites vary).

Building the eval surfaced a race in `gemini.client()`: an unlocked `lru_cache` let concurrent first calls build duplicate clients, and the garbage-collected duplicates closed the HTTP connection mid-request ("client has been closed"). Fixed with a lock; this also affected `warm_cache` on a cold server.

## Next

1. Pre-warm script (step 10) for the demo merchants, with Gemini rate limits in mind (4 merchants at a time x 8 judge calls).
2. Persist the research and validated caches (shared with Person A's DB work).
3. Grow the eval with Amazon and Shell claims, and label the blocked-site claims by hand so the `url_context` path is measured.

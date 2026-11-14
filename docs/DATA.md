# Data sources and how they get into the app

Owner: Person A. Read this before adding a data source or showing a new number in the UI.

## The rules

1. **One join key.** Every fact is attached to a brand slug (`starbucks`, `traderjoes`). Outside IDs
   (Climate TRACE owner, DOL name patterns, carbon factor overrides) are mapped by hand once in
   `backend/app/data/brand_sources.json`, never guessed at request time.
2. **Every number carries its provenance** (`Provenance` in `models.py`): source, source name and URL,
   locator (`p. 86`, a case id), the year the fact describes, when we fetched it, how (`api`,
   `pdf_verified`, `manual`), and whether it is `reported`, `estimated`, or an `enforcement_record`.
   The UI shows the source and year next to the number.
3. **Validate, then write.** Loaders build plain dicts and call `services/pipeline.load()`. Records that
   fail their Pydantic model are rejected, counted, and logged in `pipeline_runs`; they are never written.
4. **Missing is not zero.** `null` means "not disclosed". A company with no report gets
   `status: "not_reported"` plus a reason, so the UI can say so.
5. **Nothing slow at request time.** Loaders run from a laptop and write to Atlas; the API only reads.

## Sources

| What the user sees | Source | Kind | Where it lives | Refreshed by |
|---|---|---|---|---|
| Transactions, merchants | Capital One Nessie API | reported | `purchases`, `merchants` | `python -m scripts.sync` |
| Estimated CO2e of the user's spending | Supply Chain GHG Emission Factors v1.4.0 (Cornerstone/EPA lineage, 2023 data, CC BY 4.0) + BLS CPI-U + EPA tailpipe CO2 + EIA gas price | estimated | `app/data/emission_factors.json` (committed) | `python -m scripts.build_emission_factors <xlsx>` (once per factor release) |
| Company-reported emissions (Scope 1/2/3) | Each company's sustainability report PDF | reported, `pdf_verified` | `company_emissions` | `python -m scripts.load_data reports` |
| Emissions of owned facilities (Shell) | Climate TRACE API (no key) | estimated | `company_emissions` | `python -m scripts.load_data climate_trace` |
| Wage and hour cases (back wages, penalties, child labor) | DOL Wage and Hour Division, via labordata's nightly `whisard.db` | enforcement_record | `labor_cases` | `python -m scripts.load_data dol` |
| Workplace safety (inspections, serious/willful/repeat violations, penalties, accident-driven inspections) | OSHA, via labordata's nightly `osha_enforcement.db` | enforcement_record | `osha_inspections` | `python -m scripts.load_data osha` |
| Right to organize (unfair labor practice charges with outcomes, union election petitions, certifications) | NLRB, via labordata's nightly `nlrb.db` | enforcement_record (charges are allegations) | `nlrb_cases` | `python -m scripts.load_data nlrb` |
| Ethics research, supply chains | Gemini (Person B / D) | cached research | `ethics_reports`, `supply_chains` | `python -m scripts.prewarm` |

Not built yet (waiting on the supply chain decision): ILAB child/forced labor goods list matched against
supply chain stage countries, Open Supply Hub factories.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/spending/summary` | adds `carbon` per merchant and `estimated_kg_co2e` in total |
| `GET /api/merchants/{id}/emissions` | `CompanyEmissions[]`, newest year first, every source |
| `GET /api/merchants/{id}/labor` | `LaborSummary` (`loaded: false` = never fetched, which is not "zero cases") |

## Labor data: storage, freshness, refresh

- **Source files:** [labordata](https://github.com/labordata) rebuilds SQLite copies of DOL Wage and Hour, OSHA,
  and NLRB data every night from the official agencies. No key and no rate limit (the DOL API rate-limits hard;
  `load_data dol --api` still exists as a fallback).
- **Storage:** files are downloaded to `backend/.cache/labordata/` (gitignored; OSHA is ~7 GB unzipped),
  queried, and deleted after the run (`--keep-cache` keeps them). Only matched records go to Atlas.
- **Window:** only the last two years are loaded and shown (`labordata.LOOKBACK_YEARS`).
- **Matching:** brand name patterns from `brand_sources.json` must match as whole words ("SHELL" does not match
  "BOMBSHELL"), plus the brand's NAICS prefixes for wages and OSHA. Cases include franchisees.
- **Refresh in the cloud:** `.github/workflows/nightly-data.yml` runs `load_data all` every night on GitHub's
  runner (needs the `MONGODB_URI` repository secret). Run it by hand from the Actions tab.

## Carbon estimate methodology

- Factor = kg CO2e per 2024 dollar, purchaser price ("with margins"), for the **product bought**, not the
  store (gas maps to petroleum refining, not gas stations).
- Gasoline adds tailpipe CO2: 8.887 kg/gallon (EPA) at $3.304/gallon (EIA 2024 average) = 2.69 kg/$.
- Spending is converted to 2024 dollars month by month with CPI-U before multiplying.
- Groceries use the median of NAICS 311 food manufacturing factors (low confidence).
- No estimate for Utilities, Education, Other: v1.4.0 has no electricity rows. No number beats a wrong one.
- Brand overrides (Starbucks -> coffee shop, Nike -> footwear) live in `brand_sources.json`.
- Always label it "estimated", with the factor label and data year from `CarbonEstimate`.

## Adding a company report

1. Find the report PDF, then: `python -m scripts.find_report_page <pdf-url>` prints the pages with an
   emissions table.
2. Add an entry to `backend/app/data/company_reports.json`: `brand_id`, `data_year`, `source_name`,
   `report_url`, `page`, and only the figures printed on that page (`scope1`, `scope2_market`,
   `scope2_location`, `scope12_market`, `scope3`, `total`). Do not add up numbers yourself.
3. `python -m scripts.load_data reports`. Each figure must appear on that page as a whole number, and
   subtotals must add up (1% tolerance), or the entry is rejected with the reason.

An LLM may draft entries in the same format; the verification step is what makes them safe to show.

Reports in millions or thousands of tonnes: set `"reported_unit": "MtCO2e"` (or `"ktCO2e"`) and enter
the figures exactly as printed; they are stored in tonnes.

Coverage (Nov 2026): 8 of 10 demo brands. Chipotle, Apple, Target, Amazon, H&M and Walmart are
pdf_verified; Trader Joe's is "not reported"; Shell has Climate TRACE. Gaps: Starbucks blocks automated
downloads (403), so its report cannot be machine-verified and is left out (we do not work around bot
blocking); Nike's PDF text fuses footnotes into numbers so they do not verify.

## Quality checks before the demo

```
python -m scripts.data_report      # one row per brand: what is real, what is still mock
```

`pipeline_runs` has one document per loader run (`written`, `rejected`, first 50 `errors`, `ok`).

## Caveats to show in the UI

- **Carbon estimate:** an industry average for the type of purchase, not the company's own footprint.
- **Climate TRACE:** counts 100% of a facility's emissions toward a majority owner.
- **DOL cases:** against locations and franchisees trading under the brand name; bigger chains have more
  locations, so compare carefully.

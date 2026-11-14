# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
General adults who care about their values and want to know whether the companies they shop from line up with them, and who are curious where the things they buy actually come from. They open Accountable to look back over their own recent card spending and see the companies, products, and supply chains behind it.

## Product Purpose
Accountable reads a user's (mock) Capital One transactions, groups them into the companies they pay, and shows sourced, factual information about what each company does and stands for, where its products are made and how they travel to the user, and which alternatives exist that the user can actually afford. Success: a user leaves knowing who their money went to and what that company's record is, with sources, and has a realistic option to shop differently if they want to.

## Positioning
Facts, not ratings. Accountable does not grade companies or tell users what is good or bad; it presents sourced information and lets the user's own values do the judging, so it never reads as greenwashing. It starts from the user's real spending (not a search box), connects each purchase to a mapped supply chain, and ties alternatives to what the user already spends, so suggestions stay within their means.

## Operating Context
- Built for a hackathon first (Capital One Nessie API); the 3-minute live demo to judges is the primary evaluation moment. Longevity is secondary.
- Data sources: Nessie (transactions and merchants), Gemini with Google Search grounding (company research and supply-chain stages), geocoding for map coordinates.
- Four-person team with file ownership (see docs/PLAN.md): A data/backend, B research engine, C frontend pages and UX, D map and alternatives.
- The demo must run fully on cached or mock data in case of network failure.

## Capabilities and Constraints
- Pages: Dashboard (spending summary, all-routes map tile, merchant tiles), Merchant detail (facts, suppliers, company map, alternatives), Map, Values/preferences, planned first-visit onboarding.
- **No scores in the UI.** The backend may compute personalized scores internally (ordering, highlighting), but no number, grade, or good/bad color is shown to users.
- **No political values.** Political spending/stances are out of scope for the product. The backend contract still lists a `political` category; removing it is a pending team decision (owners A/B).
- Every fact about a real company must show its source and a Verified/Unverified status; the LLM never sets "verified" (validation does). No unsourced claims.
- Affordability: alternatives are compared against what the user already spends at that merchant (from their transactions); income is not collected.
- `USE_MOCK_DATA=true` must always work with zero API keys.
- Stack is fixed by the existing codebase: FastAPI backend, React + Vite + TypeScript frontend.
- Undecided: final list of value topics after removing political; how alternatives' prices are sourced; which product to map for broad retailers (Amazon, Walmart).

## Evidence on Hand
- Mock data only: 10 merchants, about $2,068 of purchases, and one mock supply chain each (`backend/app/data/`). Ethics/values content there is placeholder text, not real research.
- No logos, testimonials, users, or real research results yet. Do not fabricate company facts, sources, prices, or claims in UI or copy.

## Product Principles
1. Show, don't judge: present sourced facts and company statements side by side; never rate a company.
2. Every claim earns its place with a visible source and verification status.
3. Start from the user's own money: spending is the entry point to everything else.
4. Alternatives must be realistic, both values-relevant and affordable at the user's current spend.
5. Demo-proof: every screen works offline on cached/mock data.

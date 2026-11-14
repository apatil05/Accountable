"""Feature 4 (starred on the whiteboard): validate LLM output before showing it.

Owner: Person B (Research engine).

Rule: the LLM proposes claims, this module decides whether they're validated.
Claims with no source are never validated.

TODO(Person B):
  - fetch source_url (httpx, short timeout), confirm 200 and that the page
    text mentions the merchant name
  - domain allow/block list (+ boost the user's trusted_sources)
  - optional second-pass "judge" call: give Gemini the fetched page text and
    ask whether it supports the claim (yes/no + quote)
  - lower CategoryScore.score confidence when most claims fail
"""

from ..models import CategoryScore


def validate_claims(merchant_name: str, categories: list[CategoryScore]) -> list[CategoryScore]:
    for cat in categories:
        for claim in cat.claims:
            claim.validated = bool(claim.source_url) and claim.confidence >= 0.6
    return categories

"""The bottom-line "does this company fit your values?" verdict shown when a
merchant is clicked. Owner: Person B (Research engine).

The label (aligns / mixed / against) is decided in scoring.verdict(), never by
the LLM. Gemini only writes the blurb explaining it, and only sees validated
claims. The template blurb is the fallback (mock mode, no key, or any error)
and uses category names and scores only, so it can never surface an
unsourced claim.
"""

import json
import logging

from pydantic import BaseModel

from .. import config
from ..models import CategoryScore, UserProfile, ValuesVerdict, VerdictLabel
from . import gemini, scoring

log = logging.getLogger(__name__)

# Mirrors CATEGORY_LABELS in frontend/src/api/types.ts.
CATEGORY_LABELS = {
    "labor": "Labor & workers",
    "environment": "Environment",
    "political": "Political spending",
    "animal_welfare": "Animal welfare",
    "community": "Local community",
    "privacy": "Data & privacy",
}


def _label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.replace("_", " ").capitalize())


def headline(name: str, label: VerdictLabel) -> str:
    return {
        "aligns": f"We think {name} aligns with your values",
        "mixed": f"{name} is a mixed fit for your values",
        "against": f"{name} goes against your values",
    }[label]


def _join(items: list[str]) -> str:
    return " and ".join(items) if len(items) <= 2 else ", ".join(items[:-1]) + f", and {items[-1]}"


def _fmt(c: CategoryScore) -> str:
    return f"{_label(c.category)} ({round(c.score)})"


def template_blurb(
    name: str,
    label: VerdictLabel,
    deal_breakers: list[str],
    categories: list[CategoryScore],
    profile: UserProfile,
    personalized_score: float,
) -> str:
    w = lambda c: profile.weights.get(c.category, 0)  # noqa: E731
    cared = [c for c in categories if w(c) > 0] or categories
    strengths = sorted((c for c in cared if c.score >= scoring.ALIGNS_AT), key=lambda c: (-w(c), -c.score))[:2]
    weaknesses = sorted((c for c in cared if c.score < scoring.MIXED_AT), key=lambda c: (-w(c), c.score))[:2]
    score = round(personalized_score)

    if label == "against" and deal_breakers:
        hits = [c for c in categories if c.category in deal_breakers]
        noun = "a deal-breaker" if len(hits) == 1 else "deal-breakers"
        return (
            f"{name} scores poorly on {_join([_fmt(c) for c in hits])}, which you marked as {noun}. "
            f"Its fit score across all your priorities is {score} out of 100."
        )
    if label == "aligns":
        lead = f"{name} scores {score} out of 100 on your priorities."
        if strengths:
            lead += f" It does well on what you weight most: {_join([_fmt(c) for c in strengths])}."
        if weaknesses:
            lead += f" Its weakest area is {_fmt(weaknesses[0])}."
        return lead
    if label == "mixed":
        lead = f"{name} scores {score} out of 100 on your priorities."
        good, bad = _join([_fmt(c) for c in strengths]), _join([_fmt(c) for c in weaknesses])
        if strengths and weaknesses:
            lead += f" It does well on {good} but falls short on {bad}."
        elif strengths:
            lead += f" It does well on {good}, but is middling on the rest."
        elif weaknesses:
            lead += f" It falls short on {bad}."
        else:
            lead += " It is middling across the areas you care about."
        return lead
    # against, no deal-breaker
    lead = f"{name} scores {score} out of 100 on your priorities."
    if weaknesses:
        lead += f" It falls short on what you weight most: {_join([_fmt(c) for c in weaknesses])}."
    return lead


class _BlurbOut(BaseModel):
    blurb: str


_BLURB_PROMPT = """You write a short explanation for a shopper about whether a company fits their personal values.

The verdict has ALREADY been decided by our scoring system: "{label}". Explain it; do not change, soften, or contradict it.
Headline the shopper sees above your text: "{headline}"

Rules:
- 2-3 sentences, plain text, second person ("you"), no markdown, no headline repeat.
- Use ONLY the facts in the data below. Do not add any fact, number, or claim that is not in it.
- Focus on the areas the shopper weights most (weight 0 = doesn't care, 5 = deal-breaker).
- If deal-breakers are listed, mention them first.
- Relate it to the shopper's own stances when they are relevant. Do not take a political side yourself.

Data:
{data}
"""


def _gemini_blurb(
    name: str,
    label: VerdictLabel,
    deal_breakers: list[str],
    categories: list[CategoryScore],
    profile: UserProfile,
    personalized_score: float,
) -> str:
    from google.genai import types

    data = {
        "company": name,
        "fit_score_for_this_shopper": round(personalized_score),
        "deal_breakers": [_label(c) for c in deal_breakers],
        "shopper_stances": profile.stances or "(none given)",
        "areas": [
            {
                "area": _label(c.category),
                "shopper_weight": profile.weights.get(c.category, 0),
                "score_0_to_100": round(c.score),
                "summary": c.summary,
                # Only validated claims may reach the user.
                "verified_facts": [cl.text for cl in c.claims if cl.validated],
            }
            for c in categories
        ],
    }
    resp = gemini.client().models.generate_content(
        model=config.GEMINI_MODEL,
        contents=_BLURB_PROMPT.format(
            label=label, headline=headline(name, label), data=json.dumps(data, indent=1)
        ),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_BlurbOut,
            temperature=0.3,
        ),
    )
    parsed = resp.parsed if isinstance(resp.parsed, _BlurbOut) else _BlurbOut.model_validate_json(resp.text)
    blurb = parsed.blurb.strip()
    if not blurb:
        raise ValueError("empty blurb")
    return blurb


def build_verdict(
    name: str,
    categories: list[CategoryScore],
    profile: UserProfile,
    personalized_score: float,
    use_ai: bool,
) -> ValuesVerdict:
    label, deal_breakers = scoring.verdict(categories, profile, personalized_score)
    args = (name, label, deal_breakers, categories, profile, personalized_score)
    blurb, source = template_blurb(*args), "template"
    if use_ai and gemini.enabled():
        try:
            blurb, source = _gemini_blurb(*args), "gemini"
        except Exception:
            log.exception("Gemini blurb failed for %s; using template", name)
    return ValuesVerdict(
        label=label,
        headline=headline(name, label),
        blurb=blurb,
        deal_breakers=deal_breakers,
        source=source,  # type: ignore[arg-type]
    )

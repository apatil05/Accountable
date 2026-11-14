"""API contract. Frontend types in frontend/src/api/types.ts mirror these.
Change a model here -> update types.ts in the same PR."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr

# The ethical dimensions a user can weigh. Keep in sync with the frontend.
EthicsCategory = Literal[
    "labor",
    "environment",
    "animal_welfare",
    "community",
    "privacy",
]
CATEGORIES: list[str] = list(EthicsCategory.__args__)  # type: ignore[attr-defined]


# ---------- Spending (Nessie) ----------
class Geocode(BaseModel):
    lat: float
    lng: float


class Merchant(BaseModel):
    id: str
    name: str
    category: list[str] = []
    geocode: Optional[Geocode] = None


class Purchase(BaseModel):
    id: str
    merchant_id: str
    amount: float
    purchase_date: str
    description: str = ""


class MerchantSummary(BaseModel):
    merchant_id: str
    name: str
    category: list[str]
    total_spent: float
    purchase_count: int
    last_purchase: str
    personalized_score: Optional[float] = None  # 0-100, None if not researched yet


class SpendingSummary(BaseModel):
    customer_id: str
    total_spent: float
    merchant_count: int
    merchants: list[MerchantSummary]


# ---------- User values ----------
class UserProfile(BaseModel):
    """Form -> JSON document (per the whiteboard)."""

    name: str = "Demo User"
    # 0 = don't care, 5 = deal-breaker
    weights: dict[str, int] = Field(default_factory=lambda: {c: 3 for c in CATEGORIES})
    stances: str = ""  # free-text, fed to the research engine as context
    prefer_local: bool = True


# ---------- Ethics research ----------
class Claim(BaseModel):
    text: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    confidence: float = 0.0  # 0-1, from the LLM
    validated: bool = False  # set by services/validation.py, never by the LLM
    # Internal only (not serialized), set by services/validation.py and used by scoring.category_score:
    _event_year: int = PrivateAttr(default=0)  # year of the event, 0 = unknown
    _direction: str = PrivateAttr(default="")  # "positive" / "negative" (neutral claims are dropped)
    _severity: str = PrivateAttr(default="")  # "minor" / "notable" / "major"
    _tier: int = PrivateAttr(default=0)  # source tier 1-4 (validation.domain_tier), 0 = unknown


class CategoryScore(BaseModel):
    category: str
    score: Optional[float] = None  # 0-100, higher = better; None = not enough verified evidence
    summary: str
    claims: list[Claim] = []


VerdictLabel = Literal["aligns", "mixed", "against"]


class ValuesVerdict(BaseModel):
    """Bottom line for the user. The label is computed by services/scoring.py,
    never by the LLM; Gemini may only write the blurb that explains it."""

    label: VerdictLabel
    headline: str  # fixed per label, e.g. "We think Starbucks aligns with your values"
    blurb: str  # 2-3 sentences
    deal_breakers: list[str] = []  # weight-5 categories that forced "against"
    source: Literal["gemini", "template"] = "template"


class EthicsReport(BaseModel):
    merchant_id: str
    merchant_name: str
    overall_score: float
    personalized_score: float
    categories: list[CategoryScore]
    verdict: ValuesVerdict
    data_source: Literal["mock", "gemini"] = "mock"
    researched_at: Optional[str] = None


# ---------- Supply chain map ----------
class SupplyChainStage(BaseModel):
    order: int
    stage: str  # e.g. "Raw materials", "Manufacturing"
    place: str  # human-readable location
    lat: float
    lng: float
    description: str = ""


class SupplyChain(BaseModel):
    merchant_id: str
    product: str
    stages: list[SupplyChainStage]
    data_source: Literal["mock", "gemini"] = "mock"


# ---------- Alternatives ----------
class Alternative(BaseModel):
    name: str
    reason: str
    is_local: bool = False
    estimated_score: Optional[float] = None
    url: Optional[str] = None

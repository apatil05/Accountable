"""API contract. Frontend types in frontend/src/api/types.ts mirror these.
Change a model here -> update types.ts in the same PR."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

# The ethical dimensions a user can weigh. Keep in sync with the frontend.
EthicsCategory = Literal[
    "labor",
    "environment",
    "political",
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

    name: str = Field("Demo User", max_length=100)
    # 0 = don't care, 5 = deal-breaker
    weights: dict[str, int] = Field(default_factory=lambda: {c: 3 for c in CATEGORIES})
    stances: str = Field("", max_length=2000)  # free-text, fed to the research engine as context
    trusted_sources: list[str] = Field(default_factory=list, max_length=20)
    prefer_local: bool = True

    @field_validator("weights")
    @classmethod
    def _weights_are_known_and_in_range(cls, weights: dict[str, int]) -> dict[str, int]:
        unknown = sorted(set(weights) - set(CATEGORIES))
        if unknown:
            raise ValueError(f"unknown categories: {', '.join(unknown)}")
        if any(not 0 <= w <= 5 for w in weights.values()):
            raise ValueError("weights must be between 0 and 5")
        return weights

    @field_validator("trusted_sources")
    @classmethod
    def _sources_are_short(cls, sources: list[str]) -> list[str]:
        if any(len(s) > 200 for s in sources):
            raise ValueError("each trusted source must be 200 characters or fewer")
        return sources


# ---------- Ethics research ----------
class Claim(BaseModel):
    text: str
    source_url: Optional[str] = None
    source_name: Optional[str] = None
    confidence: float = 0.0  # 0-1, from the LLM
    validated: bool = False  # set by services/validation.py, never by the LLM


class CategoryScore(BaseModel):
    category: str
    score: float  # 0-100, higher = better
    summary: str
    claims: list[Claim] = []


class EthicsReport(BaseModel):
    merchant_id: str
    merchant_name: str
    overall_score: float
    personalized_score: float
    categories: list[CategoryScore]
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

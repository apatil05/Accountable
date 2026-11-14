"""API contract. Frontend types in frontend/src/api/types.ts mirror these.
Change a model here -> update types.ts in the same PR."""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

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


class CarbonEstimate(BaseModel):
    """Spend-based estimate: dollars spent x a US industry-average emission factor."""

    kg_co2e: float
    factor_id: str
    factor_label: str  # e.g. "Limited-service restaurants (NAICS 722513)"
    confidence: Literal["high", "medium", "low"]
    data_year: int  # year of the emissions data behind the factor
    includes_use_phase: bool = False  # True for fuel: burning it is included


class MerchantSummary(BaseModel):
    merchant_id: str
    name: str
    category: list[str]
    total_spent: float
    purchase_count: int
    last_purchase: str
    personalized_score: Optional[float] = None  # 0-100, None if not researched yet
    carbon: Optional[CarbonEstimate] = None  # None when no factor fits this merchant


class SpendingSummary(BaseModel):
    customer_id: str
    total_spent: float
    merchant_count: int
    merchants: list[MerchantSummary]
    estimated_kg_co2e: Optional[float] = None  # sum over merchants that have an estimate


# ---------- Provenance: where every fact came from ----------
MIN_DATA_YEAR = 1990
MAX_DATA_YEAR = 2100

SourceName = Literal["company_report", "climate_trace", "dol_whd", "epa_useeio", "manual"]


class Provenance(BaseModel):
    source: SourceName
    source_name: str = Field(max_length=300)  # human-readable, shown in the UI
    source_url: Optional[str] = Field(None, max_length=2000)
    locator: Optional[str] = Field(None, max_length=200)  # "p. 86", a case id, an asset id
    data_year: int = Field(ge=MIN_DATA_YEAR, le=MAX_DATA_YEAR)  # the year the fact describes
    retrieved_at: datetime
    method: Literal["api", "download", "pdf_verified", "manual"]
    kind: Literal["reported", "estimated", "enforcement_record"]
    license: Optional[str] = None
    sha256: Optional[str] = Field(None, pattern=r"^[0-9a-f]{64}$")  # of the source file, to detect edits

    @field_validator("source_url")
    @classmethod
    def _https_only(cls, url: Optional[str]) -> Optional[str]:
        if url is not None and not url.startswith("https://"):
            raise ValueError("source_url must be an https:// URL")
        return url


# ---------- Company emissions ----------
CONSISTENCY_TOLERANCE = 0.01  # reported subtotals may differ from their parts by rounding only


def _close(expected: float, actual: float) -> bool:
    return abs(expected - actual) <= CONSISTENCY_TOLERANCE * max(abs(expected), 1.0)


class EmissionsFigures(BaseModel):
    """Tonnes of CO2e. None = not disclosed, never zero-filled."""

    scope1: Optional[float] = Field(None, ge=0)
    scope2_market: Optional[float] = Field(None, ge=0)
    scope2_location: Optional[float] = Field(None, ge=0)
    scope12_market: Optional[float] = Field(None, ge=0)
    scope3: Optional[float] = Field(None, ge=0)
    total: Optional[float] = Field(None, ge=0)  # scope 1 + 2 (market) + 3
    owned_assets: Optional[float] = Field(None, ge=0)  # Climate TRACE: direct emissions of owned facilities

    @model_validator(mode="after")
    def _parts_add_up(self) -> "EmissionsFigures":
        if None not in (self.scope1, self.scope2_market, self.scope12_market):
            if not _close(self.scope12_market, self.scope1 + self.scope2_market):
                raise ValueError("scope 1 + scope 2 (market) does not match the reported scope 1+2 total")
        if None not in (self.scope12_market, self.scope3, self.total):
            if not _close(self.total, self.scope12_market + self.scope3):
                raise ValueError("total does not match scope 1+2 (market) + scope 3")
        return self

    def has_any(self) -> bool:
        return any(v is not None for v in self.model_dump().values())


class CompanyEmissions(BaseModel):
    brand_id: str = Field(min_length=1, max_length=100)
    data_year: int = Field(ge=MIN_DATA_YEAR, le=MAX_DATA_YEAR)
    status: Literal["reported", "not_reported"]
    reason: Optional[str] = Field(None, max_length=300)  # why nothing is reported
    unit: Literal["tCO2e"] = "tCO2e"
    figures: Optional[EmissionsFigures] = None
    provenance: Provenance

    @model_validator(mode="after")
    def _status_matches_content(self) -> "CompanyEmissions":
        if self.status == "reported" and not (self.figures and self.figures.has_any()):
            raise ValueError("a reported entry needs at least one figure")
        if self.status == "not_reported" and not self.reason:
            raise ValueError("a not_reported entry needs a reason")
        if self.provenance.data_year != self.data_year:
            raise ValueError("data_year and provenance.data_year disagree")
        return self


# ---------- Labor (US DOL Wage and Hour enforcement) ----------
class LaborCase(BaseModel):
    case_id: str = Field(min_length=1, max_length=40)
    brand_id: str
    trade_name: str = ""
    legal_name: str = ""
    state: Optional[str] = Field(None, max_length=2)
    naics: Optional[str] = None
    findings_start: Optional[date] = None
    findings_end: Optional[date] = None
    violations: int = Field(0, ge=0)
    employees_affected: int = Field(0, ge=0)
    back_wages_cents: int = Field(0, ge=0)
    penalties_cents: int = Field(0, ge=0)
    child_labor_violations: Optional[int] = Field(None, ge=0)  # None when the field is absent
    provenance: Provenance


class LaborSummary(BaseModel):
    """Facts only: counts and dollar totals of concluded DOL Wage and Hour cases."""

    merchant_id: str
    loaded: bool  # False = never fetched for this brand (different from zero cases)
    case_count: int = 0
    violations: int = 0
    employees_affected: int = 0
    back_wages: float = 0.0
    penalties: float = 0.0
    child_labor_cases: int = 0
    earliest_finding: Optional[str] = None
    latest_finding: Optional[str] = None
    source_name: str = "U.S. Department of Labor, Wage and Hour Division enforcement data"
    source_url: str = "https://enforcedata.dol.gov/"
    note: str = (
        "Cases against businesses trading under this brand's name, including franchisees "
        "and individual locations."
    )


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


# ---------- Real suppliers (ImportYeti) + nearby places (Wikipedia) ----------
class NearbyPlace(BaseModel):
    """A Wikipedia article about a place near a coordinate, to learn about where something comes from."""

    title: str
    url: str
    summary: Optional[str] = None
    thumbnail_url: Optional[str] = None
    distance_km: Optional[float] = None
    source: Literal["wikipedia"] = "wikipedia"


class Supplier(BaseModel):
    name: str
    address: Optional[str] = None
    country: Optional[str] = None  # English country name, from geocoding the listed address
    country_code: Optional[str] = None  # ISO 3166-1 alpha-3 of that same geocoded country (e.g. "CHN")
    shipments: Optional[int] = None
    lat: float  # approximate: geocoded from the supplier's listed address
    lng: float
    place: Optional[NearbyPlace] = None


class Importer(BaseModel):
    """The company's own importing entity in US records: where its suppliers' shipments go."""

    name: str
    address: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    place: Optional[NearbyPlace] = None  # Wikipedia article about where the importer is


class SupplierReport(BaseModel):
    merchant_id: str
    company_name: Optional[str] = None  # the importer record used; None = no trusted import records
    importer: Optional[Importer] = None
    suppliers: list[Supplier]
    source: Literal["importyeti"] = "importyeti"
    fetched_at: str

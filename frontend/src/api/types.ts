// Mirrors backend/app/models.py. Change both in the same PR.

export const CATEGORIES = [
  "labor",
  "environment",
  "animal_welfare",
  "community",
  "privacy",
] as const;
export type EthicsCategory = (typeof CATEGORIES)[number];

export const CATEGORY_LABELS: Record<EthicsCategory, string> = {
  labor: "Labor & workers",
  environment: "Environment",
  animal_welfare: "Animal welfare",
  community: "Local community",
  privacy: "Data & privacy",
};

/** Spend-based estimate: dollars spent x a US industry-average factor. Label it "estimated". */
export interface CarbonEstimate {
  kg_co2e: number;
  factor_id: string;
  factor_label: string;
  confidence: "high" | "medium" | "low";
  data_year: number;
  includes_use_phase: boolean;
}

export interface MerchantSummary {
  merchant_id: string;
  name: string;
  category: string[];
  total_spent: number;
  purchase_count: number;
  last_purchase: string;
  personalized_score: number | null;
  carbon: CarbonEstimate | null;
}

export interface SpendingSummary {
  customer_id: string;
  total_spent: number;
  merchant_count: number;
  merchants: MerchantSummary[];
  estimated_kg_co2e: number | null;
  /** Where this customer's purchases came from: "mock" (sample data) or "nessie" (live Capital One API). */
  data_source: string | null;
}

/** A logged-in account. Each account is linked to one customer's purchases. */
export interface AuthUser {
  email: string;
  name: string;
  customer_id: string;
}

export interface LoginResponse {
  token: string;
  user: AuthUser;
}

/** Where a fact came from. Show source_name, locator and data_year next to every number. */
export interface Provenance {
  source: "company_report" | "climate_trace" | "dol_whd" | "epa_useeio" | "manual" | "osha" | "nlrb";
  source_name: string;
  source_url: string | null;
  locator: string | null;
  data_year: number;
  retrieved_at: string;
  method: "api" | "download" | "pdf_verified" | "manual";
  kind: "reported" | "estimated" | "enforcement_record";
  license: string | null;
  sha256: string | null;
}

/** Tonnes CO2e. null = not disclosed (never treat as zero). */
export interface EmissionsFigures {
  scope1: number | null;
  scope2_market: number | null;
  scope2_location: number | null;
  scope12_market: number | null;
  scope3: number | null;
  total: number | null;
  owned_assets: number | null;
}

export interface CompanyEmissions {
  brand_id: string;
  data_year: number;
  status: "reported" | "not_reported";
  reason: string | null;
  unit: "tCO2e";
  figures: EmissionsFigures | null;
  provenance: Provenance;
}

/** OSHA inspections opened in the window. loaded=false means never checked. */
export interface SafetySummary {
  loaded: boolean;
  inspections: number;
  after_accident: number;
  serious: number;
  willful: number;
  repeat: number;
  penalties: number;
  source_name: string;
  source_url: string;
}

/** NLRB charges against the employer (allegations, with outcomes) and union election petitions. */
export interface OrganizingSummary {
  loaded: boolean;
  charges: number;
  charges_open: number;
  charges_remedied: number;
  charges_dismissed: number;
  top_allegations: string[];
  petitions: number;
  unions_certified: number;
  elections_union_lost: number;
  decertification_petitions: number;
  source_name: string;
  source_url: string;
}

/** Last two years (from window_start): wage cases, workplace safety, right to organize.
 *  loaded=false means never checked (not the same as zero). */
export interface LaborSummary {
  merchant_id: string;
  window_start: string | null;
  safety: SafetySummary | null;
  organizing: OrganizingSummary | null;
  loaded: boolean;
  case_count: number;
  violations: number;
  employees_affected: number;
  back_wages: number;
  penalties: number;
  child_labor_cases: number;
  earliest_finding: string | null;
  latest_finding: string | null;
  source_name: string;
  source_url: string;
  note: string;
}

export interface UserProfile {
  name: string;
  weights: Record<string, number>;
  stances: string;
  prefer_local: boolean;
}

export interface Claim {
  text: string;
  source_url: string | null;
  source_name: string | null;
  confidence: number;
  validated: boolean;
  // 1 government/court, 2 news/ratings, 3 other, 4 the company itself; null = no source
  source_tier: number | null;
}

export interface CategoryScore {
  category: EthicsCategory;
  score: number | null; // null = not enough verified evidence
  summary: string;
  claims: Claim[];
}

export type VerdictLabel = "aligns" | "mixed" | "against";

export interface ValuesVerdict {
  label: VerdictLabel;
  headline: string;
  blurb: string;
  deal_breakers: string[];
  source: "gemini" | "template";
}

export interface EthicsReport {
  merchant_id: string;
  merchant_name: string;
  overall_score: number;
  personalized_score: number;
  categories: CategoryScore[];
  verdict: ValuesVerdict;
  data_source: "mock" | "gemini";
  researched_at: string | null;
}

export interface SupplyChainStage {
  order: number;
  stage: string;
  place: string;
  lat: number;
  lng: number;
  description: string;
}

export interface SupplyChain {
  merchant_id: string;
  product: string;
  stages: SupplyChainStage[];
  data_source: "mock" | "gemini";
}

export interface Alternative {
  name: string;
  reason: string;
  is_local: boolean;
  estimated_score: number | null;
  url: string | null;
}

// Real suppliers (ImportYeti) + nearby places (Wikipedia). Mirrors models.NearbyPlace / Supplier / SupplierReport.
export interface NearbyPlace {
  title: string;
  url: string;
  summary: string | null;
  thumbnail_url: string | null;
  distance_km: number | null;
  source: "wikipedia";
}

export interface Supplier {
  name: string;
  address: string | null;
  country: string | null; // from geocoding the supplier's listed address
  country_code: string | null; // ISO alpha-3 of that country, e.g. "CHN"
  shipments: number | null;
  lat: number; // approximate: geocoded from the supplier's listed address
  lng: number;
  place: NearbyPlace | null; // the town/district it is in
  nearby?: NearbyPlace[]; // other Wikipedia articles close to the geocoded point, nearest first
}

/** The company's own importing entity in US records: where its suppliers' shipments go. */
export interface Importer {
  name: string;
  address: string | null;
  lat: number | null;
  lng: number | null;
  place: NearbyPlace | null; // Wikipedia article about where the importer is
  nearby?: NearbyPlace[]; // other Wikipedia articles close to the importer, nearest first
}

export interface SupplierReport {
  merchant_id: string;
  company_name: string | null; // importer record used; null = no trusted import records
  importer: Importer | null;
  suppliers: Supplier[];
  source: "importyeti";
  fetched_at: string;
}

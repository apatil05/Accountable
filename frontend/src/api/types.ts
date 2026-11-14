// Mirrors backend/app/models.py. Change both in the same PR.

export const CATEGORIES = [
  "labor",
  "environment",
  "political",
  "animal_welfare",
  "community",
  "privacy",
] as const;
export type EthicsCategory = (typeof CATEGORIES)[number];

export const CATEGORY_LABELS: Record<EthicsCategory, string> = {
  labor: "Labor & workers",
  environment: "Environment",
  political: "Political spending",
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
}

/** Where a fact came from. Show source_name, locator and data_year next to every number. */
export interface Provenance {
  source: "company_report" | "climate_trace" | "dol_whd" | "epa_useeio" | "manual";
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

/** Concluded US Department of Labor wage and hour cases. loaded=false means never fetched. */
export interface LaborSummary {
  merchant_id: string;
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
  trusted_sources: string[];
  prefer_local: boolean;
}

export interface Claim {
  text: string;
  source_url: string | null;
  source_name: string | null;
  confidence: number;
  validated: boolean;
}

export interface CategoryScore {
  category: EthicsCategory;
  score: number;
  summary: string;
  claims: Claim[];
}

export interface EthicsReport {
  merchant_id: string;
  merchant_name: string;
  overall_score: number;
  personalized_score: number;
  categories: CategoryScore[];
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

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

export interface MerchantSummary {
  merchant_id: string;
  name: string;
  category: string[];
  total_spent: number;
  purchase_count: number;
  last_purchase: string;
  personalized_score: number | null;
}

export interface SpendingSummary {
  customer_id: string;
  total_spent: number;
  merchant_count: number;
  merchants: MerchantSummary[];
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

// Carbon and emissions helpers for the dashboard and company pages. Facts only, never scores.
// Owner: Person A. Data contract: docs/DATA.md. Pure functions so they can be unit-tested.
import type { CompanyEmissions, EmissionsFigures, MerchantSummary } from "../api/types";

const KG_PER_TONNE = 1000;
const MILLION = 1_000_000;

/** "Shell's", "Trader Joe's" (not "Trader Joe's's"), "Target's". */
export function possessive(name: string): string {
  if (/'s$/i.test(name)) return name;
  return /s$/i.test(name) ? `${name}'` : `${name}'s`;
}

/** Estimated kilograms, switching to tonnes past one tonne: "13 kg", "1.04 t". */
export function formatKg(kg: number): string {
  if (kg < KG_PER_TONNE) return `${Math.round(kg).toLocaleString("en-US")} kg`;
  return `${(kg / KG_PER_TONNE).toLocaleString("en-US", { maximumFractionDigits: 2 })} t`;
}

/** Company-scale tonnes: exact below a million, then "5.07 million t". */
export function formatTonnes(t: number): string {
  if (t < MILLION) return `${Math.round(t).toLocaleString("en-US")} t`;
  return `${(t / MILLION).toLocaleString("en-US", { maximumFractionDigits: 2 })} million t`;
}

export interface FootprintLead {
  merchant: MerchantSummary;
  spendShare: number; // of spending that has an estimate
  footprintShare: number; // of the estimated footprint
}

/** The merchant whose share of your estimated footprint most exceeds its share of your spending. */
export function footprintLead(merchants: MerchantSummary[]): FootprintLead | null {
  const estimated = merchants.filter((m) => m.carbon);
  const spend = estimated.reduce((a, m) => a + m.total_spent, 0);
  const kg = estimated.reduce((a, m) => a + (m.carbon?.kg_co2e ?? 0), 0);
  if (!spend || !kg) return null;
  let best: FootprintLead | null = null;
  for (const m of estimated) {
    const lead = { merchant: m, spendShare: m.total_spent / spend, footprintShare: (m.carbon?.kg_co2e ?? 0) / kg };
    const gap = lead.footprintShare - lead.spendShare;
    if (gap > 0 && (!best || gap > best.footprintShare - best.spendShare)) best = lead;
  }
  return best;
}

// Broadest figure first. Never computed by us: each comes printed in the source.
const HEADLINE_ORDER: [keyof EmissionsFigures, string][] = [
  ["total", "Total, scopes 1–3"],
  ["scope12_market", "Scopes 1 + 2"],
  ["scope3", "Scope 3 (value chain)"],
  ["scope1", "Scope 1 (direct)"],
  ["owned_assets", "Owned facilities"],
];

export const FIGURE_LABELS: Record<keyof EmissionsFigures, string> = {
  scope1: "Scope 1, direct operations",
  scope2_market: "Scope 2, purchased energy",
  scope2_location: "Scope 2, location-based",
  scope12_market: "Scopes 1 + 2",
  scope3: "Scope 3, suppliers and products",
  total: "Total, scopes 1–3",
  owned_assets: "Owned facilities",
};

export function headlineFigure(f: EmissionsFigures): { key: keyof EmissionsFigures; label: string; tonnes: number } | null {
  for (const [key, label] of HEADLINE_ORDER) {
    const v = f[key];
    if (v !== null && v !== undefined) return { key, label, tonnes: v };
  }
  return null;
}

const newest = (docs: CompanyEmissions[]) =>
  docs.reduce<CompanyEmissions | undefined>((a, d) => (!a || d.data_year > a.data_year ? d : a), undefined);

/** Newest company-reported entry (or "doesn't report") and newest independent estimate, kept apart. */
export function pickEmissions(docs: CompanyEmissions[]): { reported?: CompanyEmissions; independent?: CompanyEmissions } {
  const own = docs.filter((d) => d.provenance.source === "company_report" || d.provenance.source === "manual");
  const independent = docs.filter((d) => d.provenance.source === "climate_trace");
  return { reported: newest(own), independent: newest(independent) };
}

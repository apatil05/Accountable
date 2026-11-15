// Dashboard insights, computed only from real data the API returns
// (/spending/summary + /suppliers import records). Facts only, never scores. Owner: Person C.
import type { MerchantSummary, SupplierReport } from "../api/types";
import { isPlaced } from "./suppliers";

export type Continent = "Asia" | "South America" | "North America" | "Africa" | "Europe" | "Oceania";

// Coarse continent lookup from a coordinate. Good enough to group example origins; not a geocoder.
export function continentOf(lat: number, lng: number): Continent {
  if (lat < 13 && lng > -82 && lng < -34) return "South America";
  if (lng < -50) return "North America";
  if (lat < -10 && lng > 110) return "Oceania";
  if (lat < 37 && lat > -36 && lng > -20 && lng < 52 && !(lat > 12 && lng > 34)) return "Africa";
  if (lat > 36 && lng > -25 && lng < 30) return "Europe";
  return "Asia";
}

export interface ContinentShare {
  continent: Continent;
  amount: number;
  share: number;
  merchantIds: string[];
}

export interface WhereItStarts {
  continents: ContinentShare[]; // most money first
  abroadAmount: number;
  abroadShare: number;
  abroadIds: string[]; // companies with at least one supplier outside the US
  countries: string[]; // supplier countries (ISO codes)
  coveredTotal: number; // spend at companies that have import records
}

/**
 * Where your spending traces to, from real import records. Each company's spend is split across its top
 * suppliers by their shipment counts (equal split when counts are missing), then grouped by the supplier's
 * continent and by whether it's outside the US.
 */
export function whereItStarts(merchants: MerchantSummary[], reports: SupplierReport[]): WhereItStarts {
  const byId = new Map(reports.map((r) => [r.merchant_id, r]));
  const groups = new Map<Continent, { amount: number; ids: Set<string> }>();
  const countries = new Set<string>();
  const abroadIds = new Set<string>();
  let abroadAmount = 0;
  let coveredTotal = 0;

  for (const m of merchants) {
    const sups = (byId.get(m.merchant_id)?.suppliers ?? []).filter(isPlaced); // continents need a location
    if (!sups.length) continue;
    coveredTotal += m.total_spent;
    const weights = sups.map((s) => (s.shipments && s.shipments > 0 ? s.shipments : 1));
    const total = weights.reduce((a, b) => a + b, 0);
    sups.forEach((s, i) => {
      const amount = (m.total_spent * weights[i]) / total;
      const c = continentOf(s.lat, s.lng);
      const g = groups.get(c) ?? { amount: 0, ids: new Set<string>() };
      g.amount += amount;
      g.ids.add(m.merchant_id);
      groups.set(c, g);
      if (s.country) countries.add(s.country);
      if (s.country_code !== "USA") {
        abroadAmount += amount;
        abroadIds.add(m.merchant_id);
      }
    });
  }

  const continents = [...groups.entries()]
    .map(([continent, g]) => ({ continent, amount: g.amount, share: coveredTotal ? g.amount / coveredTotal : 0, merchantIds: [...g.ids] }))
    .sort((a, b) => b.amount - a.amount);

  return {
    continents,
    abroadAmount,
    abroadShare: coveredTotal ? abroadAmount / coveredTotal : 0,
    abroadIds: [...abroadIds],
    countries: [...countries],
    coveredTotal,
  };
}

export interface Habits {
  purchases: number;
  avgPurchase: number;
  mostFrequent: MerchantSummary | null;
  biggest: MerchantSummary | null;
}

export function habits(merchants: MerchantSummary[], total: number): Habits {
  const purchases = merchants.reduce((a, m) => a + m.purchase_count, 0);
  const mostFrequent = merchants.reduce<MerchantSummary | null>(
    (best, m) => (!best || m.purchase_count > best.purchase_count ? m : best),
    null,
  );
  const biggest = merchants.reduce<MerchantSummary | null>(
    (best, m) => (!best || m.total_spent > best.total_spent ? m : best),
    null,
  );
  return { purchases, avgPurchase: purchases ? total / purchases : 0, mostFrequent, biggest };
}

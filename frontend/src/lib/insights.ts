// Dashboard insights, computed only from data the API already returns
// (/spending/summary + /supply-chains). Facts only, never scores. Owner: Person C.
import type { MerchantSummary, SupplyChain } from "../api/types";

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

// Contiguous US bounding box. Origins inside count as "starts in the US".
const inUS = (lat: number, lng: number) => lat > 24 && lat < 50 && lng > -125 && lng < -66;

export interface ContinentShare {
  continent: Continent;
  amount: number;
  share: number;
  merchantIds: string[];
  /** A representative point to turn the globe to (spend-weighted centroid of origins). */
  center: [number, number];
}

export interface WhereItStarts {
  continents: ContinentShare[]; // most money first
  abroadAmount: number;
  abroadShare: number;
  abroadIds: string[];
  coveredTotal: number; // spend that has an example route at all
}

export function whereItStarts(merchants: MerchantSummary[], chains: SupplyChain[]): WhereItStarts {
  const byId = new Map(chains.map((c) => [c.merchant_id, c]));
  const groups = new Map<Continent, { amount: number; ids: string[]; lat: number; lng: number }>();
  let abroadAmount = 0;
  let coveredTotal = 0;
  const abroadIds: string[] = [];

  for (const m of merchants) {
    const origin = byId.get(m.merchant_id)?.stages[0];
    if (!origin) continue;
    coveredTotal += m.total_spent;
    const c = continentOf(origin.lat, origin.lng);
    const g = groups.get(c) ?? { amount: 0, ids: [], lat: 0, lng: 0 };
    g.amount += m.total_spent;
    g.ids.push(m.merchant_id);
    g.lat += origin.lat * m.total_spent;
    g.lng += origin.lng * m.total_spent;
    groups.set(c, g);
    if (!inUS(origin.lat, origin.lng)) {
      abroadAmount += m.total_spent;
      abroadIds.push(m.merchant_id);
    }
  }

  const continents = [...groups.entries()]
    .map(([continent, g]) => ({
      continent,
      amount: g.amount,
      share: coveredTotal ? g.amount / coveredTotal : 0,
      merchantIds: g.ids,
      center: [g.lng / g.amount, g.lat / g.amount] as [number, number],
    }))
    .sort((a, b) => b.amount - a.amount);

  return {
    continents,
    abroadAmount,
    abroadShare: coveredTotal ? abroadAmount / coveredTotal : 0,
    abroadIds,
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

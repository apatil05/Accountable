import { describe, expect, it } from "vitest";
import type { CompanyEmissions, MerchantSummary } from "../api/types";
import { footprintLead, formatKg, formatTonnes, headlineFigure, pickEmissions, possessive } from "./footprint";

const merchant = (id: string, spent: number, kg: number | null): MerchantSummary => ({
  merchant_id: id,
  name: id,
  category: [],
  total_spent: spent,
  purchase_count: 1,
  last_purchase: "2026-11-01",
  personalized_score: null,
  carbon: kg === null ? null : {
    kg_co2e: kg, factor_id: "f", factor_label: "F", confidence: "medium", data_year: 2023, includes_use_phase: false,
  },
});

const doc = (source: CompanyEmissions["provenance"]["source"], year: number, status: CompanyEmissions["status"] = "reported"): CompanyEmissions => ({
  brand_id: "b",
  data_year: year,
  status,
  reason: status === "not_reported" ? "Private" : null,
  unit: "tCO2e",
  figures: status === "not_reported" ? null : {
    scope1: 1, scope2_market: null, scope2_location: null, scope12_market: null, scope3: null, total: null, owned_assets: null,
  },
  provenance: {
    source, source_name: "S", source_url: null, locator: null, data_year: year, retrieved_at: "", method: "api",
    kind: "reported", license: null, sha256: null,
  },
});

describe("possessive", () => {
  it("never doubles an apostrophe-s", () => {
    expect(possessive("Shell")).toBe("Shell's");
    expect(possessive("Trader Joe's")).toBe("Trader Joe's");
    expect(possessive("Walgreens")).toBe("Walgreens'");
  });
});

describe("formatKg", () => {
  it("shows kilograms below a tonne and tonnes above", () => {
    expect(formatKg(12.88)).toBe("13 kg");
    expect(formatKg(1041.5)).toBe("1.04 t");
  });
});

describe("formatTonnes", () => {
  it("keeps small totals exact and abbreviates large ones", () => {
    expect(formatTonnes(44372)).toBe("44,372 t");
    expect(formatTonnes(5069572)).toBe("5.07 million t");
    expect(formatTonnes(80850000)).toBe("80.85 million t");
  });
});

describe("footprintLead", () => {
  it("finds the merchant whose share of emissions most exceeds its share of spending", () => {
    const ms = [merchant("amazon", 400, 60), merchant("shell", 250, 730), merchant("local", 100, null)];
    const lead = footprintLead(ms);
    expect(lead?.merchant.merchant_id).toBe("shell");
    expect(lead?.footprintShare).toBeCloseTo(730 / 790);
    expect(lead?.spendShare).toBeCloseTo(250 / 650); // only spending that has an estimate
  });

  it("returns null without estimates or without a merchant that outweighs its spend", () => {
    expect(footprintLead([merchant("a", 10, null)])).toBeNull();
    expect(footprintLead([merchant("a", 10, 5), merchant("b", 10, 5)])).toBeNull();
  });
});

describe("headlineFigure", () => {
  it("prefers the total, then the broadest figure printed", () => {
    expect(headlineFigure({ scope1: 1, scope2_market: 2, scope2_location: null, scope12_market: 3, scope3: 9, total: 12, owned_assets: null }))
      .toEqual({ key: "total", label: "Total, scopes 1–3", tonnes: 12 });
    expect(headlineFigure({ scope1: 1, scope2_market: 2, scope2_location: null, scope12_market: null, scope3: null, total: null, owned_assets: null }))
      .toEqual({ key: "scope1", label: "Scope 1 (direct)", tonnes: 1 });
    expect(headlineFigure({ scope1: null, scope2_market: null, scope2_location: null, scope12_market: null, scope3: null, total: null, owned_assets: 7 }))
      .toEqual({ key: "owned_assets", label: "Owned facilities", tonnes: 7 });
  });
});

describe("pickEmissions", () => {
  it("takes the newest company-reported entry and the newest independent estimate separately", () => {
    const picked = pickEmissions([doc("company_report", 2024), doc("company_report", 2025), doc("climate_trace", 2025)]);
    expect(picked.reported?.data_year).toBe(2025);
    expect(picked.independent?.provenance.source).toBe("climate_trace");
  });

  it("treats a manual not-reported entry as the company's reporting status", () => {
    expect(pickEmissions([doc("manual", 2024, "not_reported")]).reported?.status).toBe("not_reported");
  });
});

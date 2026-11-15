// Real supplier data (ImportYeti import records) shaped for the globe and for insights. Owner: Person C.
import type { Supplier, SupplierReport } from "../api/types";
import type { GlobePoint, GlobeRoute } from "../components/Globe";

export const supplierPointId = (merchantId: string, i: number) => `${merchantId}:${i}`;
/** "company:index" / "company:importer" -> [company, part]. Splits on the last colon: looked-up companies have ids like "lookup:ikea". */
export function splitPointId(id: string): [string, string] {
  const at = id.lastIndexOf(":");
  return [id.slice(0, at), id.slice(at + 1)];
}
export const isLookup = (merchantId: string) => merchantId.startsWith("lookup:");
/** A supplier the records let us place on the map (some are listed without a usable address). */
export const isPlaced = (s: Supplier): s is Supplier & { lat: number; lng: number } => s.lat != null && s.lng != null;

/** Lines from each supplier to the company's US importer, plus a marker for every supplier and importer. */
export function toGlobe(reports: SupplierReport[], names: Record<string, string> = {}) {
  const routes: GlobeRoute[] = [];
  const points: GlobePoint[] = [];
  for (const r of reports) {
    const imp = r.importer && r.importer.lat != null && r.importer.lng != null ? r.importer : null;
    r.suppliers.forEach((s, i) => {
      if (!isPlaced(s)) return; // listed in the panels, but no pin without a placeable address
      points.push({ id: supplierPointId(r.merchant_id, i), group: r.merchant_id, lat: s.lat, lng: s.lng, kind: "supplier", label: s.name });
      if (imp) routes.push({ group: r.merchant_id, from: [s.lng, s.lat], to: [imp.lng!, imp.lat!] });
    });
    if (imp && r.suppliers.some(isPlaced)) {
      points.push({
        id: `${r.merchant_id}:importer`, group: r.merchant_id, lat: imp.lat!, lng: imp.lng!, kind: "importer",
        label: `${names[r.merchant_id] ?? imp.name} · US importer`,
      });
    }
  }
  return { routes, points };
}

/** Center of a set of points on the sphere, so they all turn onto the visible face. */
export function sphericalCenter(pts: { lat: number; lng: number }[]): [number, number] | null {
  if (!pts.length) return null;
  let x = 0, y = 0, z = 0;
  for (const p of pts) {
    const l = (p.lng * Math.PI) / 180, t = (p.lat * Math.PI) / 180;
    x += Math.cos(t) * Math.cos(l);
    y += Math.cos(t) * Math.sin(l);
    z += Math.sin(t);
  }
  return [(Math.atan2(y, x) * 180) / Math.PI, (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI];
}

/** Everything one company's import records cover: its suppliers and its importer. */
export function reportPoints(r?: SupplierReport | null): { lat: number; lng: number }[] {
  if (!r) return [];
  const pts: { lat: number; lng: number }[] = r.suppliers.filter(isPlaced).map((s) => ({ lat: s.lat, lng: s.lng }));
  if (r.importer?.lat != null && r.importer.lng != null) pts.push({ lat: r.importer.lat, lng: r.importer.lng });
  return pts;
}

export const topSupplier = (r?: SupplierReport | null): Supplier | undefined =>
  r ? [...r.suppliers].sort((a, b) => (b.shipments ?? 0) - (a.shipments ?? 0))[0] : undefined;

/** A supplier's country, as geocoded from its listed address (never from its name). */
export const supplierCountry = (s?: Pick<Supplier, "country"> | null) => s?.country ?? "";

/** "Nike" -> "Nike's", "Trader Joe's" -> "Trader Joe's" (no doubled possessive). */
export const possessive = (name: string) => (/'s$/i.test(name) ? name : `${name}'s`);

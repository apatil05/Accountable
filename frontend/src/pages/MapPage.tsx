// "Where your money goes": every company's real suppliers on a zoomable globe. Owner: Person C
// (rebuilt from Person D's original page with the owner's heads-up). All data is real: US import records
// (ImportYeti) -> each supplier and the company's US importer, geocoded, with a Wikipedia article per place.
//  - point at a company to light its supplier routes; click it to open its page
//  - "Suppliers" lists a company's suppliers; click one (or a diamond on the globe) to learn about where it is
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SpendingSummary, SupplierReport } from "../api/types";
import Globe from "../components/Globe";
import SupplierCard, { ImporterCard } from "../components/SupplierCard";
import { possessive, supplierCountry, reportPoints, sphericalCenter, supplierPointId, toGlobe } from "../lib/suppliers";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const num = (n: number) => n.toLocaleString("en-US");

export default function MapPage() {
  const [reports, setReports] = useState<SupplierReport[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [summary, setSummary] = useState<SpendingSummary | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [pinned, setPinned] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null); // point id: "company:index" or "company:importer"
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState("");
  const coarse = typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;

  useEffect(() => {
    api
      .allSuppliers()
      .then(setReports)
      .catch((e) => {
        setReports([]);
        setFailed(
          String(e).includes("503")
            ? "Supplier data isn't set up on this server yet (it needs an ImportYeti key)."
            : "We couldn't load supplier data right now.",
        );
      });
    api.summary().then(setSummary).catch(() => setSummary(null));
  }, []);

  const names = useMemo(() => Object.fromEntries((summary?.merchants ?? []).map((m) => [m.merchant_id, m.name])), [summary]);
  const byId = useMemo(() => new Map((reports ?? []).map((r) => [r.merchant_id, r])), [reports]);
  const globe = useMemo(() => toGlobe(reports ?? [], names), [reports, names]);
  const companies = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (summary?.merchants ?? []).filter((m) => {
      if (!q) return true;
      const r = byId.get(m.merchant_id);
      const hay = [m.name, ...(r?.suppliers ?? []).flatMap((s) => [s.name, s.place?.title ?? "", supplierCountry(s)])];
      return hay.join(" ").toLowerCase().includes(q);
    });
  }, [summary, byId, query]);

  const [selCompany, selPart] = selected ? selected.split(":") : [null, ""];
  const selReport = selCompany ? byId.get(selCompany) : undefined;
  const supplier = selPart !== "importer" ? selReport?.suppliers[Number(selPart)] : undefined;
  const importer = selPart === "importer" ? selReport?.importer ?? undefined : undefined;
  const lit = pinned ?? preview;
  const pinnedReport = pinned ? byId.get(pinned) : undefined;
  const focus: [number, number] | null = supplier
    ? [supplier.lng, supplier.lat]
    : importer?.lat != null && importer.lng != null
      ? [importer.lng, importer.lat]
      : lit
      ? sphericalCenter(reportPoints(byId.get(lit)))
      : null;

  const pin = (id: string) => {
    setPinned(id);
    setSelected(null);
    setZoom(1.2);
  };
  const unpin = () => {
    setPinned(null);
    setSelected(null);
    setZoom(1);
  };
  const pickSupplier = (id: string) => {
    setPinned(id.split(":")[0]);
    setSelected(id);
    setZoom(3);
  };
  const onPointClick = (id: string) => pickSupplier(id); // suppliers and importers both open a card

  return (
    <div className="mapx">
      <aside className="mapx-panel" aria-label="Companies and their suppliers">
        {!pinned ? (
          <>
            <div className="mapx-head">
              <h1>Where it comes from</h1>
              <p>
                Real suppliers behind the companies you pay, from US import records. Point at a company to see its
                supplier routes, click it to open its page, or list its suppliers.
              </p>
            </div>
            <label className="mapx-search">
              <span className="visually-hidden">Filter companies</span>
              <input type="search" placeholder="Filter by company, supplier, place or country" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
            {reports === null || !summary ? (
              <p className="mapx-note" role="status">Loading suppliers…</p>
            ) : failed ? (
              <p className="mapx-note" role="alert">{failed}</p>
            ) : companies.length === 0 ? (
              <p className="mapx-note">No companies match that filter.</p>
            ) : (
              <ul className="mapx-list" onMouseLeave={() => setPreview(null)}>
                {companies.map((m) => {
                  const r = byId.get(m.merchant_id);
                  const n = r?.suppliers.length ?? 0;
                  const countries = [...new Set((r?.suppliers ?? []).map((s) => supplierCountry(s)).filter(Boolean))];
                  return (
                    <li key={m.merchant_id} className="mapx-row">
                      <Link
                        to={`/merchant/${m.merchant_id}`}
                        className={`mapx-item ${lit === m.merchant_id ? "is-active" : ""}`}
                        onMouseEnter={() => setPreview(m.merchant_id)}
                        onFocus={() => setPreview(m.merchant_id)}
                        onClick={(e) => {
                          // Touch has no hover: the first tap previews the routes, the second opens the page.
                          if (coarse && lit !== m.merchant_id) {
                            e.preventDefault();
                            setPreview(m.merchant_id);
                          }
                        }}
                      >
                        <span className="mapx-item-top">
                          <strong>{m.name}</strong>
                          <span className="num">{usd(m.total_spent)}</span>
                        </span>
                        <span className="mapx-item-sub">
                          {n ? `${n} supplier${n === 1 ? "" : "s"} · ${countries.join(", ")}` : "No US import records found"}
                        </span>
                        {coarse && lit === m.merchant_id && <span className="cr-open">Tap again to open</span>}
                      </Link>
                      {n > 0 && (
                        <button type="button" className="mapx-sup-btn" onClick={() => pin(m.merchant_id)} aria-label={`List ${possessive(m.name)} suppliers`}>
                          <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2l5 6-5 6-5-6z" /></svg>
                          Suppliers
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        ) : (
          <>
            <button type="button" className="mapx-back" onClick={unpin}>All companies</button>
            <div className="mapx-head">
              <h1>{possessive(names[pinned] ?? pinned)} suppliers</h1>
              <p>
                Top suppliers in US import records
                {pinnedReport?.company_name && <> for "{pinnedReport.company_name}"</>}. Locations are approximate, from each
                supplier's listed address. Click one to learn about where it is.
              </p>
            </div>
            {pinnedReport?.suppliers.length ? (
              <ul className="mapx-list">
                {pinnedReport.suppliers.map((s, i) => {
                  const id = supplierPointId(pinned, i);
                  return (
                    <li key={id}>
                      <button type="button" className={`mapx-item mapx-supplier ${selected === id ? "is-active" : ""}`} onClick={() => pickSupplier(id)} aria-pressed={selected === id}>
                        <span className="mapx-item-top">
                          <strong>{s.name}</strong>
                          {s.country && <span className="tag">{supplierCountry(s)}</span>}
                        </span>
                        <span className="mapx-item-sub">
                          {s.place ? s.place.title : "Location from listed address"}
                          {s.shipments != null && ` · ${num(s.shipments)} shipments`}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="mapx-note">No suppliers found in US import records for {names[pinned] ?? "this company"}.</p>
            )}
            {pinnedReport?.importer && (
              <button
                type="button"
                className={`mapx-item mapx-importer-btn ${selected === `${pinned}:importer` ? "is-active" : ""}`}
                onClick={() => pickSupplier(`${pinned}:importer`)}
                disabled={pinnedReport.importer.lat == null}
              >
                <span className="mapx-item-top"><strong>Ships to {pinnedReport.importer.name}</strong><span className="tag">US importer</span></span>
                <span className="mapx-item-sub">
                  {pinnedReport.importer.place ? pinnedReport.importer.place.title : pinnedReport.importer.address}
                </span>
              </button>
            )}
            <p className="mapx-credit">Supplier data: ImportYeti (US bill-of-lading records). Places: Wikipedia.</p>
            <Link to={`/merchant/${pinned}`} className="mapx-report">Open {possessive(names[pinned] ?? "company")} page</Link>
          </>
        )}
      </aside>

      <section className="mapx-stage" aria-label="Globe">
        {globe.routes.length > 0 && (
          <Globe
            routes={globe.routes}
            points={globe.points}
            highlight={lit ? [lit] : null}
            focus={focus}
            zoom={zoom}
            onZoomChange={setZoom}
            zoomable
            wheel="zoom"
            selectedPointId={selected}
            onPointClick={onPointClick}
            tweenMs={900}
            ariaLabel={`Globe of ${globe.routes.length} real supplier routes from US import records. Drag to spin, scroll or use plus and minus to zoom, click a supplier for details.`}
          />
        )}
        {supplier && <SupplierCard supplier={supplier} company={names[selCompany!]} onClose={() => { setSelected(null); setZoom(1.2); }} />}
        {importer && <ImporterCard importer={importer} company={names[selCompany!]} onClose={() => { setSelected(null); setZoom(1.2); }} />}

        <div className="mapx-controls" role="group" aria-label="Zoom">
          <button type="button" onClick={() => setZoom((z) => Math.min(8, z * 1.4))} aria-label="Zoom in">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
          </button>
          <button type="button" onClick={() => setZoom((z) => Math.max(1, z / 1.4))} aria-label="Zoom out">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10" /></svg>
          </button>
          <button type="button" className="mapx-reset" onClick={unpin}>Show all</button>
        </div>
        <p className="mapx-caption">
          {coarse ? "Drag to spin · pinch to zoom · tap a supplier" : "Drag to spin · scroll, pinch or double-click to zoom · click a supplier"}
        </p>
      </section>
    </div>
  );
}

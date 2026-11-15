// Company page: what you spent, its real suppliers (US import records), what research found, and alternatives.
// Owner: Person C. Facts, not ratings: the backend's scores are never shown, and political topics are left out.
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import {
  type Alternative,
  type CompanyEmissions,
  type EthicsReport,
  type LaborSummary,
  type MerchantSummary,
  type SupplierReport,
} from "../api/types";
import Footprint from "../components/Footprint";
import Globe, { type GlobeLayout } from "../components/Globe";
import { ImporterCard } from "../components/SupplierCard";
import SupplierCarousel from "../components/SupplierCarousel";
import TopicTabs from "../components/TopicTabs";
import { useOnDemandResearch } from "../lib/useResearch";
import { possessive, supplierCountry, reportPoints, sphericalCenter, supplierPointId, toGlobe } from "../lib/suppliers";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const HIDDEN_TOPICS = new Set(["political"]); // product decision: no political values
function localDate(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
// Where the globe sits in its box. With the importer card open, the globe moves into the space the card leaves
// free (beside it on wide screens, above it on phones) so the selected place is never hidden behind the card.
const GLOBE_FULL: GlobeLayout = { cx: 0.5, cy: 0.5, r: 0.94 };
const CARD_SPACE = 324; // .stop-card on this page: 300px wide + 24px inset (see .co-globe .stop-card)
const VERDICT_LABELS = { aligns: "Aligns", mixed: "Mixed", against: "Does not align" } as const;
const shortDate = (iso: string) => localDate(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

export default function MerchantDetail() {
  const { id = "" } = useParams();
  const [report, setReport] = useState<EthicsReport | null | "missing">(null);
  const [sup, setSup] = useState<SupplierReport | null | "missing">(null);
  const [alts, setAlts] = useState<Alternative[]>([]);
  const [me, setMe] = useState<MerchantSummary | null>(null);
  const [emissions, setEmissions] = useState<CompanyEmissions[] | null | "missing">(null);
  const [labor, setLabor] = useState<LaborSummary | null | "missing">(null);
  const [selected, setSelected] = useState<number | "importer" | null>(null);
  const [zoom, setZoom] = useState(1);
  const globeBox = useRef<HTMLDivElement>(null);
  const layout = useRef<GlobeLayout>({ ...GLOBE_FULL });

  useEffect(() => {
    setReport(null);
    setSup(null);
    setEmissions(null);
    setLabor(null);
    setSelected(null);
    setZoom(1);
    window.scrollTo(0, 0);
    api.ethics(id).then(setReport).catch(() => setReport("missing"));
    api.emissions(id).then(setEmissions).catch(() => setEmissions("missing"));
    api.labor(id).then(setLabor).catch(() => setLabor("missing"));
    api.suppliers(id).then(setSup).catch(() => setSup("missing"));
    api.alternatives(id).then(setAlts).catch(() => setAlts([]));
    api.summary().then((s) => setMe(s.merchants.find((m) => m.merchant_id === id) ?? null)).catch(() => setMe(null));
  }, [id]);

  const real = sup && sup !== "missing" ? sup : null;
  const research = report && report !== "missing" ? report : null;
  // No real research yet (placeholder data): ask the server to research this company and wait for it.
  const progress = useOnDemandResearch(id, research?.data_source === "mock", () => {
    api.ethics(id).then(setReport).catch(() => undefined);
  });
  const name = research?.merchant_name ?? me?.name ?? "This company";
  const topics = useMemo(
    () => (research?.categories ?? []).filter((c) => !HIDDEN_TOPICS.has(c.category)),
    [research],
  );
  const globe = useMemo(() => toGlobe(real ? [real] : [], { [id]: name }), [real, id, name]);
  const supplier = real && typeof selected === "number" ? real.suppliers[selected] : undefined;
  const importer = real && selected === "importer" ? real.importer : null;
  const focus: [number, number] | null = supplier
    ? [supplier.lng, supplier.lat]
    : importer?.lat != null && importer.lng != null
      ? [importer.lng, importer.lat]
      : sphericalCenter(reportPoints(real));
  const countries = [...new Set((real?.suppliers ?? []).map((s) => supplierCountry(s)).filter(Boolean))];

  const cardOpen = selected === "importer"; // supplier details live in the carousel; only the importer pops up
  useEffect(() => {
    const place = () => {
      const box = globeBox.current;
      if (!box || !cardOpen) {
        layout.current = { ...GLOBE_FULL };
        return;
      }
      const { clientWidth: w, clientHeight: h } = box;
      if (window.matchMedia("(max-width: 900px)").matches) {
        layout.current = { cx: 0.5, cy: 0.21, r: 0.42 };
      } else {
        const free = Math.max(w - CARD_SPACE, 0);
        layout.current = { cx: (CARD_SPACE + free / 2) / w, cy: 0.5, r: Math.min(GLOBE_FULL.r, Math.max(0.4, free / Math.min(w, h))) };
      }
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [cardOpen]);

  const pick = (i: number | "importer" | null) => {
    setSelected(i);
    setZoom(i == null ? 1 : 2.8);
  };

  return (
    <div className="co">
      {/* 1. Who, how much, and its route */}
      <section className="co-hero">
        <div className="co-hero-copy">
          <Link to="/dashboard" className="co-back">Spending</Link>
          <h1 className="co-name">{name}</h1>
          {me ? (
            <p className="co-spend">
              <span className="co-figure">{usd(me.total_spent)}</span>
              <span className="co-spend-note">
                across {me.purchase_count} {me.purchase_count === 1 ? "purchase" : "purchases"} · last on {shortDate(me.last_purchase)}
              </span>
            </p>
          ) : (
            <p className="co-spend-note">Loading what you spent…</p>
          )}
          <div className="co-tags">
            {me?.category.map((c) => <span key={c} className="tag">{c}</span>)}
            {countries.map((c) => <span key={c} className="tag">Supplier in {c}</span>)}
          </div>
          {real?.importer && (
            <button
              type="button"
              className={`co-delivered ${selected === "importer" ? "is-active" : ""}`}
              onClick={() => pick("importer")}
              disabled={real.importer.lat == null}
            >
              <span className="co-delivered-label">Delivered to</span>
              <span className="co-delivered-name">{real.importer.name}</span>
              {real.importer.place && <span className="co-delivered-where">{real.importer.place.title}</span>}
            </button>
          )}
          {research?.verdict ? (
            <div className={`co-verdict is-${research.verdict.label}`}>
              <p className="co-verdict-label">{VERDICT_LABELS[research.verdict.label]}</p>
              <p className="co-lede">{research.verdict.blurb}</p>
            </div>
          ) : report === null ? (
            <p className="co-lede" role="status">Checking {name} against your values…</p>
          ) : null}
        </div>

        <div className={`co-globe ${cardOpen ? "has-card" : ""}`} ref={globeBox}>
          {real && real.suppliers.length > 0 ? (
            <>
              <Globe
                routes={globe.routes}
                points={globe.points}
                highlight={[id]}
                focus={focus}
                layoutRef={layout}
                zoom={zoom}
                onZoomChange={setZoom}
                zoomable
                selectedPointId={selected === "importer" ? `${id}:importer` : selected != null ? supplierPointId(id, selected) : null}
                onPointClick={(pid) => (pid.endsWith(":importer") ? pick("importer") : pick(Number(pid.split(":")[1])))}
                tweenMs={900}
                ariaLabel={`Globe showing ${possessive(name)} suppliers. Drag to spin, scroll to zoom, click a supplier for details.`}
              />
              {importer && <ImporterCard importer={importer} company={name} onClose={() => pick(null)} />}
              <div className="mapx-controls co-zoom" role="group" aria-label="Zoom">
                <button type="button" onClick={() => setZoom((z) => Math.min(12, z * 1.5))} aria-label="Zoom in">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
                </button>
                <button type="button" onClick={() => setZoom((z) => Math.max(1, z / 1.5))} aria-label="Zoom out">
                  <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10" /></svg>
                </button>
              </div>
              <p className="co-globe-hint">Drag to spin · pinch, double-click or Ctrl + scroll to zoom</p>
            </>
          ) : (
            <div className="co-globe-empty">
              {sup === null ? "Looking up suppliers…" : sup === "missing" ? "Supplier data isn't available right now." : `No US import records found for ${name}.`}
            </div>
          )}
        </div>
      </section>

      {/* 2. Real suppliers, from US import records */}
      {real && real.suppliers.length > 0 && (
        <section className="co-section" aria-labelledby="sup-title">
          <div className="co-section-head">
            <h2 id="sup-title">Suppliers</h2>
            <p>
              Locations are approximate, based on each supplier's listed address.
            </p>
          </div>
          <SupplierCarousel
            companyId={id}
            suppliers={real.suppliers}
            selected={typeof selected === "number" ? selected : null}
            onSelect={pick}
            onShowGlobe={() =>
              globeBox.current?.scrollIntoView({
                behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
                block: "center",
              })
            }
          />
        </section>
      )}

      {/* 3. Carbon, reported emissions, and labor record */}
      <Footprint name={name} me={me} emissions={emissions} labor={labor} />

      {/* 4. What research found, by topic */}
      <section className="co-section" aria-labelledby="facts-title">
        <div className="co-section-head">
          <h2 id="facts-title">What we found</h2>
        </div>
        {research?.data_source === "mock" &&
          (progress === "working" ? (
            <p className="co-muted" role="status">
              Researching {name} from live sources. This takes a few minutes, and this page updates by itself.
            </p>
          ) : (
            <>
              {progress === "capped" && <p className="co-muted" role="status">Today's research limit has been reached. Try again tomorrow.</p>}
              {progress === "failed" && <p className="co-muted" role="status">Research for {name} couldn't finish. It will be retried later.</p>}
            </>
          ))}
        {report === null ? (
          <p className="co-muted" role="status">Researching {name}… this can take a few seconds.</p>
        ) : !research || topics.length === 0 ? (
          <p className="co-muted">No research yet for {name}.</p>
        ) : (
          <TopicTabs key={id} topics={topics} initial={topics[0]?.category ?? null} />
        )}
      </section>

      {/* 5. Alternatives */}
      <section className="co-section" aria-labelledby="alts-title">
        <div className="co-section-head">
          <h2 id="alts-title">Alternatives</h2>
        </div>
        {alts.length === 0 ? (
          <p className="co-muted">No suggestions yet.</p>
        ) : (
          <ul className="co-alts">
            {alts.map((a) => (
              <li key={a.name}>
                <span className="co-alt-name">
                  {a.name}
                  {a.is_local && <span className="tag">Local</span>}
                </span>
                <span className="co-alt-reason">{a.reason}</span>
                {a.url && <a href={a.url} target="_blank" rel="noreferrer" className="co-source">Visit</a>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

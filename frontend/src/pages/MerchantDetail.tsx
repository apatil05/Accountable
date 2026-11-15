// Company page: what you spent, its real suppliers (US import records), what research found, and alternatives.
// Owner: Person C. Facts, not ratings: the backend's scores are never shown, and political topics are left out.
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import {
  CATEGORY_LABELS,
  type Alternative,
  type CompanyEmissions,
  type EthicsReport,
  type LaborSummary,
  type MerchantSummary,
  type SupplierReport,
} from "../api/types";
import Footprint from "../components/Footprint";
import Globe from "../components/Globe";
import MockBanner from "../components/MockBanner";
import SupplierCard, { ImporterCard } from "../components/SupplierCard";
import { useOnDemandResearch } from "../lib/useResearch";
import { isPlaced, possessive, supplierCountry, reportPoints, sphericalCenter, supplierPointId, toGlobe } from "../lib/suppliers";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const HIDDEN_TOPICS = new Set(["political"]); // product decision: no political values
function localDate(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
const VERDICT_LABELS = { aligns: "Aligns", mixed: "Mixed", against: "Does not align" } as const;
const MAX_SOURCES = 2; // keep each topic tile scannable; best source tier first (company's own site is tier 4), then most confident
// Stored summaries can run 2+ sentences; show only the first. Skips periods in abbreviations like "Inc." or "U.S.".
function firstSentence(text: string) {
  const m = text.match(/^.*?(?<!\b(?:Inc|Co|Corp|Ltd|St|Mr|Ms|Dr|vs|U\.S|[A-Z]))[.!?](?=\s+["A-Z]|$)/s);
  return m ? m[0] : text;
}
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
  const focus: [number, number] | null = supplier && isPlaced(supplier)
    ? [supplier.lng, supplier.lat]
    : importer?.lat != null && importer.lng != null
      ? [importer.lng, importer.lat]
      : sphericalCenter(reportPoints(real));
  const countries = [...new Set((real?.suppliers ?? []).map((s) => supplierCountry(s)).filter(Boolean))];

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
          {research?.verdict ? (
            <div className={`co-verdict is-${research.verdict.label}`}>
              <p className="co-verdict-label">{VERDICT_LABELS[research.verdict.label]}</p>
              <p className="co-lede">{research.verdict.blurb}</p>
            </div>
          ) : report === null ? (
            <p className="co-lede" role="status">Checking {name} against your values…</p>
          ) : null}
        </div>

        <div className="co-globe">
          {real && real.suppliers.length > 0 ? (
            <>
              <Globe
                routes={globe.routes}
                points={globe.points}
                highlight={[id]}
                focus={focus}
                zoom={zoom}
                onZoomChange={setZoom}
                zoomable
                selectedPointId={selected === "importer" ? `${id}:importer` : selected != null ? supplierPointId(id, selected) : null}
                onPointClick={(pid) => (pid.endsWith(":importer") ? pick("importer") : pick(Number(pid.split(":")[1])))}
                tweenMs={900}
                ariaLabel={`Globe showing ${possessive(name)} real suppliers from US import records. Drag to spin, scroll to zoom, click a supplier for details.`}
              />
              {supplier && <SupplierCard supplier={supplier} company={name} onClose={() => pick(null)} />}
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
              From US import records (ImportYeti){real.company_name && <> for "{real.company_name}"</>}. Locations are approximate,
              from each supplier's listed address; place articles from Wikipedia.
            </p>
          </div>
          <ul className="co-suppliers">
            {real.suppliers.map((s, i) => (
              <li key={supplierPointId(id, i)}>
                <button type="button" className={`co-supplier ${selected === i ? "is-active" : ""}`} onClick={() => pick(i)} aria-pressed={selected === i}>
                  {s.place?.thumbnail_url ? <img src={s.place.thumbnail_url} alt="" /> : <span className="co-supplier-mark" aria-hidden="true" />}
                  <span className="co-supplier-text">
                    <span className="co-supplier-name">{s.name}</span>
                    <span className="co-supplier-where">
                      {isPlaced(s) ? [s.place?.title, supplierCountry(s)].filter(Boolean).join(" · ") : "Not on the map"}
                      {s.shipments != null && ` · ${s.shipments.toLocaleString("en-US")} shipments`}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {real.importer && (
            <button
              type="button"
              className={`co-supplier co-importer-btn ${selected === "importer" ? "is-active" : ""}`}
              onClick={() => pick("importer")}
              disabled={real.importer.lat == null}
            >
              {real.importer.place?.thumbnail_url ? <img src={real.importer.place.thumbnail_url} alt="" /> : <span className="co-supplier-mark is-importer" aria-hidden="true" />}
              <span className="co-supplier-text">
                <span className="co-supplier-name">They ship to {real.importer.name}</span>
                <span className="co-supplier-where">
                  US importer{real.importer.place ? ` · ${real.importer.place.title}` : real.importer.address ? ` · ${real.importer.address}` : ""}
                </span>
              </span>
            </button>
          )}
        </section>
      )}

      {/* 3. Carbon, reported emissions, and labor record */}
      <Footprint name={name} me={me} emissions={emissions} labor={labor} />

      {/* 4. What research found, by topic */}
      <section className="co-section" aria-labelledby="facts-title">
        <div className="co-section-head">
          <h2 id="facts-title">What we found</h2>
          <p>
            The key facts and verified sources behind the verdict above, grouped by topic.
            Compare them against <Link to="/values">your values</Link> to see what matters most to you.
          </p>
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
              <MockBanner />
            </>
          ))}
        {report === null ? (
          <p className="co-muted" role="status">Researching {name}… this can take a few seconds.</p>
        ) : !research || topics.length === 0 ? (
          <p className="co-muted">No research yet for {name}.</p>
        ) : (
          <div className="co-topics">
            {topics.map((t) => (
              <article key={t.category} className="co-topic">
                <h3>{CATEGORY_LABELS[t.category as keyof typeof CATEGORY_LABELS] ?? t.category}</h3>
                <p className="co-topic-summary">{firstSentence(t.summary)}</p>
                {(() => {
                  const sources = t.claims
                    .filter((c) => c.validated && c.source_url)
                    .sort((a, b) => (a.source_tier ?? 9) - (b.source_tier ?? 9) || b.confidence - a.confidence)
                    .slice(0, MAX_SOURCES);
                  return sources.length === 0 ? (
                    <p className="co-source is-missing">No verified sources yet</p>
                  ) : (
                    <ul className="co-claims">
                      {sources.map((c, i) => (
                        <li key={i}>
                          <span className="status is-verified">Verified</span>
                          <a href={c.source_url!} target="_blank" rel="noreferrer" className="co-source" title={c.text}>
                            {c.source_name ?? new URL(c.source_url!).hostname}
                          </a>
                        </li>
                      ))}
                    </ul>
                  );
                })()}
              </article>
            ))}
          </div>
        )}
      </section>

      {/* 5. Alternatives */}
      <section className="co-section" aria-labelledby="alts-title">
        <div className="co-section-head">
          <h2 id="alts-title">Alternatives</h2>
          <p>Other places to buy something similar.</p>
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

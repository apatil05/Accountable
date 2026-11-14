// Company page: what you spent, where its product comes from, what research found, and alternatives.
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
  type SupplyChain,
} from "../api/types";
import Footprint from "../components/Footprint";
import Globe, { type StopRef } from "../components/Globe";
import MockBanner from "../components/MockBanner";
import StopDetail from "../components/StopDetail";
import { continentOf } from "../lib/insights";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const HIDDEN_TOPICS = new Set(["political"]); // product decision: no political values
function localDate(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
const shortDate = (iso: string) => localDate(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

// Center of a route on the sphere, so the whole route sits on the visible face.
function routeCenter(c: SupplyChain): [number, number] {
  let x = 0, y = 0, z = 0;
  for (const s of c.stages) {
    const l = (s.lng * Math.PI) / 180, p = (s.lat * Math.PI) / 180;
    x += Math.cos(p) * Math.cos(l);
    y += Math.cos(p) * Math.sin(l);
    z += Math.sin(p);
  }
  return [(Math.atan2(y, x) * 180) / Math.PI, (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI];
}

export default function MerchantDetail() {
  const { id = "" } = useParams();
  const [report, setReport] = useState<EthicsReport | null | "missing">(null);
  const [chain, setChain] = useState<SupplyChain | null | "missing">(null);
  const [alts, setAlts] = useState<Alternative[]>([]);
  const [me, setMe] = useState<MerchantSummary | null>(null);
  const [emissions, setEmissions] = useState<CompanyEmissions[] | null | "missing">(null);
  const [labor, setLabor] = useState<LaborSummary | null | "missing">(null);
  const [stop, setStop] = useState<StopRef | null>(null);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    setReport(null);
    setChain(null);
    setEmissions(null);
    setLabor(null);
    setStop(null);
    setZoom(1);
    window.scrollTo(0, 0);
    api.ethics(id).then(setReport).catch(() => setReport("missing"));
    api.emissions(id).then(setEmissions).catch(() => setEmissions("missing"));
    api.labor(id).then(setLabor).catch(() => setLabor("missing"));
    api.supplyChain(id).then(setChain).catch(() => setChain("missing"));
    api.alternatives(id).then(setAlts).catch(() => setAlts([]));
    api.summary().then((s) => setMe(s.merchants.find((m) => m.merchant_id === id) ?? null)).catch(() => setMe(null));
  }, [id]);

  const route = chain && chain !== "missing" ? chain : null;
  const research = report && report !== "missing" ? report : null;
  const name = research?.merchant_name ?? me?.name ?? "This company";
  const topics = useMemo(
    () => (research?.categories ?? []).filter((c) => !HIDDEN_TOPICS.has(c.category)),
    [research],
  );
  const stopData = route && stop ? route.stages[stop.index] : null;
  const focus: [number, number] | null = stopData ? [stopData.lng, stopData.lat] : route ? routeCenter(route) : null;
  const origin = route?.stages[0];

  const pickStop = (i: number) => {
    if (!route) return;
    if (i === route.stages.length - 1) {
      setStop(null);
      setZoom(1);
      return;
    }
    setStop({ merchantId: route.merchant_id, index: i });
    setZoom(2.6);
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
            {origin && <span className="tag">Starts in {continentOf(origin.lat, origin.lng)}</span>}
          </div>
          {route && (
            <p className="co-lede">
              We traced one example product, a {route.product.toLowerCase()}, from {origin?.place} to you in {route.stages.length - 1} stops.
              This route is an example and hasn't been verified yet.
            </p>
          )}
        </div>

        <div className="co-globe">
          {route ? (
            <>
              <Globe
                chains={[route]}
                highlight={[route.merchant_id]}
                focus={focus}
                zoom={zoom}
                onZoomChange={setZoom}
                zoomable
                labels={{ [route.merchant_id]: name }}
                labelAllStops
                selectedStop={stop}
                onStopClick={(s) => pickStop(s.index)}
                tweenMs={900}
                ariaLabel={`Globe showing ${name}'s example supply route. Drag to spin, scroll to zoom, click a stop for details.`}
              />
              {stopData && (
                <div className="stop-card">
                  <button type="button" className="stop-close" onClick={() => pickStop(route.stages.length - 1)} aria-label="Close stop details">
                    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
                  </button>
                  <StopDetail stage={stopData} />
                </div>
              )}
            </>
          ) : (
            <div className="co-globe-empty">{chain === null ? "Loading the route…" : "No route traced for this company yet."}</div>
          )}
        </div>
      </section>

      {/* 2. The route, stop by stop */}
      {route && (
        <section className="co-section" aria-labelledby="route-title">
          <h2 id="route-title">The route</h2>
          <ol className="co-route">
            {route.stages.map((s, i) => {
              const active = stop?.index === i;
              const isEnd = i === route.stages.length - 1;
              return (
                <li key={s.order}>
                  <button type="button" className={`co-stop ${active ? "is-active" : ""} ${isEnd ? "is-end" : ""}`} onClick={() => pickStop(i)} aria-pressed={active}>
                    <span className="co-stop-n">{i + 1}</span>
                    <span className="co-stop-stage">{s.stage}</span>
                    <span className="co-stop-place">{s.place}</span>
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      )}

      {/* 3. Carbon, reported emissions, and labor record */}
      <Footprint name={name} me={me} emissions={emissions} labor={labor} />

      {/* 4. What research found, by topic */}
      <section className="co-section" aria-labelledby="facts-title">
        <div className="co-section-head">
          <h2 id="facts-title">What we found</h2>
          <p>
            Facts and sources, grouped by topic. Each claim says whether a source was checked. We don't rate companies;
            weigh these against <Link to="/values">your values</Link>.
          </p>
        </div>
        {research?.data_source === "mock" && <MockBanner />}
        {report === null ? (
          <p className="co-muted" role="status">Researching {name}… this can take a few seconds.</p>
        ) : !research || topics.length === 0 ? (
          <p className="co-muted">No research yet for {name}.</p>
        ) : (
          <div className="co-topics">
            {topics.map((t) => (
              <article key={t.category} className="co-topic">
                <h3>{CATEGORY_LABELS[t.category as keyof typeof CATEGORY_LABELS] ?? t.category}</h3>
                <p className="co-topic-summary">{t.summary}</p>
                <ul className="co-claims">
                  {t.claims.map((c, i) => (
                    <li key={i}>
                      <span className={`status ${c.validated ? "is-verified" : ""}`}>{c.validated ? "Verified" : "Unverified"}</span>
                      <span className="co-claim-text">{c.text}</span>
                      {c.source_url ? (
                        <a href={c.source_url} target="_blank" rel="noreferrer" className="co-source">{c.source_name ?? new URL(c.source_url).hostname}</a>
                      ) : (
                        <span className="co-source is-missing">No source yet</span>
                      )}
                    </li>
                  ))}
                </ul>
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

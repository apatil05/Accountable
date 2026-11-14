// Home page: spending summary, supply-chain map tile, merchant tiles. Owner: Person C.
// Scores are computed by the backend but intentionally not shown: we present facts, not ratings.
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SpendingSummary, SupplyChain } from "../api/types";
import WorldMap, { ROUTE_COLORS } from "../components/WorldMap";
import MockBanner from "../components/MockBanner";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });

// Nessie dates are "YYYY-MM-DD". Parse as a local date; `new Date(iso)` would read it as UTC
// and show the previous day in US time zones.
function localDate(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
const shortDate = (iso: string) => localDate(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

export default function Dashboard() {
  const navigate = useNavigate();
  const [data, setData] = useState<SpendingSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // null = still loading; [] = none or failed (the map is a bonus, so the page works without it)
  const [chains, setChains] = useState<SupplyChain[] | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);

  const load = useCallback(() => {
    setErr(null);
    setData(null);
    setChains(null);
    api.summary().then(setData).catch((e) => setErr(String(e)));
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
  }, []);
  useEffect(load, [load]);

  // Pass chains to the map in spend order, so route colors run in the same order as the tiles.
  const ordered = useMemo(() => {
    if (!data || !chains) return chains;
    const rank = new Map(data.merchants.map((m, i) => [m.merchant_id, i]));
    return [...chains].sort((a, b) => (rank.get(a.merchant_id) ?? 99) - (rank.get(b.merchant_id) ?? 99));
  }, [data, chains]);

  if (err) {
    return (
      <div className="state-panel" role="alert">
        <h1>We couldn't load your spending</h1>
        <p>The Accountable server didn't respond. Check that it's running, then try again.</p>
        <button className="primary" onClick={load}>Try again</button>
        <details>
          <summary>Technical details</summary>
          <code>{err}</code>
        </details>
      </div>
    );
  }
  if (!data) {
    return <div className="loading" role="status">Loading your spending…</div>;
  }

  const purchases = data.merchants.reduce((a, m) => a + m.purchase_count, 0);
  const latest = data.merchants.reduce((a, m) => (m.last_purchase > a ? m.last_purchase : a), "");
  const names = Object.fromEntries(data.merchants.map((m) => [m.merchant_id, m.name]));
  const colorById = Object.fromEntries((ordered ?? []).map((c, i) => [c.merchant_id, ROUTE_COLORS[i % ROUTE_COLORS.length]]));
  const chainById = Object.fromEntries((ordered ?? []).map((c) => [c.merchant_id, c]));
  const hl = {
    onMouseEnter: (id: string) => setHighlight(id),
    onFocus: (id: string) => setHighlight(id),
    clear: () => setHighlight(null),
  };

  return (
    <>
      <MockBanner />

      {/* 1. Spending summary */}
      <section className="summary" aria-labelledby="summary-title">
        <h1 id="summary-title">Where your money went</h1>
        {data.merchants.length > 0 && (
          <p className="summary-line">
            You spent <strong>{usd(data.total_spent)}</strong> across{" "}
            <strong>{data.merchant_count} companies</strong> in <strong>{purchases} purchases</strong>
            {latest && <>, up to {shortDate(latest)}</>}.
          </p>
        )}
      </section>

      {/* 2. Map tile: always reserves its space so the tiles below never jump. */}
      {(ordered === null || ordered.length > 0) && (
        <section className="card map-tile" aria-labelledby="map-title">
          <div className="tile-head">
            <div>
              <h2 id="map-title">Where products like these are made</h2>
              <p className="muted">
                Each line follows one typical product from each company, from raw material to your area.
                These are example routes and haven't been verified yet.
              </p>
            </div>
            <Link to="/map" className="tile-link">Open the full map →</Link>
          </div>

          <div className="map-frame">
            {ordered === null ? (
              <div className="map-placeholder" role="status">Loading routes…</div>
            ) : (
              <WorldMap
                chains={ordered}
                labels={names}
                highlight={highlight}
                onSelect={(id) => navigate(`/merchant/${id}`)}
              />
            )}
          </div>

          {ordered && ordered.length > 0 && (
            <ul className="route-legend" aria-label="Routes on the map" onMouseLeave={hl.clear}>
              {ordered.map((c) => (
                <li key={c.merchant_id}>
                  <Link
                    to={`/merchant/${c.merchant_id}`}
                    className={`route-item ${highlight === c.merchant_id ? "is-active" : ""}`}
                    onMouseEnter={() => hl.onMouseEnter(c.merchant_id)}
                    onFocus={() => hl.onFocus(c.merchant_id)}
                    onBlur={hl.clear}
                  >
                    <i className="route-dot" style={{ background: colorById[c.merchant_id] }} aria-hidden="true" />
                    <span className="route-name">{names[c.merchant_id] ?? c.merchant_id}</span>
                    <span className="route-path">
                      {c.product}: {c.stages[0]?.place} → {c.stages[c.stages.length - 1]?.place}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* 3. Merchant tiles */}
      <section aria-labelledby="companies-title">
        <div className="section-head">
          <h2 id="companies-title">Companies you buy from</h2>
          <span className="muted">Most spent first</span>
        </div>

        {data.merchants.length === 0 ? (
          <div className="card empty">
            <p><strong>No purchases yet.</strong></p>
            <p className="muted">
              When transactions come in from your account, the companies you pay will show up here with what you
              spent and where their products come from.
            </p>
          </div>
        ) : (
          <ul className="merchant-grid" onMouseLeave={hl.clear}>
            {data.merchants.map((m) => {
              const c = chainById[m.merchant_id];
              const share = data.total_spent ? m.total_spent / data.total_spent : 0;
              return (
                <li key={m.merchant_id}>
                  <Link
                    to={`/merchant/${m.merchant_id}`}
                    className="merchant-tile"
                    onMouseEnter={() => hl.onMouseEnter(m.merchant_id)}
                    onFocus={() => hl.onFocus(m.merchant_id)}
                    onBlur={hl.clear}
                  >
                    <h3 className="mt-name">
                      {colorById[m.merchant_id] && (
                        <i className="route-dot" style={{ background: colorById[m.merchant_id] }} aria-hidden="true" />
                      )}
                      {m.name}
                    </h3>
                    <span className="muted">{m.category.join(" · ")}</span>
                    <div className="mt-spend">{usd(m.total_spent)}</div>
                    <div className="mt-share" aria-hidden="true"><div style={{ width: `${share * 100}%` }} /></div>
                    <div className="muted">
                      {Math.round(share * 100)}% of your spending · {m.purchase_count}{" "}
                      {m.purchase_count === 1 ? "purchase" : "purchases"} · last on {shortDate(m.last_purchase)}
                    </div>
                    {c && (
                      <p className="mt-example">
                        Example product: {c.product}, first made in {c.stages[0]?.place}
                      </p>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* Closing: hand off to values */}
      <section className="values-cta" aria-labelledby="values-cta-title">
        <h2 id="values-cta-title">What matters to you?</h2>
        <p className="muted">
          Tell Accountable which issues you care about, like how workers are treated or the environment, so you can
          compare each company against them.
        </p>
        <Link to="/values" className="tile-link">Set your values →</Link>
      </section>
    </>
  );
}

// Home page: spending summary, supply-chain map tile, merchant tiles. Owner: Person C.
// Scores are computed by the backend but intentionally not shown: we present facts, not ratings.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SpendingSummary, SupplyChain } from "../api/types";
import WorldMap, { ROUTE_COLORS } from "../components/WorldMap";
import MockBanner from "../components/MockBanner";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const shortDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

export default function Dashboard() {
  const [data, setData] = useState<SpendingSummary | null>(null);
  const [chains, setChains] = useState<SupplyChain[]>([]);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.summary().then(setData).catch((e) => setErr(String(e)));
    // The map is a bonus; the dashboard still works if it fails.
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
  }, []);

  if (err) return <div className="error">Couldn't reach the API. Is the backend running on :8000?<br />{err}</div>;
  if (!data) return <div className="loading">Loading your spending…</div>;

  const purchases = data.merchants.reduce((a, m) => a + m.purchase_count, 0);
  const names = Object.fromEntries(data.merchants.map((m) => [m.merchant_id, m.name]));
  const chainById = Object.fromEntries(chains.map((c, i) => [c.merchant_id, { chain: c, color: ROUTE_COLORS[i % ROUTE_COLORS.length] }]));
  const stops = chains.reduce((a, c) => a + c.stages.length, 0);

  return (
    <>
      <MockBanner />

      {/* 1. Spending summary */}
      <section className="hero">
        <div>
          <p className="eyebrow">Last 90 days</p>
          <h1>Where your money went</h1>
        </div>
        <div className="stats">
          <div className="stat"><span>{usd(data.total_spent)}</span>spent</div>
          <div className="stat"><span>{data.merchant_count}</span>companies</div>
          <div className="stat"><span>{purchases}</span>purchases</div>
        </div>
      </section>

      {/* 2. Map tile */}
      {chains.length > 0 && (
        <section className="card map-tile">
          <div className="tile-head">
            <div>
              <h3>Where your purchases come from</h3>
              <p className="muted">
                {chains.length} supply routes · {stops} stops from raw material to you
              </p>
            </div>
            <Link to="/map" className="tile-link">Explore the map →</Link>
          </div>
          <WorldMap chains={chains} labels={names} />
        </section>
      )}

      {/* 3. Merchant tiles */}
      <div className="section-head">
        <h3>Companies you buy from</h3>
        <span className="muted">Sorted by how much you spent</span>
      </div>
      {data.merchants.length === 0 ? (
        <div className="card muted">No purchases found yet.</div>
      ) : (
        <ul className="merchant-grid">
          {data.merchants.map((m) => {
            const c = chainById[m.merchant_id];
            return (
              <li key={m.merchant_id}>
                <Link to={`/merchant/${m.merchant_id}`} className="merchant-tile">
                  <div className="mt-top">
                    <strong className="mt-name">
                      {c && <i className="route-dot" style={{ background: c.color }} />}
                      {m.name}
                    </strong>
                    <span className="muted">{m.category.join(" · ")}</span>
                  </div>
                  <div className="mt-spend">{usd(m.total_spent)}</div>
                  <div className="muted">
                    {m.purchase_count} {m.purchase_count === 1 ? "purchase" : "purchases"} · last {shortDate(m.last_purchase)}
                  </div>
                  {c && (
                    <div className="mt-facts">
                      <span className="fact-chip">{c.chain.product}</span>
                      <span className="fact-chip">Starts in {c.chain.stages[0]?.place}</span>
                    </div>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

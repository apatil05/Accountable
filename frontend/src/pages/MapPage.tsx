// "Where your money goes": every merchant's supply chain on one map. Owner: Person D.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { SpendingSummary, SupplyChain } from "../api/types";
import WorldMap, { ROUTE_COLORS } from "../components/WorldMap";
import MockBanner from "../components/MockBanner";

export default function MapPage() {
  const [chains, setChains] = useState<SupplyChain[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [sel, setSel] = useState<string | null>(null);

  useEffect(() => {
    api.allSupplyChains().then(setChains);
    api.summary().then((s: SpendingSummary) =>
      setNames(Object.fromEntries(s.merchants.map((m) => [m.merchant_id, m.name]))),
    );
  }, []);

  const selected = chains.find((c) => c.merchant_id === sel);

  return (
    <>
      <MockBanner />
      <section className="hero">
        <div>
          <p className="eyebrow">Supply chains</p>
          <h1>The journey behind your purchases</h1>
        </div>
      </section>
      <div className="card map-card">
        <WorldMap chains={chains} highlight={sel} onSelect={(id) => setSel(id === sel ? null : id)} labels={names} />
        <div className="legend">
          {chains.map((c, i) => (
            <button
              key={c.merchant_id}
              className={`legend-item ${sel === c.merchant_id ? "active" : ""}`}
              onClick={() => setSel(sel === c.merchant_id ? null : c.merchant_id)}
            >
              <i style={{ background: ROUTE_COLORS[i % ROUTE_COLORS.length] }} />
              {names[c.merchant_id] ?? c.merchant_id}
              <span className="muted">{c.product}</span>
            </button>
          ))}
        </div>
      </div>
      {selected && (
        <div className="card">
          <h3>{names[selected.merchant_id]}: {selected.product}</h3>
          <ol className="stages">
            {selected.stages.map((s) => (
              <li key={s.order}><strong>{s.stage}</strong><span className="muted">{s.place}</span></li>
            ))}
          </ol>
          <Link to={`/merchant/${selected.merchant_id}`}>Full report →</Link>
        </div>
      )}
    </>
  );
}

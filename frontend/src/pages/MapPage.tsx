// "Where your money goes": every supply route on a zoomable night globe. Owner: Person C (rebuilt from
// Person D's original page with the owner's heads-up; D's flat WorldMap stays on the merchant page).
// Point at a company to preview its route; click it to open its page. Click a stop on the globe for details.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { SpendingSummary, SupplyChain, SupplyChainStage } from "../api/types";
import Globe, { type StopRef } from "../components/Globe";
import StopDetail from "../components/StopDetail";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });

// Center of a route on the sphere, so the whole route turns onto the visible face.
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

export default function MapPage() {
  const navigate = useNavigate();
  const [chains, setChains] = useState<SupplyChain[] | null>(null);
  const [summary, setSummary] = useState<SpendingSummary | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [stop, setStop] = useState<StopRef | null>(null);
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState("");
  const coarse = typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches;

  useEffect(() => {
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
    api.summary().then(setSummary).catch(() => setSummary(null));
  }, []);

  const names = useMemo(() => Object.fromEntries((summary?.merchants ?? []).map((m) => [m.merchant_id, m.name])), [summary]);
  const spend = useMemo(() => Object.fromEntries((summary?.merchants ?? []).map((m) => [m.merchant_id, m.total_spent])), [summary]);
  const list = useMemo(() => {
    const q = query.trim().toLowerCase();
    return [...(chains ?? [])]
      .sort((a, b) => (spend[b.merchant_id] ?? 0) - (spend[a.merchant_id] ?? 0))
      .filter((c) => !q || `${names[c.merchant_id] ?? ""} ${c.product} ${c.stages.map((s) => s.place).join(" ")}`.toLowerCase().includes(q));
  }, [chains, names, spend, query]);

  const byId = useMemo(() => new Map((chains ?? []).map((c) => [c.merchant_id, c])), [chains]);
  const stopChain = stop ? byId.get(stop.merchantId) : undefined;
  const stopData: SupplyChainStage | undefined = stop ? stopChain?.stages[stop.index] : undefined;
  const lit = stop?.merchantId ?? preview;
  const litChain = lit ? byId.get(lit) : undefined;
  const focus: [number, number] | null = stopData ? [stopData.lng, stopData.lat] : litChain ? routeCenter(litChain) : null;

  const selectStop = (ref: StopRef) => {
    setStop(ref);
    setZoom(3);
  };
  const reset = () => {
    setStop(null);
    setPreview(null);
    setZoom(1);
  };

  return (
    <div className="mapx">
      <aside className="mapx-panel" aria-label="Supply routes">
        <div className="mapx-head">
          <h1>Supply routes</h1>
          <p>One example product per company you pay, traced from where it starts to you. Not yet verified. Point at a company to see its route; click to open it.</p>
        </div>
        <label className="mapx-search">
          <span className="visually-hidden">Filter routes</span>
          <input type="search" placeholder="Filter by company, product or place" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        {chains === null ? (
          <p className="mapx-note" role="status">Loading routes…</p>
        ) : list.length === 0 ? (
          <p className="mapx-note">{chains.length ? "No routes match that filter." : "No routes yet."}</p>
        ) : (
          <ul className="mapx-list" onMouseLeave={() => setPreview(null)}>
            {list.map((c) => (
              <li key={c.merchant_id}>
                <Link
                  to={`/merchant/${c.merchant_id}`}
                  className={`mapx-item ${lit === c.merchant_id ? "is-active" : ""}`}
                  onMouseEnter={() => { setStop(null); setPreview(c.merchant_id); }}
                  onFocus={() => { setStop(null); setPreview(c.merchant_id); }}
                  onClick={(e) => {
                    // Touch has no hover: the first tap previews the route, the second opens the page.
                    if (coarse && lit !== c.merchant_id) {
                      e.preventDefault();
                      setStop(null);
                      setPreview(c.merchant_id);
                    }
                  }}
                >
                  <span className="mapx-item-top">
                    <strong>{names[c.merchant_id] ?? c.merchant_id}</strong>
                    {spend[c.merchant_id] != null && <span className="num">{usd(spend[c.merchant_id])}</span>}
                  </span>
                  <span className="mapx-item-sub">{c.product} · {c.stages[0].place} · {c.stages.length - 1} stops</span>
                  {coarse && lit === c.merchant_id && <span className="cr-open">Tap again to open</span>}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="mapx-stage" aria-label="Globe">
        {chains && chains.length > 0 && (
          <Globe
            chains={chains}
            highlight={lit ? [lit] : null}
            focus={focus}
            zoom={zoom}
            onZoomChange={setZoom}
            zoomable
            labels={names}
            labelAllStops
            selectedStop={stop}
            onStopClick={selectStop}
            tweenMs={900}
            ariaLabel={`Night globe of ${chains.length} example supply routes. Drag to spin, scroll or use plus and minus to zoom, click a stop for details.`}
          />
        )}

        {stop && stopData && stopChain && (
          <div className="stop-card" role="dialog" aria-label={`${stopData.place} details`}>
            <button type="button" className="stop-close" onClick={() => { setStop(null); setZoom(1.6); }} aria-label="Close stop details">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
            </button>
            <p className="stop-of">{names[stopChain.merchant_id] ?? stopChain.merchant_id} · {stopChain.product}</p>
            <StopDetail stage={stopData} />
            <Link to={`/merchant/${stopChain.merchant_id}`} className="mapx-report">Open {names[stopChain.merchant_id] ?? "company"}'s page</Link>
          </div>
        )}

        <div className="mapx-controls" role="group" aria-label="Zoom">
          <button type="button" onClick={() => setZoom((z) => Math.min(8, z * 1.4))} aria-label="Zoom in">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>
          </button>
          <button type="button" onClick={() => setZoom((z) => Math.max(1, z / 1.4))} aria-label="Zoom out">
            <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h10" /></svg>
          </button>
          <button type="button" className="mapx-reset" onClick={reset}>Show all</button>
        </div>
        <p className="mapx-caption">
          {coarse ? "Drag to spin · + and − to zoom · tap a stop for details" : "Drag to spin · scroll to zoom · click a stop for details"}
        </p>
      </section>
    </div>
  );
}

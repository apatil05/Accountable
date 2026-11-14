// Spending dashboard: your money over a living globe. Owner: Person C.
// Scores are computed by the backend but intentionally not shown: we present facts, not ratings.
// The globe is a fixed backdrop. It starts huge; as you scroll it eases aside and the content sheet slides
// over it. Hovering a company (or an insight) turns the globe to that route and lights it.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { MerchantSummary, SpendingSummary, SupplyChain } from "../api/types";
import Globe, { type GlobeLayout } from "../components/Globe";
import MockBanner from "../components/MockBanner";
import { continentOf, habits, whereItStarts } from "../lib/insights";

const usd = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });
const usd0 = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const pct = (x: number) => `${Math.round(x * 100)}%`;
// Explicit tints of the route accent for ranked shares (opacity fades go muddy on the dark field).
const TINTS = ["#f2b04a", "#f4bf6a", "#f5cb88", "#f6d49e", "#f7dbb0"];

// Nessie dates are "YYYY-MM-DD"; parse as a local date (new Date(iso) would read UTC and slip a day).
function localDate(iso: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(y, m - 1, d);
}
const shortDate = (iso: string) => localDate(iso).toLocaleDateString("en-US", { month: "short", day: "numeric" });

// Mean of points on a sphere (so averaging Asia and the Americas doesn't land in the wrong ocean).
function sphericalMean(points: [number, number][]): [number, number] {
  let x = 0, y = 0, z = 0;
  for (const [lng, lat] of points) {
    const l = (lng * Math.PI) / 180, p = (lat * Math.PI) / 180;
    x += Math.cos(p) * Math.cos(l);
    y += Math.cos(p) * Math.sin(l);
    z += Math.sin(p);
  }
  return [(Math.atan2(y, x) * 180) / Math.PI, (Math.atan2(z, Math.hypot(x, y)) * 180) / Math.PI];
}

// Globe placement at the top of the page (huge, centered) and once docked beside the sheet.
const LAYOUTS = {
  wide: { entry: { cx: 0.6, cy: 0.62, r: 1.3 }, docked: { cx: 0.735, cy: 0.53, r: 0.78 } }, // docked fully on screen
  // Phones: the globe lives in a sticky band at the top of the screen and never moves behind text.
  narrow: { entry: { cx: 0.5, cy: 0.52, r: 0.92 }, docked: { cx: 0.5, cy: 0.52, r: 0.92 } },
};
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

interface Spotlight {
  ids: string[];
  focus: [number, number];
}

export default function Dashboard() {
  const [data, setData] = useState<SpendingSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [chains, setChains] = useState<SupplyChain[] | null>(null);
  const [spot, setSpot] = useState<Spotlight | null>(null);
  const layout = useRef<GlobeLayout>({ ...LAYOUTS.wide.entry });
  const dashRef = useRef<HTMLDivElement>(null);

  const load = useCallback(() => {
    setErr(null);
    setData(null);
    setChains(null);
    api.summary().then(setData).catch((e) => setErr(String(e)));
    api.allSupplyChains().then(setChains).catch(() => setChains([]));
  }, []);
  useEffect(load, [load]);

  // Scroll-linked globe placement: progress over the first ~70% of a screen, eased inside the globe.
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 900px)");
    let raf = 0;
    const update = () => {
      raf = 0;
      const set = mq.matches ? LAYOUTS.narrow : LAYOUTS.wide;
      const p = Math.min(1, Math.max(0, window.scrollY / (window.innerHeight * 0.7)));
      const e = 1 - Math.pow(1 - p, 3);
      dashRef.current?.style.setProperty("--p", e.toFixed(3));
      layout.current = {
        cx: lerp(set.entry.cx, set.docked.cx, e),
        cy: lerp(set.entry.cy, set.docked.cy, e),
        r: lerp(set.entry.r, set.docked.r, e),
      };
    };
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  const model = useMemo(() => {
    if (!data) return null;
    const ch = chains ?? [];
    const chainById = new Map(ch.map((c) => [c.merchant_id, c]));
    const originOf = (id: string) => chainById.get(id)?.stages[0];
    const where = whereItStarts(data.merchants, ch);
    const hab = habits(data.merchants, data.total_spent);
    const latest = data.merchants.reduce((a, m) => (m.last_purchase > a ? m.last_purchase : a), "");
    const names = Object.fromEntries(data.merchants.map((m) => [m.merchant_id, m.name]));
    // Frame a single route on all of its stops (so it sits on the visible face); a group on its origins.
    const spotFor = (ids: string[]): Spotlight | null => {
      const stops = ids.length === 1 ? chainById.get(ids[0])?.stages ?? [] : ids.map(originOf).filter(Boolean);
      const pts = stops.map((p) => [p!.lng, p!.lat] as [number, number]);
      return pts.length ? { ids, focus: sphericalMean(pts) } : null;
    };
    return { chainById, originOf, where, hab, latest, names, spotFor };
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

  const hasRoutes = !!chains?.length;
  const lightUp = (ids: string[]) => () => setSpot(model?.spotFor(ids) ?? null);
  const clear = () => setSpot(null);

  return (
    <div className="dash" ref={dashRef}>
      <div className="globe-backdrop">
        {hasRoutes && (
          <Globe
            chains={chains!}
            highlight={spot?.ids ?? null}
            focus={spot?.focus ?? null}
            labels={model?.names}
            layoutRef={layout}
            tweenMs={1100}
          />
        )}
      </div>

      {/* 1. Entry: the globe is the page; the money sits quietly in front of it. */}
      <section className="dash-hero" aria-label="Your spending">
        {!data || !model ? (
          <p className="dash-loading" role="status">Loading your spending…</p>
        ) : (
          <div className="dash-hero-copy">
            <h1 className="dash-display">Where your<br />money went.</h1>
            <p className="dash-total">
              <span className="figure">{usd(data.total_spent)}</span>
              {model.latest && <span className="dash-total-note">spent through {shortDate(model.latest)}</span>}
            </p>
            <dl className="dash-stats">
              <div><dt>companies</dt><dd>{data.merchant_count}</dd></div>
              <div><dt>purchases</dt><dd>{model.hab.purchases}</dd></div>
              {hasRoutes && <div><dt>continents of origin</dt><dd>{model.where.continents.length}</dd></div>}
              {hasRoutes && <div><dt>starts outside the US</dt><dd>{pct(model.where.abroadShare)}</dd></div>}
            </dl>
            {hasRoutes && (
              <p className="dash-hero-caption">
                Each line is one example product's route to you, not yet verified. Drag the globe to spin it.
              </p>
            )}
          </div>
        )}
        <p className="scroll-cue" aria-hidden="true">Scroll for the companies</p>
      </section>

      {/* 2. The sheet slides up over the globe. */}
      {data && model && (
        <div className="dash-sheet">
          <MockBanner />

          {hasRoutes && (
            <section aria-labelledby="glance-title">
              <h2 id="glance-title" className="sheet-title">At a glance</h2>
              <ul className="glance" onMouseLeave={clear}>
                {model.where.abroadIds.length > 0 && (
                  <li>
                    <button type="button" className="glance-item" onClick={lightUp(model.where.abroadIds)} onMouseEnter={lightUp(model.where.abroadIds)} onFocus={lightUp(model.where.abroadIds)} onBlur={clear}>
                      <span className="glance-figure">{pct(model.where.abroadShare)}</span>
                      <span>of your spending goes to companies whose example product starts outside the US</span>
                    </button>
                  </li>
                )}
                {model.where.continents[0] && (
                  <li>
                    <button type="button" className="glance-item" onClick={lightUp(model.where.continents[0].merchantIds)} onMouseEnter={lightUp(model.where.continents[0].merchantIds)} onFocus={lightUp(model.where.continents[0].merchantIds)} onBlur={clear}>
                      <span className="glance-figure">{pct(model.where.continents[0].share)}</span>
                      <span>starts in {model.where.continents[0].continent}, the most of any continent</span>
                      <span className="split" aria-hidden="true">
                        {model.where.continents.map((c, i) => (
                          <span key={c.continent} style={{ flexGrow: c.amount, background: TINTS[Math.min(i, TINTS.length - 1)] }} />
                        ))}
                      </span>
                      <span className="split-key">
                        {model.where.continents.map((c, i) => (
                          <span key={c.continent}>
                            <i style={{ background: TINTS[Math.min(i, TINTS.length - 1)] }} />
                            {c.continent} {usd0(c.amount)}
                          </span>
                        ))}
                      </span>
                    </button>
                  </li>
                )}
                {model.hab.mostFrequent && (
                  <li>
                    <button type="button" className="glance-item" onClick={lightUp([model.hab.mostFrequent.merchant_id])} onMouseEnter={lightUp([model.hab.mostFrequent.merchant_id])} onFocus={lightUp([model.hab.mostFrequent.merchant_id])} onBlur={clear}>
                      <span className="glance-figure">{model.hab.mostFrequent.purchase_count}×</span>
                      <span>
                        {model.hab.mostFrequent.name} is your most frequent stop, about{" "}
                        {usd(model.hab.mostFrequent.total_spent / model.hab.mostFrequent.purchase_count)} per visit
                      </span>
                    </button>
                  </li>
                )}
              </ul>
            </section>
          )}

          <section aria-labelledby="companies-title" className="sheet-section">
            <div className="sheet-head">
              <h2 id="companies-title" className="sheet-title">Companies you buy from</h2>
              <p>Most spent first. Point at one (or tap it once) to see its route; example origins are not yet verified.</p>
            </div>
            {data.merchants.length === 0 ? (
              <p className="sheet-empty">
                No purchases yet. When transactions come in from your account, the companies you pay will show up here.
              </p>
            ) : (
              <ul className="company-list" onMouseLeave={clear}>
                {data.merchants.map((m) => (
                  <CompanyRow
                    key={m.merchant_id}
                    m={m}
                    total={data.total_spent}
                    chain={model.chainById.get(m.merchant_id)}
                    active={spot?.ids.length === 1 && spot.ids[0] === m.merchant_id}
                    onEnter={lightUp([m.merchant_id])}
                    onLeave={clear}
                  />
                ))}
              </ul>
            )}
          </section>

          <section className="values-cta" aria-labelledby="values-cta-title">
            <h2 id="values-cta-title">What matters to you?</h2>
            <p>
              Tell Accountable which issues you care about, like how workers are treated or the environment, so you can
              compare each company against them.
            </p>
            <Link to="/values" className="button-light">Set your values</Link>
          </section>
        </div>
      )}
    </div>
  );
}

function CompanyRow({
  m, total, chain, active, onEnter, onLeave,
}: {
  m: MerchantSummary;
  total: number;
  chain?: SupplyChain;
  active: boolean;
  onEnter: () => void;
  onLeave: () => void;
}) {
  const o = chain?.stages[0];
  const share = total ? m.total_spent / total : 0;
  return (
    <li>
      <Link
        to={`/merchant/${m.merchant_id}`}
        className={`company-row ${active ? "is-active" : ""}`}
        onClick={(e) => {
          // Touch has no hover: the first tap lights the route on the globe, the second opens the page.
          if (!active && window.matchMedia("(hover: none)").matches) {
            e.preventDefault();
            onEnter();
          }
        }}
        onMouseEnter={onEnter}
        onFocus={onEnter}
        onBlur={onLeave}
      >
        <span className="cr-main">
          <span className="cr-name">
            {m.name}
            {o && <span className="tag">{continentOf(o.lat, o.lng)}</span>}
          </span>
          {chain && o && (
            <span className="cr-origin">
              {chain.product}, starts in {o.place} <span className="unverified">(unverified)</span>
            </span>
          )}
        </span>
        <span className="cr-nums">
          <span className="cr-spend">{usd(m.total_spent)}</span>
          <span className="cr-meta">
            {pct(share)} · {m.purchase_count} {m.purchase_count === 1 ? "purchase" : "purchases"} · last {shortDate(m.last_purchase)}
          </span>
        </span>
        {active && <span className="cr-open">Tap again to open</span>}
        <span className="cr-share" aria-hidden="true"><span style={{ width: `${Math.max(share * 100, 2)}%` }} /></span>
      </Link>
    </li>
  );
}

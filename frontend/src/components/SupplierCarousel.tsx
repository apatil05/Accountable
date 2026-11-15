// Company page suppliers as a carousel: one large slide centered at a time, neighbors peeking in, linked both
// ways to the globe. Owner: Person C. A swipe or arrow press that settles on a slide selects that supplier (the
// globe flies there); selecting a supplier on the globe slides its card to the center. Only user scrolls select,
// never the first render or a scroll we started ourselves.
import { useEffect, useRef, useState } from "react";
import type { Supplier } from "../api/types";
import { mapLinkFor } from "../lib/places";
import { isPlaced, supplierCountry, supplierPointId } from "../lib/suppliers";
import { NearbyList } from "./SupplierCard";

const SETTLE_MS = 120;
const reducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export default function SupplierCarousel({
  companyId, suppliers, selected, onSelect, onShowGlobe,
}: {
  companyId: string;
  suppliers: Supplier[];
  selected: number | null;
  onSelect: (i: number) => void;
  onShowGlobe?: () => void; // the "Show on the globe" button: bring the globe into view
}) {
  const track = useRef<HTMLUListElement>(null);
  const userScrolling = useRef(false);
  const settle = useRef(0);
  const [current, setCurrent] = useState(0); // the centered slide, selected or not
  const [live, setLive] = useState<number | null>(null); // lit while the user is scrolling, before the globe catches up
  const raf = useRef(0);
  // Read the latest values inside the debounced scroll handler without re-binding it.
  const latest = useRef({ selected, onSelect });
  latest.current = { selected, onSelect };

  const slides = () => Array.from(track.current?.children ?? []) as HTMLElement[];
  const centerOn = (i: number) => {
    const el = track.current;
    const s = slides()[i];
    if (!el || !s) return;
    el.scrollTo({ left: s.offsetLeft + s.offsetWidth / 2 - el.clientWidth / 2, behavior: reducedMotion() ? "auto" : "smooth" });
  };

  // Globe -> carousel: slide the selected supplier to the center (horizontal only; the page never jumps).
  useEffect(() => {
    if (selected != null) {
      setCurrent(selected);
      centerOn(selected);
    }
  }, [selected]);

  useEffect(() => () => {
    window.clearTimeout(settle.current);
    cancelAnimationFrame(raf.current);
  }, []);

  // The slide whose center is nearest the middle of the row.
  const nearest = () => {
    const el = track.current;
    if (!el) return 0;
    const mid = el.scrollLeft + el.clientWidth / 2;
    let best = 0;
    let bestDist = Infinity;
    slides().forEach((s, i) => {
      const d = Math.abs(s.offsetLeft + s.offsetWidth / 2 - mid);
      if (d < bestDist) {
        best = i;
        bestDist = d;
      }
    });
    return best;
  };

  // Carousel -> globe. While the user scrolls, light the nearest slide right away (once per frame).
  // The globe only flies once the scroll settles, so it doesn't swing past every slide on the way.
  const onScroll = () => {
    if (userScrolling.current && !raf.current) {
      raf.current = requestAnimationFrame(() => {
        raf.current = 0;
        const i = nearest();
        setCurrent(i);
        setLive(i);
      });
    }
    window.clearTimeout(settle.current);
    settle.current = window.setTimeout(() => {
      const best = nearest();
      setCurrent(best);
      if (userScrolling.current && best !== latest.current.selected) latest.current.onSelect(best);
      userScrolling.current = false;
      setLive(null);
    }, SETTLE_MS);
  };
  const markUser = () => {
    userScrolling.current = true;
  };

  const go = (i: number) => {
    const next = Math.min(suppliers.length - 1, Math.max(0, i));
    setCurrent(next);
    centerOn(next);
    if (next !== selected) onSelect(next);
  };
  const many = suppliers.length > 1;

  return (
    <div className="co-carousel-wrap">
      <div className="co-carousel-stage">
        <ul
          ref={track}
          className="co-carousel"
          role="region"
          aria-roledescription="carousel"
          aria-label="Suppliers"
          tabIndex={0}
          onScroll={onScroll}
          onPointerDown={markUser}
          onWheel={markUser}
          onTouchStart={markUser}
          onKeyDown={markUser}
        >
          {suppliers.map((s, i) => (
            <li
              key={supplierPointId(companyId, i)}
              className={i === current ? "is-current" : ""}
              aria-roledescription="slide"
              aria-label={`${i + 1} of ${suppliers.length}`}
            >
              {/* The whole slide selects; links inside don't, so following one never moves the globe. */}
              <div className={`co-slide ${(live ?? selected) === i ? "is-active" : ""}`} onClick={() => go(i)}>
                <span className="co-slide-photo">
                  {s.place?.thumbnail_url ? <img src={s.place.thumbnail_url} alt="" /> : <span className="co-supplier-mark" aria-hidden="true" />}
                </span>
                <div className="co-slide-main">
                  <span className="co-slide-where">
                    {isPlaced(s) ? [s.place?.title, supplierCountry(s)].filter(Boolean).join(" · ") : "Not on the map"}
                  </span>
                  <h3 className="co-slide-name">{s.name}</h3>
                  {s.address && <p className="co-slide-address">{s.address}</p>}
                  {s.shipments != null && (
                    <p className="co-slide-figure">
                      {s.shipments.toLocaleString("en-US")} <span>shipments to the US</span>
                    </p>
                  )}
                  {isPlaced(s) && <button
                    type="button"
                    className="co-slide-cta"
                    onClick={(e) => {
                      e.stopPropagation();
                      go(i);
                      onShowGlobe?.();
                    }}
                    aria-pressed={selected === i}
                  >
                    {selected === i ? "Shown on the globe" : "Show on the globe"}
                  </button>}
                </div>
                <div className="co-slide-more" onClick={(e) => e.stopPropagation()}>
                  {s.place?.summary && <p className="co-slide-summary">{s.place.summary}</p>}
                  <p className="co-slide-links">
                    {s.place && <a href={s.place.url} target="_blank" rel="noreferrer">About {s.place.title}</a>}
                    {isPlaced(s) && <a href={mapLinkFor(s)} target="_blank" rel="noreferrer">Approximate location on OpenStreetMap</a>}
                  </p>
                  {s.approximate && (
                    <p className="co-slide-summary">Approximate pin: this address couldn’t be confirmed, so it’s the geocoder’s best guess and may be off.</p>
                  )}
                  <NearbyList nearby={s.nearby} max={3} />
                </div>
              </div>
            </li>
          ))}
        </ul>
        {many && (
          <>
            <button type="button" className="co-carousel-arrow is-prev" onClick={() => go(current - 1)} disabled={current === 0} aria-label="Previous supplier">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3L5 8l5 5" /></svg>
            </button>
            <button type="button" className="co-carousel-arrow is-next" onClick={() => go(current + 1)} disabled={current === suppliers.length - 1} aria-label="Next supplier">
              <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3l5 5-5 5" /></svg>
            </button>
          </>
        )}
      </div>
      {many && (
        <div className="co-carousel-dots" role="group" aria-label="Choose a supplier">
          {suppliers.map((s, i) => (
            <button
              key={i}
              type="button"
              className={i === current ? "is-current" : ""}
              onClick={() => go(i)}
              aria-label={`Supplier ${i + 1}: ${s.name}`}
              aria-current={i === current}
            />
          ))}
          <span className="co-carousel-count" aria-live="polite">{current + 1} of {suppliers.length}</span>
        </div>
      )}
    </div>
  );
}

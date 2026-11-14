// Spinnable 3D globe of supply routes. Owner: Person C. Used by Landing, Dashboard and the Map page.
// Orthographic d3-geo projection drawn to canvas (no extra packages, works offline).
//  - drag / arrow keys spin it; wheel and +/- zoom it (when `zoomable`)
//  - `focus` turns it to a place, `zoom` scales it, `highlight` lights only those routes
//  - `layoutRef` lets a page move/resize the globe every frame (e.g. scroll-linked) without re-rendering
//  - `onStopClick` reports which supply-chain stop was clicked (for stop details / place info)
import { useEffect, useRef, type MutableRefObject } from "react";
import { geoDistance, geoGraticule10, geoOrthographic, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature, mesh } from "topojson-client";
import type { Topology } from "topojson-specification";
import type { LineString } from "geojson";
import world from "world-atlas/countries-110m.json";
import type { SupplyChain } from "../api/types";

const topo = world as unknown as Topology;
const LAND = feature(topo, topo.objects.land) as unknown as GeoPermissibleObjects;
const BORDERS = mesh(topo, topo.objects.countries as never, (a, b) => a !== b) as unknown as GeoPermissibleObjects;
const GRATICULE = geoGraticule10();
const SPHERE = { type: "Sphere" } as GeoPermissibleObjects;
const FONT = `"Hanken Grotesk", ui-sans-serif, system-ui, sans-serif`;

// Deep atlas palette (see DESIGN.md). Land is a step lighter than the field so continents read on a projector.
const C = {
  ocean: ["#152c40", "#10222f", "#0b1822"],
  land: "#243f57",
  border: "rgba(255,255,255,0.07)",
  grid: "rgba(255,255,255,0.045)",
  route: "242,176,74",
  selected: "255,255,255",
  plate: "rgba(15,31,43,0.96)",
};

/** Where the globe sits in its canvas: center as fractions of width/height, radius as a fraction of min(w,h)/2. */
export interface GlobeLayout {
  cx: number;
  cy: number;
  r: number;
}
export interface StopRef {
  merchantId: string;
  index: number;
}

interface Props {
  chains: SupplyChain[];
  highlight?: string[] | null;
  focus?: [number, number] | null;
  /** Zoom multiplier (1 = whole globe). */
  zoom?: number;
  onZoomChange?: (z: number) => void;
  zoomable?: boolean;
  labels?: Record<string, string>;
  /** Target layout, read every frame and eased toward. */
  layoutRef?: MutableRefObject<GlobeLayout>;
  /** false = decorative backdrop: no pointer/keyboard input, no labels. */
  interactive?: boolean;
  /** Label every stop of lit routes (Map page), not just their origins. */
  labelAllStops?: boolean;
  selectedStop?: StopRef | null;
  onStopClick?: (stop: StopRef) => void;
  /** Degrees per frame of idle spin. */
  spin?: number;
  tweenMs?: number;
  className?: string;
  ariaLabel?: string;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

// Deterministic pseudo-random numbers, so stars and city sprawl look the same on every load.
function rng(seed: number) {
  return () => {
    seed = (seed * 1664525 + 1013904223) % 4294967296;
    return seed / 4294967296;
  };
}

interface Star { x: number; y: number; r: number; a: number; tw: number }
function makeStars(w: number, h: number): Star[] {
  const r = rng(Math.round(w * 7 + h));
  const n = Math.min(700, Math.round((w * h) / 2600));
  return Array.from({ length: n }, () => ({
    x: r() * w, y: r() * h,
    r: r() < 0.92 ? 0.4 + r() * 0.6 : 1 + r() * 0.7,
    a: 0.25 + r() * 0.6,
    tw: r() * Math.PI * 2,
  }));
}
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));
const DEFAULT_LAYOUT: GlobeLayout = { cx: 0.5, cy: 0.5, r: 0.94 };

export default function Globe({
  chains,
  highlight = null,
  focus = null,
  zoom = 1,
  onZoomChange,
  zoomable = false,
  labels = {},
  layoutRef,
  interactive = true,
  labelAllStops = false,
  selectedStop = null,
  onStopClick,
  spin = 0.05,
  tweenMs = 1500,
  className,
  ariaLabel,
}: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const reduced =
    typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  // Mutable render state lives in a ref so the animation loop never re-renders React.
  const s = useRef({
    rot: [75, -18] as [number, number],
    tween: null as null | { from: [number, number]; to: [number, number]; start: number; dur: number },
    drag: null as null | { x: number; y: number; moved: number },
    resumeAt: 0,
    visible: true,
    size: { w: 0, h: 0, dpr: 1 },
    layout: { ...(layoutRef?.current ?? DEFAULT_LAYOUT) },
    zoom: zoom,
    stops: [] as { x: number; y: number; ref: StopRef }[],
    stars: [] as Star[],
    props: { chains, highlight, labels, focus, zoom, labelAllStops, selectedStop, interactive, spin },
  });
  s.current.props = { chains, highlight, labels, focus, zoom, labelAllStops, selectedStop, interactive, spin };

  // Turn toward the focus point whenever it changes.
  useEffect(() => {
    const st = s.current;
    if (!focus) return;
    const to: [number, number] = [-focus[0], clamp(-focus[1], -60, 60)];
    if (reduced) {
      st.rot = to;
      st.tween = null;
      return;
    }
    const from = st.rot;
    const dl = ((to[0] - from[0] + 540) % 360) - 180; // shortest way round
    st.tween = { from, to: [from[0] + dl, to[1]], start: performance.now(), dur: tweenMs };
  }, [focus?.[0], focus?.[1], reduced, tweenMs]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const wrap = wrapRef.current!;
    const canvas = canvasRef.current!;
    const ctx = canvas.getContext("2d")!;
    const st = s.current;

    const resize = () => {
      const r = wrap.getBoundingClientRect();
      // Large canvases (full-screen backdrops) cap their pixel ratio to stay smooth.
      const dpr = Math.min(window.devicePixelRatio || 1, r.width * r.height > 900_000 ? 1.5 : 2);
      st.size = { w: r.width, h: r.height, dpr };
      st.stars = makeStars(r.width, r.height);
      canvas.width = Math.round(r.width * dpr);
      canvas.height = Math.round(r.height * dpr);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    const io = new IntersectionObserver(([e]) => (st.visible = e.isIntersecting));
    io.observe(wrap);

    let raf = 0;
    const draw = (now: number) => {
      raf = requestAnimationFrame(draw);
      if (!st.visible || !st.size.w) return;
      const { w, h, dpr } = st.size;
      const { chains, highlight, labels, focus, labelAllStops, selectedStop, spin } = st.props;

      // Ease layout and zoom toward their targets: continuous, never snapping.
      const target = layoutRef?.current ?? DEFAULT_LAYOUT;
      const k = reduced ? 1 : 0.12;
      st.layout = {
        cx: st.layout.cx + (target.cx - st.layout.cx) * k,
        cy: st.layout.cy + (target.cy - st.layout.cy) * k,
        r: st.layout.r + (target.r - st.layout.r) * k,
      };
      st.zoom += (st.props.zoom - st.zoom) * (reduced ? 1 : 0.14);

      // Rotation: tween > drag > idle spin (only when nothing is focused).
      if (st.tween) {
        const t = easeOutExpo((now - st.tween.start) / st.tween.dur);
        st.rot = [
          st.tween.from[0] + (st.tween.to[0] - st.tween.from[0]) * t,
          st.tween.from[1] + (st.tween.to[1] - st.tween.from[1]) * t,
        ];
        if (t >= 1) st.tween = null;
      } else if (!st.drag && !focus && !reduced && now > st.resumeAt) {
        st.rot = [st.rot[0] + spin, st.rot[1]];
      }

      const r = (Math.min(w, h) / 2) * st.layout.r * st.zoom;
      const cx = w * st.layout.cx;
      const cy = h * st.layout.cy;
      const proj = geoOrthographic().scale(r).translate([cx, cy]).rotate([st.rot[0], st.rot[1]]).clipAngle(90);
      const path = geoPath(proj, ctx);
      const center: [number, number] = [-st.rot[0], -st.rot[1]];
      const visible = (p: [number, number]) => geoDistance(p, center) < Math.PI / 2 - 0.02;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      // Night sky: a quiet starfield that drifts slowly with the spin and twinkles very slightly.
      const drift = ((st.rot[0] * 0.8) % w + w) % w;
      for (const sr of st.stars) {
        const x = (sr.x + drift) % w;
        const tw = reduced ? 1 : 0.78 + 0.22 * Math.sin(now / 1400 + sr.tw);
        ctx.globalAlpha = sr.a * tw;
        ctx.fillStyle = "#e8eef6";
        ctx.beginPath();
        ctx.arc(x, sr.y, sr.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      const halo = ctx.createRadialGradient(cx, cy, r * 0.98, cx, cy, r * 1.12);
      halo.addColorStop(0, "rgba(140,190,235,0.16)");
      halo.addColorStop(1, "rgba(140,190,235,0)");
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(cx, cy, r * 1.12, 0, Math.PI * 2);
      ctx.fill();

      const sea = ctx.createRadialGradient(cx - r * 0.35, cy - r * 0.4, r * 0.1, cx, cy, r);
      sea.addColorStop(0, C.ocean[0]);
      sea.addColorStop(0.6, C.ocean[1]);
      sea.addColorStop(1, C.ocean[2]);
      ctx.beginPath();
      path(SPHERE);
      ctx.fillStyle = sea;
      ctx.fill();

      ctx.beginPath();
      path(GRATICULE);
      ctx.strokeStyle = C.grid;
      ctx.lineWidth = 0.6;
      ctx.stroke();

      ctx.beginPath();
      path(LAND);
      ctx.fillStyle = C.land;
      ctx.fill();
      ctx.beginPath();
      path(BORDERS);
      ctx.strokeStyle = C.border;
      ctx.lineWidth = 0.5;
      ctx.stroke();


      // Routes: the lit set in white with a slow flow, everything else in the warm accent.
      const lit = highlight && highlight.length ? new Set(highlight) : null;
      const order = lit ? [...chains].sort((a, b) => Number(lit.has(a.merchant_id)) - Number(lit.has(b.merchant_id))) : chains;
      ctx.lineCap = "round";
      for (const c of order) {
        const on = lit?.has(c.merchant_id);
        const line: LineString = { type: "LineString", coordinates: c.stages.map((p) => [p.lng, p.lat]) };
        ctx.beginPath();
        path(line);
        if (on) {
          ctx.strokeStyle = `rgba(${C.selected},0.95)`;
          ctx.lineWidth = 2.2;
          ctx.setLineDash(reduced ? [] : [6, 6]);
          ctx.lineDashOffset = reduced ? 0 : -(now / 60) % 12;
        } else {
          ctx.strokeStyle = `rgba(${C.route},${lit ? 0.16 : 0.8})`;
          ctx.lineWidth = 1.5;
          ctx.setLineDash([]);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // Stops (dots), remembered for hit-testing; labels are queued and drawn last on plates.
      const labelQueue: { text: string; x: number; y: number; weight: number; align?: CanvasTextAlign }[] = [];
      st.stops = [];
      const showLabels = st.props.interactive;
      for (const c of order) {
        const on = lit?.has(c.merchant_id);
        c.stages.forEach((p, i) => {
          const ll: [number, number] = [p.lng, p.lat];
          if (i === c.stages.length - 1 || !visible(ll)) return;
          const [x, y] = proj(ll)!;
          const isSel = selectedStop?.merchantId === c.merchant_id && selectedStop.index === i;
          ctx.beginPath();
          ctx.arc(x, y, isSel ? 5 : on && i === 0 ? 3.5 : on ? 2.6 : 1.8, 0, Math.PI * 2);
          ctx.fillStyle = on || isSel ? `rgba(${C.selected},1)` : `rgba(${C.route},${lit ? 0.25 : 0.9})`;
          ctx.fill();
          if (isSel) {
            ctx.beginPath();
            ctx.arc(x, y, 10, 0, Math.PI * 2);
            ctx.strokeStyle = `rgba(${C.route},0.9)`;
            ctx.lineWidth = 1.5;
            ctx.stroke();
          }
          if (on || !lit) st.stops.push({ x, y, ref: { merchantId: c.merchant_id, index: i } });
          if (!showLabels || !on || !lit) return;
          if (i === 0 && lit.size <= 3) {
            labelQueue.push({ text: `${labels[c.merchant_id] ?? c.product} · ${p.place}`, x: x + 9, y: y - 9, weight: 500 });
          } else if (i > 0 && labelAllStops && lit.size === 1) {
            labelQueue.push({ text: p.place, x: x + 9, y: y - 9, weight: 400 });
          }
        });
      }

      // Terminus: where every route ends.
      const end = chains[0]?.stages[chains[0].stages.length - 1];
      if (end && visible([end.lng, end.lat])) {
        const [x, y] = proj([end.lng, end.lat])!;
        const pulse = reduced ? 0.5 : (now % 2400) / 2400;
        ctx.beginPath();
        ctx.arc(x, y, 5 + pulse * 10, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,255,255,${0.5 * (1 - pulse)})`;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(x, y, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = "#fff";
        ctx.fill();
        if (showLabels) {
          const text = `You · ${end.place}`;
          ctx.font = `600 12px ${FONT}`;
          const tw = ctx.measureText(text).width;
          const fitsRight = x + 12 + tw < w - 6;
          labelQueue.push({ text, x: fitsRight ? x + 12 : Math.max(x - 12, tw + 6), y: y + 18, weight: 600, align: fitsRight ? "left" : "right" });
        }
      }

      // Labels sit on a field-colored plate, so no route can show through gaps between glyphs.
      // Heavier labels (you, origins) are placed first; any later label that would collide is skipped.
      const placed: { x: number; y: number; w: number; h: number }[] = [];
      for (const l of [...labelQueue].sort((a, b) => b.weight - a.weight)) {
        ctx.font = `${l.weight} 12px ${FONT}`;
        const tw = ctx.measureText(l.text).width;
        const hits = (bx: { x: number; y: number; w: number; h: number }) =>
          bx.x < 0 || bx.x + bx.w > w ||
          placed.some((b) => bx.x < b.x + b.w + 2 && b.x < bx.x + bx.w + 2 && bx.y < b.y + b.h + 2 && b.y < bx.y + bx.h + 2);
        // Preferred spot, then mirrored to the other side of the dot, then below it; drop only if all collide.
        const anchorX = l.align === "right" ? l.x + 12 : l.x - 9; // the dot the label belongs to
        const tries: { x: number; y: number; align: CanvasTextAlign }[] = [
          { x: l.x, y: l.y, align: l.align ?? "left" },
          { x: l.align === "right" ? anchorX + 9 : anchorX - 9, y: l.y, align: l.align === "right" ? "left" : "right" },
          { x: anchorX + 9, y: l.y + 26, align: "left" },
        ];
        const fit = tries
          .map((t) => ({ t, box: { x: (t.align === "right" ? t.x - tw : t.x) - 6, y: t.y - 13, w: tw + 12, h: 19 } }))
          .find((c) => !hits(c.box));
        if (!fit) continue;
        placed.push(fit.box);
        l.x = fit.t.x;
        l.y = fit.t.y;
        l.align = fit.t.align;
        const left = fit.box.x + 6;
        ctx.beginPath();
        ctx.roundRect(left - 6, l.y - 13, tw + 12, 19, 6);
        ctx.fillStyle = C.plate;
        ctx.fill();
        ctx.textAlign = l.align ?? "left";
        ctx.fillStyle = "rgba(255,255,255,0.96)";
        ctx.fillText(l.text, l.x, l.y);
      }
      ctx.textAlign = "left";

      ctx.beginPath();
      path(SPHERE);
      ctx.strokeStyle = "rgba(255,255,255,0.10)";
      ctx.lineWidth = 1;
      ctx.stroke();
    };
    raf = requestAnimationFrame(draw);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      io.disconnect();
    };
  }, [reduced]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Input ----
  const setZoom = (z: number) => onZoomChange?.(clamp(z, 1, 8));
  const onPointerDown = (e: React.PointerEvent) => {
    const st = s.current;
    st.drag = { x: e.clientX, y: e.clientY, moved: 0 };
    st.tween = null;
    (e.target as Element).setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const st = s.current;
    if (!st.drag) return;
    const dx = e.clientX - st.drag.x;
    const dy = e.clientY - st.drag.y;
    // Spin speed scales with the drawn radius, so a zoomed-in globe moves under the finger, not past it.
    const radius = (Math.min(st.size.w, st.size.h) / 2) * st.layout.r * st.zoom;
    const k = 57 / Math.max(120, radius);
    st.rot = [st.rot[0] + dx * k, clamp(st.rot[1] - dy * k, -75, 75)];
    st.drag = { x: e.clientX, y: e.clientY, moved: st.drag.moved + Math.abs(dx) + Math.abs(dy) };
  };
  const endDrag = (e: React.PointerEvent) => {
    const st = s.current;
    const click = st.drag && st.drag.moved < 5;
    st.drag = null;
    st.resumeAt = performance.now() + 4000;
    if (click && onStopClick) {
      const rect = canvasRef.current!.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      let best: { d: number; ref: StopRef } | null = null;
      for (const sp of st.stops) {
        const d = Math.hypot(sp.x - px, sp.y - py);
        if (d < 14 && (!best || d < best.d)) best = { d, ref: sp.ref };
      }
      if (best) onStopClick(best.ref);
    }
  };
  const onWheel = (e: React.WheelEvent) => {
    if (!zoomable) return;
    setZoom(s.current.props.zoom * Math.exp(-e.deltaY * 0.0015));
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const st = s.current;
    if (zoomable && (e.key === "+" || e.key === "=")) return setZoom(st.props.zoom * 1.4);
    if (zoomable && (e.key === "-" || e.key === "_")) return setZoom(st.props.zoom / 1.4);
    const step: Record<string, [number, number]> = { ArrowLeft: [10, 0], ArrowRight: [-10, 0], ArrowUp: [0, -8], ArrowDown: [0, 8] };
    const d = step[e.key];
    if (!d) return;
    e.preventDefault();
    st.tween = null;
    const f = 1 / st.props.zoom;
    st.rot = [st.rot[0] + d[0] * f, clamp(st.rot[1] + d[1] * f, -75, 75)];
    st.resumeAt = performance.now() + 4000;
  };

  return (
    <div ref={wrapRef} className={`globe ${interactive ? "" : "globe-static"} ${className ?? ""}`}>
      <canvas
        ref={canvasRef}
        tabIndex={interactive ? 0 : -1}
        role="img"
        aria-roledescription={interactive ? "interactive globe" : undefined}
        aria-label={
          ariaLabel ??
          `Globe showing ${chains.length} example supply routes ending where you live.${interactive ? " Drag, or use the arrow keys, to spin it." : ""}`
        }
        {...(interactive
          ? { onPointerDown, onPointerMove, onPointerUp: endDrag, onPointerCancel: endDrag, onKeyDown, onWheel }
          : {})}
      />
    </div>
  );
}

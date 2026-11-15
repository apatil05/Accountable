// Spinnable 3D globe of real supplier routes. Owner: Person C. Used by Landing, Auth, Dashboard, Map, company pages.
// Orthographic d3-geo projection drawn to canvas (no extra packages, works offline).
//  - routes: great-circle lines (supplier -> the company's US importer), grouped by company id
//  - points: suppliers (diamonds) and importers (rings), clickable
//  - highlight lights one or more companies; focus turns the globe; zoom scales it; layoutRef moves it per frame
//  - zooming (when `zoomable`): pinch (trackpad or touch), double-click, Ctrl/⌘ + scroll, keyboard +/-, or plain
//    scroll when wheel="zoom". It zooms toward the pointer, and loads finer coastlines once you're zoomed in.
import { useEffect, useRef, type MutableRefObject } from "react";
import { geoDistance, geoGraticule10, geoOrthographic, geoPath, type GeoPermissibleObjects } from "d3-geo";
import { feature, mesh } from "topojson-client";
import type { Topology } from "topojson-specification";
import type { LineString } from "geojson";
import world from "world-atlas/countries-110m.json";

const topo = world as unknown as Topology;
const LAND = feature(topo, topo.objects.land) as unknown as GeoPermissibleObjects;
const BORDERS = mesh(topo, topo.objects.countries as never, (a, b) => a !== b) as unknown as GeoPermissibleObjects;
const GRATICULE = geoGraticule10();
const SPHERE = { type: "Sphere" } as GeoPermissibleObjects;
const FONT = `"Geologica", ui-sans-serif, system-ui, sans-serif`;
const MAX_ZOOM = 12;
const DETAIL_AT = 2.2; // zoom level where finer coastlines take over

// Finer 1:50m coastlines and borders, loaded once on first zoom-in (kept out of the initial bundle).
let DETAIL: { land: GeoPermissibleObjects; borders: GeoPermissibleObjects } | null = null;
let detailRequested = false;
function loadDetail() {
  if (detailRequested) return;
  detailRequested = true;
  import("world-atlas/countries-50m.json").then((m) => {
    const t = (m.default ?? m) as unknown as Topology;
    DETAIL = {
      land: feature(t, t.objects.land) as unknown as GeoPermissibleObjects,
      borders: mesh(t, t.objects.countries as never, (a, b) => a !== b) as unknown as GeoPermissibleObjects,
    };
  }).catch(() => (detailRequested = false));
}

// Vantage palette (see styles.css): the globe as a scan. Blue-black sea, land as a dot screen of steel points,
// routes in steel, and lime for what is found or lit (suppliers, importers, the company you point at).
const C = {
  ocean: ["#101a22", "#0d161d", "#0a1217"],
  land: "#8fa6b6",
  border: "rgba(0,0,0,0)",
  grid: "rgba(160,185,205,0.07)",
  route: "160,185,205",
  selected: "197,240,90",
  ink: "#eef2f5",
  plate: "rgba(11,18,24,0.94)",
  plateText: "#eef2f5",
  point: "197,240,90", // supplier / importer marks at rest
  litPoint: "#d9f78a", // marks of the lit company
  halo: "160,185,205,0.10" as string | null, // a faint rim of atmosphere; null = none
  rim: "rgba(160,185,205,0.26)",
  landDots: 3, // > 0 draws land as a dot screen at this spacing (px) instead of a solid fill
};
export type GlobePalette = typeof C;

const tiles = new Map<string, HTMLCanvasElement>();
function dotTile(color: string, step: number) {
  const key = `${color}|${step}`;
  let tile = tiles.get(key);
  if (!tile) {
    tile = document.createElement("canvas");
    tile.width = tile.height = step;
    const tc = tile.getContext("2d")!;
    tc.fillStyle = color;
    tc.beginPath();
    tc.arc(step / 2, step / 2, Math.max(0.6, step * 0.22), 0, Math.PI * 2);
    tc.fill();
    tiles.set(key, tile);
  }
  return tile;
}

/** Where the globe sits in its canvas: center as fractions of width/height, radius as a fraction of min(w,h)/2. */
export interface GlobeLayout {
  cx: number;
  cy: number;
  r: number;
}
export interface GlobeRoute {
  group: string; // company id: lights together
  from: [number, number]; // [lng, lat]
  to: [number, number];
}
export interface GlobePoint {
  id: string;
  group: string;
  lat: number;
  lng: number;
  kind: "supplier" | "importer";
  label?: string;
}

interface Props {
  routes?: GlobeRoute[];
  points?: GlobePoint[];
  /** company ids to light; null = everything at rest. */
  highlight?: string[] | null;
  /** [lng, lat] to turn toward; null = gentle auto-spin. */
  focus?: [number, number] | null;
  /** Controlled zoom (1 = whole globe). Leave undefined to let the globe manage its own zoom. */
  zoom?: number;
  onZoomChange?: (z: number) => void;
  zoomable?: boolean;
  /** "zoom": plain scroll zooms (full-screen maps). "modifier": scroll scrolls the page; pinch or Ctrl/⌘ + scroll zooms. */
  wheel?: "zoom" | "modifier";
  /** Allow two-finger pinch on touch screens (turn off where the globe must not capture page scrolling). */
  touchZoom?: boolean;
  layoutRef?: MutableRefObject<GlobeLayout>;
  /** false = decorative backdrop: no pointer/keyboard input, no labels. */
  interactive?: boolean;
  selectedPointId?: string | null;
  onPointClick?: (id: string) => void;
  spin?: number;
  tweenMs?: number;
  className?: string;
  ariaLabel?: string;
  /** Override any of the default colours (a page look). */
  palette?: Partial<GlobePalette>;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const easeOutExpo = (t: number) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));
const DEFAULT_LAYOUT: GlobeLayout = { cx: 0.5, cy: 0.5, r: 0.94 };

// Deterministic pseudo-random numbers, so the starfield looks the same on every load.
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

export default function Globe({
  routes = [],
  points = [],
  highlight = null,
  focus = null,
  zoom,
  onZoomChange,
  zoomable = false,
  wheel = "modifier",
  touchZoom = true,
  layoutRef,
  interactive = true,
  selectedPointId = null,
  onPointClick,
  spin = 0.05,
  tweenMs = 1500,
  className,
  ariaLabel,
  palette,
}: Props) {
  const pal = useRef<GlobePalette>(C);
  pal.current = palette ? { ...C, ...palette } : C;
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
    zoom: zoom ?? 1,
    targetZoom: zoom ?? 1,
    proj: null as null | ReturnType<typeof geoOrthographic>,
    pointers: new Map<number, { x: number; y: number }>(),
    pinch: null as null | { d: number },
    pinched: false,
    hits: [] as { x: number; y: number; id: string }[],
    stars: [] as Star[],
    props: { routes, points, highlight, focus, selectedPointId, interactive, spin, zoomable, wheel, onZoomChange },
  });
  s.current.props = { routes, points, highlight, focus, selectedPointId, interactive, spin, zoomable, wheel, onZoomChange };

  // A controlled zoom (e.g. the +/- buttons) sets the target the globe eases toward.
  useEffect(() => {
    if (zoom != null) s.current.targetZoom = clamp(zoom, 1, MAX_ZOOM);
  }, [zoom]);

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
      const { routes, points, highlight, focus, selectedPointId, spin, interactive } = st.props;

      // Ease layout and zoom toward their targets: continuous, never snapping.
      const target = layoutRef?.current ?? DEFAULT_LAYOUT;
      const k = reduced ? 1 : 0.12;
      st.layout = {
        cx: st.layout.cx + (target.cx - st.layout.cx) * k,
        cy: st.layout.cy + (target.cy - st.layout.cy) * k,
        r: st.layout.r + (target.r - st.layout.r) * k,
      };
      st.zoom += (st.targetZoom - st.zoom) * (reduced ? 1 : 0.16);
      if (st.zoom > DETAIL_AT) loadDetail();
      const fine = st.zoom > DETAIL_AT && DETAIL;

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
      st.proj = proj;
      const path = geoPath(proj, ctx);
      const center: [number, number] = [-st.rot[0], -st.rot[1]];
      const visible = (p: [number, number]) => geoDistance(p, center) < Math.PI / 2 - 0.02;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const C = pal.current;

      if (C.halo) {
        const rgb = C.halo.split(",").slice(0, 3).join(",");
        const halo = ctx.createRadialGradient(cx, cy, r * 0.98, cx, cy, r * 1.12);
        halo.addColorStop(0, `rgba(${C.halo})`);
        halo.addColorStop(1, `rgba(${rgb},0)`);
        ctx.fillStyle = halo;
        ctx.beginPath();
        ctx.arc(cx, cy, r * 1.12, 0, Math.PI * 2);
        ctx.fill();
      }

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
      path(fine ? DETAIL!.land : LAND);
      if (C.landDots > 0) {
        // A dot screen: the land reads as measured points, like a scan.
        ctx.fillStyle = ctx.createPattern(dotTile(C.land, C.landDots), "repeat")!;
      } else {
        ctx.fillStyle = C.land;
      }
      ctx.fill();
      ctx.beginPath();
      path(fine ? DETAIL!.borders : BORDERS);
      ctx.strokeStyle = C.border;
      ctx.lineWidth = 0.5;
      ctx.stroke();

      // Routes: lit companies in white with a slow flow, everything else in the warm accent.
      const lit = highlight && highlight.length ? new Set(highlight) : null;
      ctx.lineCap = "round";
      const ordered = lit ? [...routes].sort((a, b) => Number(lit.has(a.group)) - Number(lit.has(b.group))) : routes;
      for (const rt of ordered) {
        const on = lit?.has(rt.group);
        const line: LineString = { type: "LineString", coordinates: [rt.from, rt.to] };
        ctx.beginPath();
        path(line);
        if (on) {
          ctx.strokeStyle = `rgba(${C.selected},0.95)`;
          ctx.lineWidth = 2;
          ctx.setLineDash(reduced ? [] : [6, 6]);
          ctx.lineDashOffset = reduced ? 0 : -(now / 60) % 12;
        } else {
          ctx.strokeStyle = `rgba(${C.route},${lit ? 0.14 : 0.75})`;
          ctx.lineWidth = 1.4;
          ctx.setLineDash([]);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]);

      // Points: suppliers are diamonds, importers are rings. Remembered for hit-testing; labels drawn last.
      const labels: { text: string; x: number; y: number; weight: number }[] = [];
      st.hits = [];
      for (const pt of points) {
        const ll: [number, number] = [pt.lng, pt.lat];
        if (!visible(ll)) continue;
        const [x, y] = proj(ll)!;
        const on = !lit || lit.has(pt.group);
        const sel = pt.id === selectedPointId;
        ctx.globalAlpha = on ? 1 : 0.25;
        if (pt.kind === "supplier") {
          const d = sel ? 6.5 : lit && on ? 5 : 3.2;
          ctx.beginPath();
          ctx.moveTo(x, y - d);
          ctx.lineTo(x + d, y);
          ctx.lineTo(x, y + d);
          ctx.lineTo(x - d, y);
          ctx.closePath();
          ctx.fillStyle = lit && on ? C.litPoint : `rgba(${C.point},1)`;
          ctx.fill();
          if (lit && on) {
            ctx.strokeStyle = C.plate;
            ctx.lineWidth = 1.2;
            ctx.stroke();
          }
        } else {
          ctx.beginPath();
          ctx.arc(x, y, lit && on ? 4.5 : 3, 0, Math.PI * 2);
          ctx.fillStyle = C.plate;
          ctx.fill();
          ctx.strokeStyle = lit && on ? C.litPoint : `rgba(${C.point},1)`;
          ctx.lineWidth = 1.6;
          ctx.stroke();
        }
        if (sel) {
          ctx.beginPath();
          ctx.arc(x, y, 12, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(${C.route},0.95)`;
          ctx.lineWidth = 1.5;
          ctx.stroke();
        }
        ctx.globalAlpha = 1;
        if (on) st.hits.push({ x, y, id: pt.id });
        if (interactive && pt.label && (sel || (lit && on))) {
          labels.push({ text: pt.label, x: x + 11, y: y - 9, weight: sel ? 650 : pt.kind === "importer" ? 550 : 450 });
        }
      }

      // Labels on plates; heavier first; try the other side and below before dropping a colliding label.
      const placed: { x: number; y: number; w: number; h: number }[] = [];
      for (const l of labels.sort((a, b) => b.weight - a.weight)) {
        ctx.font = `${l.weight} 12px ${FONT}`;
        const tw = ctx.measureText(l.text).width;
        const hit = (b2: { x: number; y: number; w: number; h: number }) =>
          b2.x < 0 || b2.x + b2.w > w ||
          placed.some((b) => b2.x < b.x + b.w + 2 && b.x < b2.x + b2.w + 2 && b2.y < b.y + b.h + 2 && b.y < b2.y + b2.h + 2);
        const dotX = l.x - 11;
        const tries = [
          { x: l.x, y: l.y, align: "left" as CanvasTextAlign },
          { x: dotX - 11, y: l.y, align: "right" as CanvasTextAlign },
          { x: l.x, y: l.y + 26, align: "left" as CanvasTextAlign },
        ];
        const fit = tries
          .map((t) => ({ t, box: { x: (t.align === "right" ? t.x - tw : t.x) - 6, y: t.y - 13, w: tw + 12, h: 19 } }))
          .find((c) => !hit(c.box));
        if (!fit) continue;
        placed.push(fit.box);
        ctx.beginPath();
        ctx.roundRect(fit.box.x, fit.box.y, fit.box.w, fit.box.h, 6);
        ctx.fillStyle = C.plate;
        ctx.fill();
        ctx.textAlign = fit.t.align;
        ctx.fillStyle = C.plateText;
        ctx.fillText(l.text, fit.t.x, fit.t.y);
      }
      ctx.textAlign = "left";

      ctx.beginPath();
      path(SPHERE);
      ctx.strokeStyle = C.rim;
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
  // Zoom by a factor, keeping the place under (clientX, clientY) under the pointer.
  const zoomAt = (clientX: number, clientY: number, f: number) => {
    const st = s.current;
    if (!st.props.zoomable) return;
    const old = st.targetZoom;
    const nz = clamp(old * f, 1, MAX_ZOOM);
    if (nz === old) return;
    st.targetZoom = nz;
    st.props.onZoomChange?.(nz);
    const rect = canvasRef.current!.getBoundingClientRect();
    const p = st.proj?.invert?.([clientX - rect.left, clientY - rect.top]);
    const c: [number, number] = [-st.rot[0], -st.rot[1]];
    if (p && geoDistance(p as [number, number], c) < Math.PI / 2) {
      const frac = 1 - old / nz; // move the view part of the way toward the pointed-at place
      const dl = ((p[0] - c[0] + 540) % 360) - 180;
      st.rot = [-(c[0] + dl * frac), clamp(-(c[1] + (p[1] - c[1]) * frac), -75, 75)];
    }
    st.tween = null;
    st.resumeAt = performance.now() + 4000;
  };

  // Wheel needs a non-passive listener so a pinch or Ctrl+scroll zooms the globe instead of the whole page.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !interactive) return;
    const onWheel = (e: WheelEvent) => {
      const st = s.current;
      if (!st.props.zoomable) return;
      const modifier = e.ctrlKey || e.metaKey; // trackpad pinch arrives as ctrl + wheel
      if (st.props.wheel === "modifier" && !modifier) return; // let the page scroll
      e.preventDefault();
      const scale = e.deltaMode === 1 ? 33 : 1; // line-based wheels
      zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * scale * (e.ctrlKey ? 0.012 : 0.0022)));
    };
    canvas.addEventListener("wheel", onWheel, { passive: false });
    return () => canvas.removeEventListener("wheel", onWheel);
  }, [interactive]); // eslint-disable-line react-hooks/exhaustive-deps

  const onPointerDown = (e: React.PointerEvent) => {
    const st = s.current;
    st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    (e.target as Element).setPointerCapture(e.pointerId);
    st.tween = null;
    if (st.pointers.size === 2 && zoomable && touchZoom) {
      const [a, b] = [...st.pointers.values()];
      st.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) };
      st.pinched = true;
      st.drag = null;
      return;
    }
    if (st.pointers.size === 1) {
      st.pinched = false;
      st.drag = { x: e.clientX, y: e.clientY, moved: 0 };
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const st = s.current;
    if (!st.pointers.has(e.pointerId)) return;
    st.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (st.pinch && st.pointers.size === 2) {
      const [a, b] = [...st.pointers.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (st.pinch.d > 0) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, d / st.pinch.d);
      st.pinch.d = d;
      return;
    }
    if (!st.drag) return;
    const dx = e.clientX - st.drag.x;
    const dy = e.clientY - st.drag.y;
    // Spin speed scales with the drawn radius, so a zoomed-in globe moves under the finger, not past it.
    const radius = (Math.min(st.size.w, st.size.h) / 2) * st.layout.r * st.zoom;
    const k = 57 / Math.max(120, radius);
    st.rot = [st.rot[0] + dx * k, clamp(st.rot[1] - dy * k, -75, 75)];
    st.drag = { x: e.clientX, y: e.clientY, moved: st.drag.moved + Math.abs(dx) + Math.abs(dy) };
  };
  const endPointer = (e: React.PointerEvent) => {
    const st = s.current;
    st.pointers.delete(e.pointerId);
    if (st.pointers.size < 2) st.pinch = null;
    const click = st.drag && st.drag.moved < 5 && !st.pinched;
    if (st.pointers.size === 0) {
      st.drag = null;
      st.resumeAt = performance.now() + 4000;
    }
    if (click && onPointClick) {
      const rect = canvasRef.current!.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const py = e.clientY - rect.top;
      let best: { d: number; id: string } | null = null;
      for (const hp of st.hits) {
        const d = Math.hypot(hp.x - px, hp.y - py);
        if (d < 14 && (!best || d < best.d)) best = { d, id: hp.id };
      }
      if (best) onPointClick(best.id);
    }
  };
  const onDoubleClick = (e: React.MouseEvent) => zoomAt(e.clientX, e.clientY, e.shiftKey ? 1 / 1.8 : 1.8);
  const onKeyDown = (e: React.KeyboardEvent) => {
    const st = s.current;
    const rect = canvasRef.current!.getBoundingClientRect();
    const mid: [number, number] = [rect.left + rect.width / 2, rect.top + rect.height / 2];
    if (zoomable && (e.key === "+" || e.key === "=")) return zoomAt(mid[0], mid[1], 1.4);
    if (zoomable && (e.key === "-" || e.key === "_")) return zoomAt(mid[0], mid[1], 1 / 1.4);
    const step: Record<string, [number, number]> = { ArrowLeft: [10, 0], ArrowRight: [-10, 0], ArrowUp: [0, -8], ArrowDown: [0, 8] };
    const d = step[e.key];
    if (!d) return;
    e.preventDefault();
    st.tween = null;
    const f = 1 / st.zoom;
    st.rot = [st.rot[0] + d[0] * f, clamp(st.rot[1] + d[1] * f, -75, 75)];
    st.resumeAt = performance.now() + 4000;
  };

  return (
    <div ref={wrapRef} className={`globe ${interactive ? "" : "globe-static"} ${zoomable && touchZoom ? "globe-zoomable" : ""} ${className ?? ""}`}>
      <canvas
        ref={canvasRef}
        tabIndex={interactive ? 0 : -1}
        role="img"
        aria-roledescription={interactive ? "interactive globe" : undefined}
        aria-label={
          ariaLabel ??
          `Globe showing ${routes.length} supplier routes.${interactive ? " Drag, or use the arrow keys, to spin it." : ""}`
        }
        {...(interactive
          ? { onPointerDown, onPointerMove, onPointerUp: endPointer, onPointerCancel: endPointer, onKeyDown, onDoubleClick }
          : {})}
      />
    </div>
  );
}

// Global supply-chain map. Owner: Person D.
// Flat Natural Earth projection drawn with d3-geo (no tile server, works offline).
// Routes are great-circle arcs between consecutive stages.
// Upgrade ideas: react-globe.gl for a 3D globe, or deck.gl ArcLayer.

import { useMemo, useState } from "react";
import { geoGraticule10, geoNaturalEarth1, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import type { Topology } from "topojson-specification";
import type { FeatureCollection, LineString } from "geojson";
import world from "world-atlas/countries-110m.json";
import type { SupplyChain, SupplyChainStage } from "../api/types";

const W = 960;
const H = 500;
export const ROUTE_COLORS = ["#1f6f50", "#c2562d", "#3b5ba5", "#a8326e", "#b58a12", "#4b8f8c", "#6c4fa3", "#8a5a3c", "#2f7d32", "#c23b3b"];

const land = feature(
  world as unknown as Topology,
  (world as unknown as Topology).objects.countries,
) as unknown as FeatureCollection;

interface Props {
  chains: SupplyChain[];
  highlight?: string | null; // merchant_id to emphasize
  onSelect?: (merchantId: string) => void;
  labels?: Record<string, string>; // merchant_id -> display name
}

export default function WorldMap({ chains, highlight, onSelect, labels = {} }: Props) {
  const [hover, setHover] = useState<{ s: SupplyChainStage; x: number; y: number; who: string } | null>(null);

  const { path, projection } = useMemo(() => {
    // Rotated so the map is Pacific-centered: most Asia -> US routes stay in frame
    // instead of wrapping off the edges at the antimeridian.
    const projection = geoNaturalEarth1()
      .rotate([-150, 0])
      .fitExtent([[10, 10], [W - 10, H - 10]], { type: "Sphere" });
    return { projection, path: geoPath(projection) };
  }, []);

  return (
    <div className="map-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="world-map" role="img" aria-label="Supply chain map">
        <path d={path({ type: "Sphere" }) ?? ""} className="map-sphere" />
        <path d={path(geoGraticule10()) ?? ""} className="map-graticule" />
        {land.features.map((f, i) => (
          <path key={i} d={path(f) ?? ""} className="map-land" />
        ))}

        {chains.map((c, ci) => {
          const color = ROUTE_COLORS[ci % ROUTE_COLORS.length];
          const dim = highlight && highlight !== c.merchant_id;
          const line: LineString = { type: "LineString", coordinates: c.stages.map((s) => [s.lng, s.lat]) };
          return (
            <g
              key={c.merchant_id}
              opacity={dim ? 0.12 : 1}
              style={{ cursor: onSelect ? "pointer" : undefined, transition: "opacity .25s" }}
              onClick={() => onSelect?.(c.merchant_id)}
            >
              <path d={path(line) ?? ""} className="map-route" stroke={color} />
              {c.stages.map((s, si) => {
                const [x, y] = projection([s.lng, s.lat]) ?? [0, 0];
                const last = si === c.stages.length - 1;
                return (
                  <g
                    key={s.order}
                    transform={`translate(${x},${y})`}
                    onMouseEnter={() => setHover({ s, x, y, who: labels[c.merchant_id] ?? c.product })}
                    onMouseLeave={() => setHover(null)}
                  >
                    <circle r={last ? 7 : 5} fill={last ? "#fff" : color} stroke={color} strokeWidth={last ? 3 : 1.5} />
                    {chains.length === 1 && (
                      <text y={-10} textAnchor="middle" className="map-stage-num">{s.order}</text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="map-tip" style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }}>
          <div className="map-tip-who">{hover.who}</div>
          <strong>{hover.s.order}. {hover.s.stage}</strong>
          <div>{hover.s.place}</div>
          {hover.s.description && <div className="muted">{hover.s.description}</div>}
        </div>
      )}
    </div>
  );
}

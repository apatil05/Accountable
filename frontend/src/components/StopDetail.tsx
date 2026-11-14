// One supply-chain stop: facts plus the "learn about this place" slot. Owner: Person C.
// Used on the Map page and the company page. Place info appears once lib/places.ts is wired up.
import { useEffect, useState } from "react";
import type { SupplyChainStage } from "../api/types";
import { findNearbyPlace, mapLinkFor, type PlaceInfo } from "../lib/places";

export default function StopDetail({ stage }: { stage: SupplyChainStage }) {
  const [place, setPlace] = useState<PlaceInfo | null>(null);
  useEffect(() => {
    let live = true;
    setPlace(null);
    findNearbyPlace({ lat: stage.lat, lng: stage.lng, place: stage.place }).then((p) => live && setPlace(p));
    return () => {
      live = false;
    };
  }, [stage.lat, stage.lng, stage.place]);

  return (
    <div className="stop-detail" aria-live="polite">
      <h2>{stage.place}</h2>
      <p className="stop-stage">{stage.stage} · example stop, not verified</p>
      {stage.description && <p>{stage.description}</p>}
      <p className="stop-coords">
        About {Math.abs(stage.lat).toFixed(1)}° {stage.lat >= 0 ? "N" : "S"}, {Math.abs(stage.lng).toFixed(1)}° {stage.lng >= 0 ? "E" : "W"} ·{" "}
        <a href={mapLinkFor(stage)} target="_blank" rel="noreferrer">Open in OpenStreetMap</a>
      </p>
      {place && (
        <div className="place-info">
          {place.thumbnailUrl && <img src={place.thumbnailUrl} alt="" />}
          <div>
            <p className="place-title">Near here: {place.title}{place.distanceKm != null && ` (${Math.round(place.distanceKm)} km)`}</p>
            {place.summary && <p>{place.summary}</p>}
            <a href={place.url} target="_blank" rel="noreferrer">Read on Wikipedia</a>
          </div>
        </div>
      )}
    </div>
  );
}

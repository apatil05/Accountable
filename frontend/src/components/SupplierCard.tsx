// Cards for a real supplier or a company's US importer: who it is, where it is, and a Wikipedia article
// about the place. Owner: Person C. Used on the Map page and on company pages.
import type { Importer, NearbyPlace, Supplier } from "../api/types";
import { mapLinkFor } from "../lib/places";
import { possessive } from "../lib/suppliers";

const num = (n: number) => n.toLocaleString("en-US");

function Card({ label, title, address, at, place, nearby, onClose }: {
  label: string;
  title: string;
  address?: string | null;
  at?: { lat: number; lng: number } | null;
  place?: NearbyPlace | null;
  nearby?: NearbyPlace[];
  onClose: () => void;
}) {
  return (
    <div className="stop-card" role="dialog" aria-label={`${title} details`}>
      <button type="button" className="stop-close" onClick={onClose} aria-label="Close details">
        <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
      </button>
      <p className="stop-of">{label}</p>
      <div className="stop-detail">
        <h2>{title}</h2>
        {address && <p className="stop-stage">{address}</p>}
        {at && (
          <p className="stop-coords">
            Approximate location · <a href={mapLinkFor(at)} target="_blank" rel="noreferrer">Open in OpenStreetMap</a>
          </p>
        )}
        {place && (
          <div className="place-info">
            {place.thumbnail_url && <img src={place.thumbnail_url} alt="" />}
            <div>
              <p className="place-title">
                Where it is: {place.title}
                {place.distance_km != null && ` (${place.distance_km} km away)`}
              </p>
              {place.summary && <p className="place-summary">{place.summary}</p>}
              <a href={place.url} target="_blank" rel="noreferrer">Read more</a>
            </div>
          </div>
        )}
        {nearby && nearby.length > 0 && (
          <div className="nearby">
            <p className="nearby-title">Nearby</p>
            <ul>
              {nearby.map((n) => (
                <li key={n.title}>
                  <a href={n.url} target="_blank" rel="noreferrer" className="nearby-link">
                    {n.thumbnail_url ? <img src={n.thumbnail_url} alt="" loading="lazy" /> : <span className="nearby-blank" aria-hidden="true" />}
                    <span className="nearby-text">
                      <span className="nearby-name">{n.title}</span>
                      <span className="nearby-meta">
                        {n.summary && <span>{n.summary}</span>}
                        {n.distance_km != null && <span className="num">{n.distance_km} km</span>}
                      </span>
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default function SupplierCard({ supplier, company, onClose }: { supplier: Supplier; company?: string; onClose: () => void }) {
  const label = [
    company ? `Supplier to ${company}` : "Supplier",
    supplier.country,
    supplier.shipments != null ? `${num(supplier.shipments)} shipments` : null,
  ].filter(Boolean).join(" · ");
  return <Card label={label} title={supplier.name} address={supplier.address} at={supplier} place={supplier.place} nearby={supplier.nearby} onClose={onClose} />;
}

/** The company's own importing office in US records, where its suppliers' shipments go. */
export function ImporterCard({ importer, company, onClose }: { importer: Importer; company?: string; onClose: () => void }) {
  const at = importer.lat != null && importer.lng != null ? { lat: importer.lat, lng: importer.lng } : null;
  return (
    <Card
      label={company ? `${possessive(company)} US importer · where suppliers ship to` : "US importer"}
      title={importer.name}
      address={importer.address}
      at={at}
      place={importer.place}
      nearby={importer.nearby}
      onClose={onClose}
    />
  );
}

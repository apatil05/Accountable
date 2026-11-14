// "Learn about this place": extension point for zooming into a supply-chain stop and reading about
// a nearby town or village. Owner: Person C. Not wired to any service yet: it returns null, so the
// UI shows nothing extra and the demo stays fully offline.
//
// To turn it on later (no UI changes needed; StopDetail already renders whatever this returns):
//   1. Nearby article for a coordinate (Wikipedia GeoSearch, CORS-enabled with origin=*):
//      https://en.wikipedia.org/w/api.php?action=query&list=geosearch&gscoord={lat}|{lng}
//        &gsradius=10000&gslimit=1&format=json&origin=*
//   2. Its summary + thumbnail (Wikipedia REST):
//      https://en.wikipedia.org/api/rest_v1/page/summary/{title}
//   Better: do both server-side (e.g. a GET /api/places/nearby?lat=&lng= endpoint) and cache the result
//   with the supply chain, so the judged demo never depends on a live third-party call.

export interface PlaceInfo {
  title: string; // e.g. "La Plata, Huila"
  url: string; // the article to open
  summary?: string; // one or two sentences
  thumbnailUrl?: string;
  distanceKm?: number; // from the stop's coordinate
  source: "wikipedia";
}

export interface PlaceQuery {
  lat: number;
  lng: number;
  place: string; // the stop's own label, e.g. "Huila, Colombia"
}

export async function findNearbyPlace(_q: PlaceQuery): Promise<PlaceInfo | null> {
  return null;
}

/** A plain, always-available link for any coordinate (OpenStreetMap), independent of the feature above. */
export const mapLinkFor = ({ lat, lng }: { lat: number; lng: number }) =>
  `https://www.openstreetmap.org/?mlat=${lat.toFixed(4)}&mlon=${lng.toFixed(4)}#map=9/${lat.toFixed(4)}/${lng.toFixed(4)}`;

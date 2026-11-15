// Map links for any coordinate. Owner: Person C.
// (Place articles now come from the backend with each supplier: see SupplierReport / NearbyPlace.)

/** A plain, always-available link for any coordinate (OpenStreetMap). */
export const mapLinkFor = ({ lat, lng }: { lat: number; lng: number }) =>
  `https://www.openstreetmap.org/?mlat=${lat.toFixed(4)}&mlon=${lng.toFixed(4)}#map=9/${lat.toFixed(4)}/${lng.toFixed(4)}`;

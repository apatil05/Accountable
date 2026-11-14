"""Real suppliers behind a company, and something to read about where they are. Owner: Person C.

Pipeline (adapted from Person D's importyeti_simple_scraper.py), all real data, no mock fallback:
  ImportYeti /company/search  -> the brand's importer record (picked via data/importyeti_companies.json)
                                 + its top suppliers + the importer's US address (geocoded + Wikipedia too)
  ImportYeti /supplier/search -> each supplier's listed address
  ArcGIS geocoder             -> coordinates and country, from each supplier's listed ADDRESS only (never its
                                 name or the record's country label); kept only if that country is in the address
  Wikipedia GeoSearch         -> an article about the town/district/city each supplier is in

Only those two ImportYeti search endpoints are ever called (they are free); see FREE_PATHS.
Results are stored in the shared MongoDB (`supplier_reports`, `places`), so each company is looked up once
for the whole team and for the hosted app. The key lives in backend/.env as IMPORTYETI_API_KEY.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache

import httpx
from pymongo.database import Database

from .. import config
from ..models import Importer, NearbyPlace, Supplier, SupplierReport

log = logging.getLogger(__name__)

IY_BASE = "https://data.importyeti.com/v1.0"
ARCGIS = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
ARCGIS_REVERSE = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
# Wikimedia asks for an identifying User-Agent with a way to reach the project.
UA = {"User-Agent": "Accountable/0.1 (hackathon project; https://github.com/apatil05/Accountable)"}
JUNK_SUPPLIERS = {"missing in source document", "not available", "n/a", "unknown", "to order"}

# Only the two ImportYeti search endpoints from the team's script are allowed. They are free; anything else
# (detail or shipment endpoints) may spend the team's limited credits, so it is blocked here on purpose.
FREE_PATHS = {"/company/search", "/supplier/search"}


class PaidEndpointBlocked(RuntimeError):
    pass


class SuppliersNotConfigured(RuntimeError):
    pass


class SuppliersUnavailable(RuntimeError):
    pass


def _key() -> str:
    return os.getenv("IMPORTYETI_API_KEY", "").strip()


@lru_cache(maxsize=1)
def _company_map() -> dict:
    data = json.loads((config.DATA_DIR / "importyeti_companies.json").read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


# ---------- HTTP helpers ----------
def _iy(client: httpx.Client, path: str, **params) -> list[dict]:
    if path not in FREE_PATHS:
        raise PaidEndpointBlocked(f"ImportYeti {path} is not one of the free endpoints {sorted(FREE_PATHS)}")
    r = client.get(f"{IY_BASE}{path}", params=params, headers={"IYApiKey": _key(), "Accept": "application/json"})
    body = r.json()
    if r.status_code != 200 or body.get("statusCode", 200) != 200:
        raise SuppliersUnavailable(f"ImportYeti {path} failed ({r.status_code})")
    if body.get("requestCost"):
        log.warning("ImportYeti %s reported a cost of %s credits", path, body.get("requestCost"))
    return body.get("data") or []


def _compact(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalpha())


def _geocode(client: httpx.Client, address: str, country: str | None = None) -> tuple[float, float] | None:
    """Geocode an importer address (US records), optionally limited to one country."""
    params = {"SingleLine": address, "f": "json", "maxLocations": 1}
    if country:
        params["sourceCountry"] = country
    r = client.get(ARCGIS, params=params)
    cands = r.json().get("candidates") or []
    if not cands or cands[0].get("score", 0) < 60:
        return None
    loc = cands[0]["location"]
    return float(loc["y"]), float(loc["x"])


@lru_cache(maxsize=1)
def _country_names() -> dict[str, list[str]]:
    data = json.loads((config.DATA_DIR / "country_names.json").read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _locate_address(client: httpx.Client, address: str) -> tuple[float, float, str, str] | None:
    """Place a supplier using ONLY its listed address (never its name or the record's country label).
    The match is kept only if the country or region it lands in is actually written in the address, so a
    vague address (e.g. "Junction City Towersuite") is dropped instead of being pinned to a look-alike place."""
    r = client.get(ARCGIS, params={
        "SingleLine": address, "f": "json", "maxLocations": 1, "outFields": "Country,CntryName,Region", "langCode": "EN",
    })
    cands = r.json().get("candidates") or []
    if not cands or cands[0].get("score", 0) < 60:
        return None
    at = cands[0].get("attributes") or {}
    iso3 = at.get("Country") or ""
    english = _country_names().get(iso3, [])
    # The geocoder names countries in their own language ("Deutschland", "Việt Nam"); compare English
    # spellings too, plus the region (state/province), against the address text.
    candidates = [*english, at.get("CntryName") or "", at.get("Region") or ""]
    text = _compact(address)
    if not any(len(n) >= 4 and n in text for n in map(_compact, candidates)):
        return None
    loc = cands[0]["location"]
    return float(loc["y"]), float(loc["x"]), (english[0] if english else at.get("CntryName") or ""), iso3


# ---------- Wikipedia ----------
NOT_PLACES = ("school", "university", "college", "station", "airport", "stadium", "church", "hospital", "company", "building")
PLACE_WORDS = ("city", "town", "village", "municipality", "district", "neighbourhood", "neighborhood", "borough",
               "capital", "commune", "county", "province", "department", "region", "locality", "suburb", "port")


def _summary(client: httpx.Client, title: str, dist_km: float | None, must_be_place: bool = False) -> NearbyPlace | None:
    if title.startswith(("List of", "Timeline of", "History of")):
        return None
    r = client.get(WIKI_SUMMARY + title.replace(" ", "_"), headers=UA)
    if r.status_code != 200:
        return None
    s = r.json()
    if s.get("type") != "standard":  # skip disambiguation and missing pages
        return None
    desc = (s.get("description") or "").lower()
    if must_be_place and (not any(w in desc for w in PLACE_WORDS) or any(w in desc for w in NOT_PLACES)):
        return None  # GeoData tags some buildings/stations/school districts as "city"; trust only real places
    return NearbyPlace(
        title=s.get("title") or title,
        url=(s.get("content_urls") or {}).get("desktop", {}).get("page")
        or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        summary=s.get("extract"),
        thumbnail_url=(s.get("thumbnail") or {}).get("source"),
        distance_km=dist_km,
    )


def _nearby_place(client: httpx.Client, lat: float, lng: float) -> NearbyPlace | None:
    """Prefer an article about the town or district itself; fall back to the containing city's article."""
    r = client.get(
        WIKI_API,
        headers=UA,
        params={
            "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lng}", "gsradius": 10000,
            "gslimit": 50, "gsprop": "type", "format": "json",
        },
    )
    hits = r.json().get("query", {}).get("geosearch", [])
    towns = [h for h in hits if (h.get("type") or "") in ("city", "adm3rd", "adm2nd")]
    for h in sorted(towns, key=lambda h: h["dist"])[:4]:
        place = _summary(client, h["title"], round(h["dist"] / 1000, 1), must_be_place=True)
        if place:
            return place
    # No town article tagged nearby (typical inside big cities): ask which city these coordinates are in.
    rev = client.get(ARCGIS_REVERSE, params={"location": f"{lng},{lat}", "f": "json"}).json().get("address") or {}
    city, region, country = rev.get("City"), rev.get("Region"), rev.get("CntryName")
    for title in [f"{city}, {region}" if city and region else None, city, region, country]:
        if title:
            place = _summary(client, title, None)
            if place:
                return place
    return None


def nearby_place(db: Database, lat: float, lng: float) -> NearbyPlace | None:
    """A Wikipedia place near any coordinate. Stored in the shared `places` collection."""
    key = f"{lat:.3f},{lng:.3f}"
    doc = db.places.find_one({"_id": key})
    if doc is not None:
        return NearbyPlace(**doc["place"]) if doc.get("place") else None
    with httpx.Client(timeout=12) as client:
        place = _nearby_place(client, lat, lng)
    db.places.replace_one({"_id": key}, {"_id": key, "place": place.model_dump() if place else None}, upsert=True)
    return place


# ---------- report ----------
def _one_supplier(name: str) -> Supplier | None:
    try:
        with httpx.Client(timeout=15) as client:
            hits = _iy(client, "/supplier/search", page_size=3, name=name)
            if not hits or not hits[0].get("address"):
                return None
            s = hits[0]
            found = _locate_address(client, s["address"])
            if not found:
                return None
            lat, lng, country, iso3 = found
            return Supplier(
                name=s.get("title") or name,
                address=s.get("address"),
                country=country or None,
                country_code=iso3 or None,
                shipments=s.get("totalShipments"),
                lat=lat,
                lng=lng,
                place=_nearby_place(client, lat, lng),
            )
    except PaidEndpointBlocked:
        raise
    except (httpx.HTTPError, SuppliersUnavailable, ValueError, KeyError) as e:
        log.warning("supplier lookup failed for %s: %s", name, e)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build(merchant_id: str) -> SupplierReport:
    pick = _company_map().get(merchant_id)
    if not pick:  # no trusted import record for this brand: say so, never guess
        return SupplierReport(merchant_id=merchant_id, suppliers=[], fetched_at=_now())

    with httpx.Client(timeout=15) as client:
        hits = _iy(client, "/company/search", page_size=10, offset=0, name=pick["query"])
        company = next((c for c in hits if (c.get("title") or "").strip().lower() == pick["title"].lower()), None)
        if not company:
            return SupplierReport(merchant_id=merchant_id, suppliers=[], fetched_at=_now())
        # The company's own address from the company search record (parsed["data"][...]["address"] in the
        # team's script) -> coordinates -> a Wikipedia article about that place, like the suppliers get.
        importer = Importer(name=company["title"], address=company.get("address"))
        if importer.address:
            ll = _geocode(client, importer.address, "US")  # US import records: the importer is always in the US
            if ll:
                importer.lat, importer.lng = ll
                importer.place = _nearby_place(client, *ll)

    names = [n for n in company.get("topSuppliers") or [] if n and n.strip().lower() not in JUNK_SUPPLIERS]
    with ThreadPoolExecutor(max_workers=max(1, len(names))) as pool:
        resolved = [s for s in pool.map(_one_supplier, dict.fromkeys(names)) if s]
    # Different name spellings can resolve to the same supplier record; keep each once.
    found = list({(s.name.lower(), round(s.lat, 2), round(s.lng, 2)): s for s in resolved}.values())

    return SupplierReport(
        merchant_id=merchant_id, company_name=company["title"], importer=importer, suppliers=found, fetched_at=_now()
    )


def get_report(db: Database, merchant_id: str) -> SupplierReport:
    doc = db.supplier_reports.find_one({"_id": merchant_id})
    if doc:
        doc.pop("_id", None)
        return SupplierReport(**doc)
    if not _key():
        raise SuppliersNotConfigured("IMPORTYETI_API_KEY is not set")
    report = _build(merchant_id)
    db.supplier_reports.replace_one({"_id": merchant_id}, {"_id": merchant_id, **report.model_dump()}, upsert=True)
    return report


def get_reports(db: Database, merchant_ids: list[str]) -> list[SupplierReport]:
    """Reports for many brands; stored ones return instantly, missing ones are looked up in parallel."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda mid: get_report(db, mid), merchant_ids))

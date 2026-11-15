"""Real suppliers behind a company, and something to read about where they are. Owner: Person C.

Pipeline (adapted from Person D's importyeti_simple_scraper.py), all real data, no mock fallback:
  ImportYeti /company/search  -> the brand's importer record: the hand-picked record in
                                 data/importyeti_companies.json (226 companies) when it turns up; otherwise the
                                 merchant's own name is searched and the result with the most shipments is used.
                                 Gives its top suppliers + its US address (geocoded + Wikipedia too).
  ImportYeti /supplier/search -> each supplier's listed address
  ArcGIS geocoder             -> coordinates and country, from each supplier's listed ADDRESS only (never its
                                 name or the record's country label); kept only if that country is in the address
  Wikipedia GeoSearch         -> an article about the town/district/city each supplier is in, plus other articles
                                 near its exact geocoded point (landmarks, neighbourhoods, sights)

Only those two ImportYeti search endpoints are ever called (they are free); see FREE_PATHS.
Results are stored in the shared MongoDB (`supplier_reports`, `places`), so each company is looked up once
for the whole team and for the hosted app. The key lives in backend/.env as IMPORTYETI_API_KEY.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache

import httpx
from pymongo.database import Database

from .. import config
from ..db import repos
from ..models import Importer, NearbyPlace, Supplier, SupplierReport

log = logging.getLogger(__name__)

IY_BASE = "https://data.importyeti.com/v1.0"
ARCGIS = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
ARCGIS_REVERSE = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/reverseGeocode"
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
# Wikimedia asks for an identifying User-Agent with a way to reach the project.
UA = {"User-Agent": "Accountable/0.1 (hackathon project; https://github.com/apatil05/Accountable)"}
# Placeholder names ImportYeti uses when a shipment's supplier was not recorded ("Missing in source document").
# They name no supplier, so they are left out of the list and never searched.
PLACEHOLDER_SUPPLIERS = {"missing in source document", "not available", "n/a", "unknown", "to order"}
# Hard cap on ImportYeti requests per company (company search included), so lookups never trip the search limit.
MAX_IY_REQUESTS = 5


class _Budget:
    """Counts ImportYeti requests for one company; refuses (without calling) once the cap is reached."""

    def __init__(self, limit: int = MAX_IY_REQUESTS):
        self.left = limit
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.left <= 0:
                return False
            self.left -= 1
            return True

# Only the two ImportYeti search endpoints from the team's script are allowed. They are free; anything else
# (detail or shipment endpoints) may spend the team's limited credits, so it is blocked here on purpose.
FREE_PATHS = {"/company/search", "/supplier/search"}


@lru_cache(maxsize=1)
def _curated() -> dict[str, dict]:
    """Hand-picked import records, keyed by the merchant name with case, spaces and punctuation removed."""
    data = json.loads((config.DATA_DIR / "importyeti_companies.json").read_text())
    return {_norm(name): pick for name, pick in data["companies"].items()}


def _norm(name: str) -> str:
    n = "".join(ch for ch in name.lower() if ch.isalnum())
    return n[3:] if n.startswith("the") and len(n) > 6 else n  # "The Home Depot" == "Home Depot"


class PaidEndpointBlocked(RuntimeError):
    pass


class SuppliersNotConfigured(RuntimeError):
    pass


class SuppliersUnavailable(RuntimeError):
    pass


def _key() -> str:
    return os.getenv("IMPORTYETI_API_KEY", "").strip()


# ---------- HTTP helpers ----------
def _iy(client: httpx.Client, path: str, **params) -> list[dict]:
    if path not in FREE_PATHS:
        raise PaidEndpointBlocked(f"ImportYeti {path} is not one of the free endpoints {sorted(FREE_PATHS)}")
    r = client.get(f"{IY_BASE}{path}", params=params, headers={"IYApiKey": _key(), "Accept": "application/json"})
    try:
        body = r.json()
    except ValueError:  # an HTML error page instead of JSON
        raise SuppliersUnavailable(f"ImportYeti {path} returned an unreadable response ({r.status_code})") from None
    if r.status_code == 403 and "limit" in str(body.get("message", "")).lower():
        raise SuppliersUnavailable("ImportYeti's search limit has been reached for now; try again later")
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


@lru_cache(maxsize=1)
def _country_hints() -> dict:
    return json.loads((config.DATA_DIR / "country_hints.json").read_text())


def _words(text: str) -> str:
    """' hong kong 999077 h ' style: lowercase, accents dropped, letters and digits only, padded for whole-word matching."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " " + " ".join(re.findall(r"[a-z0-9]+", plain)) + " "


def _has(text: str, phrase: str) -> bool:
    return _words(phrase) in text


# Region names that are also everyday address words ("Laurels Industrial Centre" is not Cameroon's Centre region).
GENERIC_REGIONS = {"centre", "center", "central", "north", "south", "east", "west", "capital", "coast", "littoral", "island", "islands"}


def _named_countries(address: str) -> tuple[list[str], dict[str, str]]:
    """Countries an address names, in the order found: by country name (whole words), by a trailing ISO-2 code
    ('... Kyoto Jp'), or by a big trade city ('Jebel Ali' -> UAE). Also returns the city that named each country."""
    text, hints = _words(address), _country_hints()
    found: list[str] = []
    cities: dict[str, str] = {}
    for iso3, names in _country_names().items():
        if any(_has(text, n) for n in names):
            found.append(iso3)
    last = text.split()[-1] if text.strip() else ""
    for iso3, code in hints["iso2"].items():
        if last == code.lower() and iso3 not in found:
            found.append(iso3)
    for iso3, names in hints["cities"].items():
        for city in names:
            if _has(text, city):
                cities.setdefault(iso3, city)
                if iso3 not in found:
                    found.append(iso3)
    return found, cities


def _km(a: dict, b: dict) -> float:
    """Great-circle distance between two ArcGIS locations ({"x": lng, "y": lat})."""
    la1, lo1, la2, lo2 = map(math.radians, (a["y"], a["x"], b["y"], b["x"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _geocode_one(client: httpx.Client, text: str, country: str | None = None) -> dict | None:
    params = {"SingleLine": text, "f": "json", "maxLocations": 1, "outFields": "Country,CntryName,Region", "langCode": "EN"}
    if country:
        params["sourceCountry"] = country
    cands = client.get(ARCGIS, params=params).json().get("candidates") or []
    return cands[0] if cands and cands[0].get("score", 0) >= 60 else None


def _best_guess(client: httpx.Client, address: str) -> tuple[float, float, str, str] | None:
    """When the address can't be confirmed: the geocoder's best guess for it (any score), else the country the
    address names, else the last few words of the address ("... Hunan 417700"). Always marked approximate."""
    def raw(text: str, country: str | None = None) -> dict | None:
        params = {"SingleLine": text, "f": "json", "maxLocations": 1, "outFields": "Country,CntryName,Region", "langCode": "EN"}
        if country:
            params["sourceCountry"] = country
        cands = client.get(ARCGIS, params=params).json().get("candidates") or []
        return cands[0] if cands else None

    named, _ = _named_countries(address)
    words = address.split()
    tries = [(address, None)] + [(_country_names()[iso3][0], iso3) for iso3 in named[:1] if iso3 in _country_names()] \
        + [(" ".join(words[-k:]), None) for k in (4, 3, 2) if len(words) > k]
    for text, country in tries:
        c = raw(text, country)
        if c:
            iso3 = (c.get("attributes") or {}).get("Country") or ""
            english = _country_names().get(iso3, [])
            return float(c["location"]["y"]), float(c["location"]["x"]), (english[0] if english else (c.get("attributes") or {}).get("CntryName") or ""), iso3
    return None


def _locate_address(client: httpx.Client, address: str) -> tuple[float, float, str, str] | None:
    """Place a supplier using ONLY its listed address (never its name or the record's country label).

    If the address names a country (by name, a trailing ISO-2 code or a big trade city), the pin must land in
    that country: a match elsewhere is retried inside it, then at the named city. If it names no country, the
    match is kept only when the country or a specific region it lands in is written in the address. Anything
    else gets no pin, so a vague address is never pinned to a look-alike place."""
    named, cities = _named_countries(address)

    def result(c: dict) -> tuple[float, float, str, str]:
        iso3 = (c.get("attributes") or {}).get("Country") or ""
        english = _country_names().get(iso3, [])
        return float(c["location"]["y"]), float(c["location"]["x"]), (english[0] if english else (c.get("attributes") or {}).get("CntryName") or ""), iso3

    def near_named_city(c: dict) -> dict:
        """Generic phrases ("Organized Industrial Zone") can match the wrong town of the right country. When the
        address names a trade city and the match is more than 40 km from it, the city itself is the better pin."""
        iso3 = (c.get("attributes") or {}).get("Country")
        if iso3 not in cities:
            return c
        city = _geocode_one(client, cities[iso3], iso3)
        if city and _km(c["location"], city["location"]) > 40:
            return city
        return c

    first = _geocode_one(client, address)
    if named:
        if first and (first.get("attributes") or {}).get("Country") in named:
            return result(near_named_city(first))
        for iso3 in named[:2]:  # retry inside the country the address names (wrong-country or no match the first time)
            c = _geocode_one(client, address, iso3)
            if c and (c.get("attributes") or {}).get("Country") == iso3:
                return result(near_named_city(c))
            if iso3 in cities:  # the full address would not place; the named trade city will (approximate)
                c = _geocode_one(client, cities[iso3], iso3)
                if c and (c.get("attributes") or {}).get("Country") == iso3:
                    return result(c)
        return None
    if not first:
        return None
    at = first.get("attributes") or {}
    text = _words(address)
    region = (at.get("Region") or "").strip()
    region_ok = bool(region) and len(region) >= 5 and region.lower() not in GENERIC_REGIONS and _has(text, region)
    if region_ok or (at.get("CntryName") and _has(text, at["CntryName"])):
        return result(first)
    return None


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


def _nearby_articles(client: httpx.Client, lat: float, lng: float, skip: str | None = None, limit: int = 6) -> list[NearbyPlace]:
    """Wikipedia articles around the exact geocoded point (not just the town): landmarks, neighbourhoods, sights.
    One GeoSearch, then one batched lookup for each article's short description, intro and thumbnail."""
    r = client.get(WIKI_API, headers=UA, params={
        "action": "query", "list": "geosearch", "gscoord": f"{lat}|{lng}", "gsradius": 10000, "gslimit": 30, "format": "json",
    })
    hits = [h for h in r.json().get("query", {}).get("geosearch", [])
            if h["title"] != skip and not h["title"].startswith(("List of", "Timeline of", "History of"))][: limit * 2]
    if not hits:
        return []
    info = client.get(WIKI_API, headers=UA, params={
        "action": "query", "format": "json", "formatversion": 2, "redirects": 1,
        "prop": "extracts|pageimages|description|pageprops", "exintro": 1, "explaintext": 1, "exsentences": 2,
        "exlimit": "max", "piprop": "thumbnail", "pithumbsize": 160, "ppprop": "disambiguation",
        "titles": "|".join(h["title"] for h in hits),
    }).json().get("query", {}).get("pages", [])
    pages = {p.get("title"): p for p in info}
    out: list[NearbyPlace] = []
    for h in sorted(hits, key=lambda h: h["dist"]):
        p = pages.get(h["title"]) or {}
        if p.get("missing") or "disambiguation" in (p.get("pageprops") or {}):
            continue
        out.append(NearbyPlace(
            title=h["title"],
            url=f"https://en.wikipedia.org/wiki/{h['title'].replace(' ', '_')}",
            summary=p.get("description") or p.get("extract") or None,
            thumbnail_url=(p.get("thumbnail") or {}).get("source"),
            distance_km=round(h["dist"] / 1000, 1),
        ))
        if len(out) >= limit:
            break
    return out


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
def _one_supplier(name: str, search: bool = True) -> Supplier:
    """A supplier exactly as named in the company's ImportYeti record. Its address comes from one supplier search
    (the hit with the same name, else the first); it gets a pin only if that address can be placed. It is listed
    either way: nothing is dropped."""
    sup = Supplier(name=name)
    if not search:
        return sup
    try:
        with httpx.Client(timeout=15) as client:
            hits = _iy(client, "/supplier/search", page_size=3, name=name)
            hit = next((h for h in hits if _norm(h.get("title") or "") == _norm(name)), hits[0] if hits else None)
            if not hit:
                return sup
            sup.address = hit.get("address") or None
            sup.shipments = hit.get("totalShipments")
            found = _locate_address(client, sup.address) if sup.address else None
            if not found and sup.address:  # unconfirmed address: still pinned, at the geocoder's best guess
                found = _best_guess(client, sup.address)
                sup.approximate = bool(found)
            if not found:
                return sup
            sup.lat, sup.lng, country, iso3 = found
            sup.country, sup.country_code = country or None, iso3 or None
            sup.place = _nearby_place(client, sup.lat, sup.lng)
            sup.nearby = _nearby_articles(client, sup.lat, sup.lng, skip=sup.place.title if sup.place else None)
    except PaidEndpointBlocked:
        raise
    except (httpx.HTTPError, SuppliersUnavailable, ValueError, KeyError) as e:
        log.warning("supplier lookup failed for %s: %s", name, e)
    return sup


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick_company(hits: list[dict]) -> dict | None:
    """Of the company-search results for a merchant name, the record with the most shipments."""
    return max(hits, key=lambda c: c.get("totalShipments") or 0, default=None)


def _find_company(client: httpx.Client, merchant_name: str, budget: _Budget | None = None) -> dict | None:
    """The hand-picked record if it still turns up; otherwise the most-shipped result for the merchant's name."""
    budget = budget or _Budget()
    pick = _curated().get(_norm(merchant_name))
    if pick and budget.take():
        hits = _iy(client, "/company/search", page_size=10, offset=0, name=pick["query"])
        want = _norm(pick["title"])
        found = next((c for c in hits if _norm(c.get("title") or "") == want), None)
        if found:
            return found
    if not budget.take():
        return None
    # Same call as the team's script: company/search?page_size=10&offset=0&name=<merchant name>.
    return _pick_company(_iy(client, "/company/search", page_size=10, offset=0, name=merchant_name))


def _build(merchant_id: str, merchant_name: str) -> SupplierReport:
    budget = _Budget()
    with httpx.Client(timeout=15) as client:
        company = _find_company(client, merchant_name, budget)
        if not company:
            return SupplierReport(merchant_id=merchant_id, suppliers=[], fetched_at=_now())
        # The company's own address from the company search record (parsed["data"][...]["address"] in the
        # team's script) -> coordinates -> a Wikipedia article about that place, like the suppliers get.
        importer = Importer(name=company["title"], address=company.get("address"))
        if importer.address:
            try:
                ll = _geocode(client, importer.address, "US")  # US import records: the importer is always in the US
                if ll:
                    importer.lat, importer.lng = ll
                    importer.place = _nearby_place(client, *ll)
                    importer.nearby = _nearby_articles(client, *ll, skip=importer.place.title if importer.place else None)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                # The importer's pin is a nice-to-have; a geocoder or Wikipedia hiccup must not lose the suppliers.
                log.warning("importer place lookup failed for %s: %s", merchant_id, e)

    # Every supplier in the record, in ImportYeti's order and under ImportYeti's name. The searches left in this
    # company's budget go to the first real names; the rest (and placeholders) are listed without a lookup.
    names = [n.strip() for n in company.get("topSuppliers") or [] if n and n.strip() and n.strip().lower() not in PLACEHOLDER_SUPPLIERS]
    plan = [(n, budget.take()) for n in names]
    with ThreadPoolExecutor(max_workers=max(1, len(plan))) as pool:
        found = list(pool.map(lambda p: _one_supplier(*p), plan))

    return SupplierReport(
        merchant_id=merchant_id, company_name=company["title"], importer=importer, suppliers=found, fetched_at=_now()
    )


def get_report(db: Database, merchant_id: str, refresh: bool = False) -> SupplierReport:
    doc = None if refresh else db.supplier_reports.find_one({"_id": merchant_id})
    if doc:
        doc.pop("_id", None)
        return SupplierReport(**doc)
    brand = repos.get_brand(db, merchant_id) or {}
    if not brand.get("name") or brand.get("is_junk"):  # not a real brand (e.g. a cash withdrawal): nothing to search
        return SupplierReport(merchant_id=merchant_id, suppliers=[], fetched_at=_now())
    if not _key():  # checked after the brand, so a non-brand never needs a key
        raise SuppliersNotConfigured("IMPORTYETI_API_KEY is not set")
    report = _build(merchant_id, brand["name"])
    db.supplier_reports.replace_one({"_id": merchant_id}, {"_id": merchant_id, **report.model_dump()}, upsert=True)
    return report


def get_reports(db: Database, merchant_ids: list[str], refresh: bool = False) -> list[SupplierReport]:
    """Reports for many brands; stored ones return instantly, missing ones are looked up in parallel.
    A brand whose lookup fails is left out (and logged) so one outage doesn't blank the whole globe."""
    def one(mid: str) -> SupplierReport | None:
        try:
            return get_report(db, mid, refresh)
        except (SuppliersNotConfigured, SuppliersUnavailable, httpx.HTTPError, ValueError) as e:
            log.warning("supplier report unavailable for %s: %s", mid, e)
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        return [r for r in pool.map(one, merchant_ids) if r is not None]


def lookup(db: Database, name: str) -> SupplierReport:
    """Any company typed in by the user (the Map's search bar): same pipeline, stored under its normalised name
    so repeat searches cost no ImportYeti calls."""
    key = f"lookup:{_norm(name)}"
    doc = db.supplier_lookups.find_one({"_id": key})
    if doc:
        doc.pop("_id", None)
        return SupplierReport(**doc)
    if not _key():
        raise SuppliersNotConfigured("IMPORTYETI_API_KEY is not set")
    report = _build(key, name.strip())
    db.supplier_lookups.replace_one({"_id": key}, {"_id": key, **report.model_dump()}, upsert=True)
    return report


def add_nearby(db: Database) -> int:
    """Give stored reports their nearby Wikipedia articles from the coordinates already saved (Wikipedia only;
    no ImportYeti calls). Returns how many reports changed."""
    changed = 0
    with httpx.Client(timeout=15) as client:
        for coll in (db.supplier_reports, db.supplier_lookups):
            for doc in coll.find({}):
                report = SupplierReport(**{k: v for k, v in doc.items() if k != "_id"})
                touched = False
                for s in report.suppliers:
                    if not s.nearby and s.lat is not None and s.lng is not None:
                        s.nearby = _nearby_articles(client, s.lat, s.lng, skip=s.place.title if s.place else None)
                        touched = True
                imp = report.importer
                if imp and imp.lat is not None and imp.lng is not None and not imp.nearby:
                    imp.nearby = _nearby_articles(client, imp.lat, imp.lng, skip=imp.place.title if imp.place else None)
                    touched = True
                if touched:
                    coll.replace_one({"_id": doc["_id"]}, {"_id": doc["_id"], **report.model_dump()})
                    changed += 1
    return changed

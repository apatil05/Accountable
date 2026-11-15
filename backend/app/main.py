from typing import Optional
import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pymongo.database import Database
from pymongo.errors import PyMongoError

from . import config, store
from .db import repos
from .db.client import get_db, ping
from .db.indexes import ensure_indexes
from .models import (
    Alternative,
    CompanyEmissions,
    EthicsReport,
    LaborSummary,
    NearbyPlace,
    Purchase,
    SpendingSummary,
    SupplierReport,
    SupplyChain,
    UserProfile,
)
from .services import aggregation, ethics, ingest, labor, nessie, suppliers, supply_chain

logger = logging.getLogger(__name__)

DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
SYNC_COOLDOWN_SECONDS = 30

router = APIRouter(prefix="/api")


class SyncGuard:
    """One sync at a time, plus a cooldown, so a request loop can't hammer Nessie or Atlas."""

    def __init__(self, cooldown_seconds: float):
        self._lock = threading.Lock()
        self._cooldown = cooldown_seconds
        self._last_finished = float("-inf")

    def try_acquire(self) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        if time.monotonic() - self._last_finished < self._cooldown:
            self._lock.release()
            return False
        return True

    def release(self) -> None:
        self._last_finished = time.monotonic()  # failures count too, so retries are rate-limited
        self._lock.release()


sync_guard = SyncGuard(SYNC_COOLDOWN_SECONDS)


def get_database() -> Database:
    return get_db()


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not config.ADMIN_TOKEN:
        raise HTTPException(403, "Sync over HTTP is disabled on this server; use `python -m scripts.sync`")
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), config.ADMIN_TOKEN.encode()):
        raise HTTPException(401, "Missing or invalid admin token")


def _customer_id(customer_id: str | None) -> str:
    """The configured customer, unless this server explicitly allows choosing another."""
    if customer_id is not None and not config.ALLOW_CUSTOMER_OVERRIDE:
        raise HTTPException(403, "Choosing a customer is disabled on this server")
    return nessie.validate_id(customer_id or nessie.default_customer_id(), "customer id")


def _brand_or_404(db: Database, brand_id: str) -> dict:
    brand = repos.get_brand(db, brand_id)
    if not brand:
        raise HTTPException(404, f"Unknown merchant {brand_id}")
    return brand


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.validate()
    db = get_db()
    try:
        ping(db)
    except PyMongoError as exc:
        raise RuntimeError(
            f"Cannot connect to MongoDB Atlas ({type(exc).__name__}). Check MONGODB_URI, "
            "the database user's password, and Atlas Network Access (IP allow list)."
        ) from None
    ensure_indexes(db)
    ingest.seed_brands(db)
    default_id = nessie.default_customer_id()
    if config.USE_MOCK_DATA and not repos.customer_exists(db, default_id):
        ingest.sync_customer(db, default_id)
    yield


@router.get("/health")
def health(db: Database = Depends(get_database)):
    connected, last_sync = True, None
    try:
        ping(db)
        run = repos.last_sync_run(db)
        last_sync = run["finished_at"].isoformat() if run else None
    except PyMongoError:
        connected = False
    return {
        "ok": connected,
        "mock_data": config.USE_MOCK_DATA,
        "gemini_configured": bool(config.GEMINI_API_KEY),
        "db": {"connected": connected},
        "last_sync": last_sync,
    }


@router.get("/customers")
def customers():
    if not config.ALLOW_CUSTOMER_OVERRIDE:
        raise HTTPException(403, "Listing customers is disabled on this server")
    return nessie.list_customers()


@router.post("/sync", dependencies=[Depends(require_admin)])
def sync(customer_id: str | None = None, db: Database = Depends(get_database)):
    """Re-pull a customer from Nessie into Atlas. Admin only; safe to run repeatedly."""
    cid = nessie.validate_id(customer_id or nessie.default_customer_id(), "customer id")
    if not sync_guard.try_acquire():
        raise HTTPException(429, f"A sync ran recently; try again in {SYNC_COOLDOWN_SECONDS} seconds")
    try:
        return ingest.sync_customer(db, cid)
    finally:
        sync_guard.release()


@router.get("/spending/summary", response_model=SpendingSummary)
def spending_summary(customer_id: str | None = None, db: Database = Depends(get_database)):
    cid = _customer_id(customer_id)
    return aggregation.spending_summary(db, cid, store.load_profile(db, _customer_id(None)))


@router.get("/spending/transactions", response_model=list[Purchase])
def transactions(customer_id: str | None = None, db: Database = Depends(get_database)):
    return [
        Purchase(
            id=p["_id"],
            merchant_id=p["brand_id"],
            amount=p["amount_cents"] / 100,
            purchase_date=p["purchase_date"].date().isoformat(),
            description=p.get("description", ""),
        )
        for p in repos.list_purchases(db, _customer_id(customer_id))
    ]


@router.get("/profile", response_model=UserProfile)
def get_profile(db: Database = Depends(get_database)):
    return store.load_profile(db, _customer_id(None))


@router.put("/profile", response_model=UserProfile)
def put_profile(profile: UserProfile, db: Database = Depends(get_database)):
    return store.save_profile(db, _customer_id(None), profile)


@router.get("/merchants/{merchant_id}/ethics", response_model=EthicsReport)
def merchant_ethics(merchant_id: str, db: Database = Depends(get_database)):
    brand = _brand_or_404(db, merchant_id)
    if brand.get("is_junk"):
        raise HTTPException(404, "Test or placeholder records are not researched")
    report = ethics.get_report(db, brand["_id"], brand["name"], store.load_profile(db, _customer_id(None)))
    if not report:
        raise HTTPException(404, "No ethics data yet for this merchant")
    return report


@router.get("/merchants/{merchant_id}/supply-chain", response_model=SupplyChain)
def merchant_supply_chain(merchant_id: str, db: Database = Depends(get_database)):
    _brand_or_404(db, merchant_id)
    chain = supply_chain.get_supply_chain(db, merchant_id)
    if not chain:
        raise HTTPException(404, "No supply chain data yet for this merchant")
    return chain


@router.get("/merchants/{merchant_id}/alternatives", response_model=list[Alternative])
def merchant_alternatives(merchant_id: str, db: Database = Depends(get_database)):
    brand = _brand_or_404(db, merchant_id)
    prefer_local = store.load_profile(db, _customer_id(None)).prefer_local
    return supply_chain.get_alternatives(brand.get("categories", []), prefer_local)


@router.get("/merchants/{merchant_id}/emissions", response_model=list[CompanyEmissions])
def merchant_emissions(merchant_id: str, db: Database = Depends(get_database)):
    """Company-level emissions facts (reported or independently estimated), newest year first."""
    _brand_or_404(db, merchant_id)
    facts = []
    for doc in repos.get_company_emissions(db, merchant_id):
        try:
            facts.append(CompanyEmissions(**doc))
        except ValidationError:
            # Only possible if someone edits the database by hand; skip it rather than fail the page.
            logger.error("invalid company_emissions document %s", doc.get("_id"))
    return facts


@router.get("/merchants/{merchant_id}/labor", response_model=LaborSummary)
def merchant_labor(merchant_id: str, db: Database = Depends(get_database)):
    """Totals of concluded US Department of Labor wage and hour cases for this brand."""
    _brand_or_404(db, merchant_id)
    return labor.summarize(db, merchant_id)


@router.get("/merchants/{merchant_id}/suppliers", response_model=SupplierReport)
def merchant_suppliers(merchant_id: str, db: Database = Depends(get_database)):
    """Top real suppliers from US import records (ImportYeti), geocoded, each with a nearby Wikipedia place."""
    _brand_or_404(db, merchant_id)
    try:
        return suppliers.get_report(db, merchant_id)
    except suppliers.SuppliersNotConfigured:
        raise HTTPException(503, "Supplier data isn't set up on this server (IMPORTYETI_API_KEY).")
    except suppliers.SuppliersUnavailable as e:
        raise HTTPException(502, f"Supplier lookup failed: {e}")


@router.get("/suppliers/lookup", response_model=SupplierReport)
def lookup_suppliers(name: str = Query(..., min_length=2, max_length=80), db: Database = Depends(get_database)):
    """Look up any company by name on ImportYeti (for the Map's search bar). Not tied to the user's purchases."""
    try:
        return suppliers.lookup(db, name)
    except suppliers.SuppliersNotConfigured:
        raise HTTPException(503, "Supplier data isn't set up on this server (IMPORTYETI_API_KEY).")
    except suppliers.SuppliersUnavailable as e:
        raise HTTPException(502, f"Supplier lookup failed: {e}")


@router.get("/suppliers", response_model=list[SupplierReport])
def all_suppliers(customer_id: str | None = None, db: Database = Depends(get_database)):
    """Real supplier reports for every company the user buys from (for the globes)."""
    ids = sorted(repos.distinct_brand_ids(db, _customer_id(customer_id)))
    try:
        return suppliers.get_reports(db, ids)
    except suppliers.SuppliersNotConfigured:
        raise HTTPException(503, "Supplier data isn't set up on this server (IMPORTYETI_API_KEY).")
    except suppliers.SuppliersUnavailable as e:
        raise HTTPException(502, f"Supplier lookup failed: {e}")


@router.get("/places/nearby", response_model=Optional[NearbyPlace])
def places_nearby(lat: float, lng: float, db: Database = Depends(get_database)):
    """A Wikipedia place near a coordinate. Null when nothing is close or Wikipedia is unreachable."""
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(422, "lat/lng out of range")
    try:
        return suppliers.nearby_place(db, lat, lng)
    except Exception:  # Wikipedia down or offline: the UI simply shows no place
        logger.warning("nearby place lookup failed", exc_info=True)
        return None


@router.get("/supply-chains", response_model=list[SupplyChain])
def all_supply_chains(customer_id: str | None = None, db: Database = Depends(get_database)):
    """Everything the user buys, for the big 'where your money goes' map."""
    ids = repos.distinct_brand_ids(db, _customer_id(customer_id))
    return supply_chain.get_supply_chains(db, ids)


def create_app() -> FastAPI:
    production = config.is_production()
    app = FastAPI(
        title="Accountable API",
        version="0.2.0",
        lifespan=lifespan,
        # The interactive API explorer is for local development only.
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )
    dev_origins = () if production else DEV_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted({*config.FRONTEND_ORIGINS, *dev_origins}),
        allow_methods=["GET", "PUT", "POST"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.exception_handler(nessie.NessieError)
    async def nessie_error(_: Request, exc: nessie.NessieError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(nessie.InvalidIdError)
    async def invalid_id(_: Request, exc: nessie.InvalidIdError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PyMongoError)
    async def database_error(_: Request, exc: PyMongoError):
        # Log the type only: driver messages can include the cluster host and topology.
        logger.error("database error: %s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "The database is temporarily unavailable"})

    return app


app = create_app()

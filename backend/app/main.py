import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.database import Database
from pymongo.errors import PyMongoError

from . import config, store
from .db import repos
from .db.client import get_db, ping
from .db.indexes import ensure_indexes
from .models import (
    Alternative,
    EthicsReport,
    Purchase,
    SpendingSummary,
    SupplyChain,
    UserProfile,
)
from .services import aggregation, ethics, ingest, nessie, supply_chain

logger = logging.getLogger(__name__)

DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")

router = APIRouter(prefix="/api")


def get_database() -> Database:
    return get_db()


def _customer_id(customer_id: str | None) -> str:
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
        "db": {"connected": connected, "name": config.MONGODB_DB},
        "last_sync": last_sync,
    }


@router.get("/customers")
def customers():
    return nessie.list_customers()


@router.post("/sync")
def sync(customer_id: str | None = None, db: Database = Depends(get_database)):
    """Re-pull a customer from Nessie into Atlas. Safe to run repeatedly."""
    return ingest.sync_customer(db, _customer_id(customer_id))


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
    saved = store.save_profile(db, _customer_id(None), profile)
    ethics._cache.clear()  # personalized scores depend on weights
    return saved


@router.get("/merchants/{merchant_id}/ethics", response_model=EthicsReport)
def merchant_ethics(merchant_id: str, db: Database = Depends(get_database)):
    brand = _brand_or_404(db, merchant_id)
    if brand.get("is_junk"):
        raise HTTPException(404, "Test or placeholder records are not researched")
    report = ethics.get_report(brand["_id"], brand["name"], store.load_profile(db, _customer_id(None)))
    if not report:
        raise HTTPException(404, "No ethics data yet for this merchant")
    return report


@router.get("/merchants/{merchant_id}/supply-chain", response_model=SupplyChain)
def merchant_supply_chain(merchant_id: str, db: Database = Depends(get_database)):
    _brand_or_404(db, merchant_id)
    chain = supply_chain.get_supply_chain(merchant_id)
    if not chain:
        raise HTTPException(404, "No supply chain data yet for this merchant")
    return chain


@router.get("/merchants/{merchant_id}/alternatives", response_model=list[Alternative])
def merchant_alternatives(merchant_id: str, db: Database = Depends(get_database)):
    brand = _brand_or_404(db, merchant_id)
    prefer_local = store.load_profile(db, _customer_id(None)).prefer_local
    return supply_chain.get_alternatives(brand.get("categories", []), prefer_local)


@router.get("/supply-chains", response_model=list[SupplyChain])
def all_supply_chains(customer_id: str | None = None, db: Database = Depends(get_database)):
    """Everything the user buys, for the big 'where your money goes' map."""
    ids = repos.distinct_brand_ids(db, _customer_id(customer_id))
    return [c for i in ids if (c := supply_chain.get_supply_chain(i))]


def create_app() -> FastAPI:
    app = FastAPI(title="Accountable API", version="0.2.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted({*config.FRONTEND_ORIGINS, *DEV_ORIGINS}),
        allow_methods=["*"],
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

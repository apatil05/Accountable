from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config, store
from .models import (
    Alternative,
    EthicsReport,
    SpendingSummary,
    SupplyChain,
    UserProfile,
)
from .services import aggregation, ethics, nessie, supply_chain

app = FastAPI(title="Accountable API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_ORIGIN, "http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "mock_data": config.USE_MOCK_DATA,
        "gemini_configured": bool(config.GEMINI_API_KEY),
    }


@app.get("/api/customers")
def customers():
    return nessie.list_customers()


@app.get("/api/spending/summary", response_model=SpendingSummary)
def spending_summary(customer_id: str | None = None):
    cid = customer_id or nessie.default_customer_id()
    profile = store.load_profile()
    # Make sure every merchant has a (mock) report so scores show on the dashboard.
    ids = {p.merchant_id for p in nessie.get_purchases(cid)}
    ethics.warm_cache(
        [(i, m.name) for i in ids if (m := nessie.get_merchant(i))], profile
    )
    return aggregation.spending_summary(cid, profile)


@app.get("/api/spending/transactions")
def transactions(customer_id: str | None = None):
    return nessie.get_purchases(customer_id or nessie.default_customer_id())


@app.get("/api/profile", response_model=UserProfile)
def get_profile():
    return store.load_profile()


@app.put("/api/profile", response_model=UserProfile)
def put_profile(profile: UserProfile):
    saved = store.save_profile(profile)
    ethics._cache.clear()  # personalized scores depend on weights
    return saved


def _merchant_or_404(merchant_id: str):
    m = nessie.get_merchant(merchant_id)
    if not m:
        raise HTTPException(404, f"Unknown merchant {merchant_id}")
    return m


@app.get("/api/merchants/{merchant_id}/ethics", response_model=EthicsReport)
def merchant_ethics(merchant_id: str):
    m = _merchant_or_404(merchant_id)
    report = ethics.get_report(m.id, m.name, store.load_profile())
    if not report:
        raise HTTPException(404, "No ethics data yet for this merchant")
    return report


@app.get("/api/merchants/{merchant_id}/supply-chain", response_model=SupplyChain)
def merchant_supply_chain(merchant_id: str):
    _merchant_or_404(merchant_id)
    chain = supply_chain.get_supply_chain(merchant_id)
    if not chain:
        raise HTTPException(404, "No supply chain data yet for this merchant")
    return chain


@app.get("/api/merchants/{merchant_id}/alternatives", response_model=list[Alternative])
def merchant_alternatives(merchant_id: str):
    m = _merchant_or_404(merchant_id)
    return supply_chain.get_alternatives(m.category, store.load_profile().prefer_local)


@app.get("/api/supply-chains", response_model=list[SupplyChain])
def all_supply_chains(customer_id: str | None = None):
    """Everything the user buys, for the big 'where your money goes' map."""
    ids = {p.merchant_id for p in nessie.get_purchases(customer_id or nessie.default_customer_id())}
    return [c for i in sorted(ids) if (c := supply_chain.get_supply_chain(i))]

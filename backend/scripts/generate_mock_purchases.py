"""Regenerate the demo purchases in app/data/mock_nessie.json.

Owner: Person A. Usage (from backend/):
    python -m scripts.generate_mock_purchases

Simulates about six months of one person's card activity at the 10 demo merchants:
weekday coffee, weekly groceries, gas every week or so, monthly subscriptions, and a
few one-off buys. Amounts are built from realistic prices and Massachusetts tax rules,
descriptions look like card-statement descriptors, the last couple of days are still
"pending", and one Amazon charge was voided ("cancelled").

The random generator has a fixed seed and the date range is fixed, so every run writes
the same file. Customer, account and merchant records in the file are left alone.

Nessie stores amounts as whole dollars, so a live sync of these purchases comes out a
little lower than the mock total.
"""

import json
import random
import string
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Callable, Sequence

from app import config

SEED = 20261114
START = date(2026, 5, 15)
END = date(2026, 11, 13)
PENDING_DAYS = 2
ACCOUNT_ID = "acct_demo"
MOCK_FILE = config.DATA_DIR / "mock_nessie.json"

MEALS_TAX = 0.07  # Massachusetts 6.25% + Amherst's 0.75% local meals tax
SALES_TAX = 0.0625  # groceries and clothing under $175 are exempt in Massachusetts

# Average regular-gas price per gallon in western Massachusetts, by month.
GAS_PRICE_BY_MONTH = {5: 3.249, 6: 3.299, 7: 3.359, 8: 3.319, 9: 3.219, 10: 3.149, 11: 3.099}

Amount = Callable[[random.Random, date], float]
Describe = Callable[[random.Random], str]


@dataclass(frozen=True)
class Draft:
    merchant_id: str
    day: date
    amount: float
    description: str
    status: str = "completed"


# ---------- building blocks ----------
def _money(value: float) -> float:
    return round(value + 1e-9, 2)


def _lognormal(rng: random.Random, median: float, spread: float, low: float, high: float) -> float:
    return min(max(rng.lognormvariate(0, spread) * median, low), high)


def _ref_code(rng: random.Random) -> str:
    return "".join(rng.choices(string.ascii_uppercase + string.digits, k=9))


def _fixed(text: str) -> Describe:
    return lambda rng: text


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def by_weekday(merchant_id: str, odds: Sequence[float], amount: Amount, describe: Describe) -> Callable:
    """Each day, a purchase with the chance given for that weekday (Monday first)."""
    def make(rng: random.Random, days: list[date]) -> list[Draft]:
        return [
            Draft(merchant_id, d, _money(amount(rng, d)), describe(rng))
            for d in days
            if rng.random() < odds[d.weekday()]
        ]
    return make


def every(merchant_id: str, min_gap: int, max_gap: int, amount: Amount, describe: Describe) -> Callable:
    """Repeats every min_gap..max_gap days, starting at a random point in the first gap."""
    def make(rng: random.Random, days: list[date]) -> list[Draft]:
        drafts, day = [], days[0] + timedelta(days=rng.randint(0, max_gap - 1))
        while day <= days[-1]:
            drafts.append(Draft(merchant_id, day, _money(amount(rng, day)), describe(rng)))
            day += timedelta(days=rng.randint(min_gap, max_gap))
        return drafts
    return make


def monthly(merchant_id: str, day_of_month: int, price: float, describe: Describe) -> Callable:
    """A subscription billed on the same day each month."""
    def make(rng: random.Random, days: list[date]) -> list[Draft]:
        return [Draft(merchant_id, d, price, describe(rng)) for d in days if d.day == day_of_month]
    return make


def once(merchant_id: str, day: date, price: float, description: str) -> Callable:
    def make(rng: random.Random, days: list[date]) -> list[Draft]:
        return [Draft(merchant_id, day, _money(price), description)] if days[0] <= day <= days[-1] else []
    return make


# ---------- prices ----------
def _coffee(rng: random.Random, day: date) -> float:
    drink = rng.choice((3.45, 4.65, 5.25, 5.95, 6.45))
    food = rng.choice((2.95, 3.75, 4.45)) if rng.random() < 0.35 else 0
    return (drink + food) * (1 + MEALS_TAX)


def _burrito(rng: random.Random, day: date) -> float:
    entree = rng.choice((10.95, 11.45, 12.25, 13.10))
    chips = 4.95 if rng.random() < 0.3 else 0
    drink = 3.10 if rng.random() < 0.25 else 0
    return (entree + chips + drink) * (1 + MEALS_TAX)


def _gas(rng: random.Random, day: date) -> float:
    return rng.uniform(8.5, 13.5) * GAS_PRICE_BY_MONTH[day.month]


def _basket(median: float, spread: float, low: float, high: float, tax: float = 0.0) -> Amount:
    return lambda rng, day: _lognormal(rng, median, spread, low, high) * (1 + tax)


def _amazon_order(rng: random.Random) -> str:
    return f"AMAZON MKTPL*{_ref_code(rng)} Amzn.com/bill WA"


# Weekday odds run Monday..Sunday.
MERCHANT_PATTERNS = (
    by_weekday("m_starbucks", (0.55, 0.55, 0.55, 0.55, 0.5, 0.25, 0.2), _coffee,
               lambda rng: rng.choice(("STARBUCKS STORE 11423 AMHERST MA", "STARBUCKS STORE 08812 HADLEY MA"))),
    by_weekday("m_chipotle", (0.1, 0.12, 0.12, 0.15, 0.35, 0.18, 0.1), _burrito, _fixed("CHIPOTLE 2741 HADLEY MA")),
    every("m_traderjoes", 6, 8, _basket(52, 0.35, 18, 140), _fixed("TRADER JOE S #509 HADLEY MA")),
    every("m_walmart", 8, 13, _basket(41, 0.45, 8, 160, SALES_TAX / 2), _fixed("WAL-MART #2183 HADLEY MA")),
    every("m_target", 10, 20, _basket(34, 0.5, 6, 120, SALES_TAX / 2), _fixed("TARGET 00018275 HADLEY MA")),
    every("m_shell", 7, 10, _gas, _fixed("SHELL OIL 57543921005 AMHERST MA")),
    every("m_amazon", 6, 14, _basket(24, 0.6, 6, 150, SALES_TAX), _amazon_order),
    monthly("m_amazon", 9, 14.99, lambda rng: f"Amazon Prime*{_ref_code(rng)} Amzn.com/bill WA"),
    monthly("m_apple", 3, 2.99, _fixed("APPLE.COM/BILL CUPERTINO CA")),
    monthly("m_apple", 21, 10.99, _fixed("APPLE.COM/BILL CUPERTINO CA")),
    once("m_apple", date(2026, 9, 20), 249.00 * (1 + SALES_TAX), "APPLE STORE R154 BOSTON MA"),
    once("m_nike", date(2026, 6, 14), 34.99, "NIKE.COM BEAVERTON OR"),
    once("m_nike", date(2026, 8, 23), 109.97, "NIKE.COM BEAVERTON OR"),
    once("m_nike", date(2026, 11, 7), 64.97, "NIKE.COM BEAVERTON OR"),
    every("m_hm", 30, 55, _basket(42, 0.4, 15, 110), _fixed("H&M 0518 HOLYOKE MA")),
)


# ---------- assembly ----------
def _with_status(drafts: list[Draft], end: date) -> list[Draft]:
    pending_from = end - timedelta(days=PENDING_DAYS - 1)
    return [replace(d, status="pending") if d.day >= pending_from else d for d in drafts]


def _with_voided_duplicate(rng: random.Random, drafts: list[Draft]) -> list[Draft]:
    """Real statements show the odd charge that was voided; aggregation must ignore it."""
    orders = [d for d in drafts if d.description.startswith("AMAZON MKTPL") and d.status == "completed"]
    if not orders:
        return drafts
    return [*drafts, replace(rng.choice(orders), status="cancelled")]


def _to_purchase(index: int, draft: Draft) -> dict:
    return {
        "_id": f"p_{index:04d}",
        "merchant_id": draft.merchant_id,
        "payer_id": ACCOUNT_ID,
        "purchase_date": draft.day.isoformat(),
        "amount": draft.amount,
        "status": draft.status,
        "medium": "balance",
        "description": draft.description,
    }


def generate(seed: int = SEED, start: date = START, end: date = END) -> list[dict]:
    """Nessie-shaped purchases, oldest first, with ids p_0001, p_0002, ..."""
    rng, days = random.Random(seed), _days(start, end)
    drafts = [draft for pattern in MERCHANT_PATTERNS for draft in pattern(rng, days)]
    drafts = _with_voided_duplicate(rng, _with_status(drafts, end))
    ordered = sorted(drafts, key=lambda d: (d.day, d.merchant_id, d.amount, d.status))
    return [_to_purchase(i, d) for i, d in enumerate(ordered, start=1)]


def main() -> int:
    data = json.loads(MOCK_FILE.read_text())
    purchases = generate()
    merchant_ids = {m["_id"] for m in data["merchants"]}
    unknown = {p["merchant_id"] for p in purchases} - merchant_ids
    if unknown:
        print(f"Generated purchases for merchants not in the mock file: {sorted(unknown)}", file=sys.stderr)
        return 1
    MOCK_FILE.write_text(json.dumps({**data, "purchases": purchases}, indent=1))
    total = sum(p["amount"] for p in purchases if p["status"] != "cancelled")
    print(f"Wrote {len(purchases)} purchases ({START} to {END}), ${total:,.2f} total.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

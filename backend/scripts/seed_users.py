"""Create the three demo accounts, each with its own six months of card purchases and values.

Owner: Person A. Usage (from backend/):
    python -m scripts.seed_users                      # create or refresh; new accounts get a printed password
    python -m scripts.seed_users --reset-passwords    # give every account a new password

Each user is a separate customer in Atlas, loaded through the same matching and validation as the
Nessie sync (ingest.sync_customer), so their pages work exactly like the demo customer's.
Users only buy from the 10 brands that already have cached research, emissions and labor data.

Safe to re-run: purchases are generated from a fixed seed (same data every run), existing passwords
are kept unless --reset-passwords, and a profile a user has edited is never overwritten.
Passwords are printed once and never stored in plain text.
"""

import argparse
import random
import secrets
import sys
from dataclasses import dataclass
from datetime import date
from typing import Callable

from pymongo.errors import PyMongoError

from app.db import repos
from app.db.client import get_db, ping
from app.db.indexes import ensure_indexes
from app.models import UserProfile
from app.services import auth, ingest
from scripts.generate_mock_purchases import (
    END,
    START,
    Amount,
    Draft,
    _days,
    _fixed,
    _lognormal,
    _ref_code,
    _with_status,
    _with_voided_duplicate,
    by_weekday,
    every,
    monthly,
    once,
)

PASSWORD_BYTES = 12  # token_urlsafe(12) -> 16 characters

# Merchant names and categories, as the Nessie merchant records carry them.
BRAND_MERCHANT = {
    "starbucks": ("Starbucks", ["Food", "Coffee"]),
    "amazon": ("Amazon", ["Shopping", "Online"]),
    "walmart": ("Walmart", ["Shopping", "Groceries"]),
    "target": ("Target", ["Shopping"]),
    "nike": ("Nike", ["Apparel"]),
    "apple": ("Apple", ["Electronics"]),
    "chipotle": ("Chipotle", ["Food", "Restaurant"]),
    "traderjoes": ("Trader Joe's", ["Groceries"]),
    "shell": ("Shell", ["Gas"]),
    "hm": ("H&M", ["Apparel"]),
}


@dataclass(frozen=True)
class DemoUser:
    key: str
    email: str
    name: str
    city: str
    state: str
    zip: str
    lat: float
    lng: float
    seed: int
    profile: UserProfile
    patterns: Callable[["DemoUser"], tuple]

    @property
    def customer_id(self) -> str:
        return f"cust_{self.key}"

    @property
    def account_id(self) -> str:
        return f"acct_{self.key}"

    def merchant_id(self, brand: str) -> str:
        return f"m_{self.key}_{brand}"

    def brand_of(self, merchant_id: str) -> str:
        return merchant_id.removeprefix(f"m_{self.key}_")

    @property
    def brands(self) -> tuple[str, ...]:
        return tuple(sorted({self.brand_of(d.merchant_id) for d in _drafts(self)}))


# ---------- prices ----------
def _taxed(price: float, tax: float) -> float:
    return price * (1 + tax)


def _coffee(tax: float) -> Amount:
    def amount(rng: random.Random, day: date) -> float:
        drink = rng.choice((3.45, 4.65, 5.25, 5.95, 6.45))
        food = rng.choice((2.95, 3.75, 4.45)) if rng.random() < 0.35 else 0
        return _taxed(drink + food, tax)
    return amount


def _chipotle(tax: float, people: tuple[int, ...] = (1,)) -> Amount:
    def amount(rng: random.Random, day: date) -> float:
        bowls = sum(rng.choice((10.95, 11.45, 12.25, 13.10)) for _ in range(rng.choice(people)))
        chips = 4.95 if rng.random() < 0.4 else 0
        return _taxed(bowls + chips, tax)
    return amount


def _basket(median: float, spread: float, low: float, high: float, tax: float = 0.0) -> Amount:
    return lambda rng, day: _taxed(_lognormal(rng, median, spread, low, high), tax)


def _gas(price_per_gallon: float, gallons: tuple[float, float]) -> Amount:
    return lambda rng, day: rng.uniform(*gallons) * price_per_gallon * rng.uniform(0.97, 1.03)


def _amazon(rng: random.Random) -> str:
    return f"AMAZON MKTPL*{_ref_code(rng)} Amzn.com/bill WA"


def _prime(rng: random.Random) -> str:
    return f"Amazon Prime*{_ref_code(rng)} Amzn.com/bill WA"


TX_TAX = 0.0825       # Texas 6.25% + local 2% (Austin, Houston); groceries are exempt
SEATTLE_TAX = 0.1035  # Washington 6.5% + Seattle 3.85%; groceries are exempt
TX_GAS, SEATTLE_GAS = 2.79, 4.39  # regular, $/gallon
APPLE_BILL = "APPLE.COM/BILL CUPERTINO CA"


# ---------- the three people ----------
def _maya(u: "DemoUser") -> tuple:
    """UX designer in Austin, no car: coffee on the way to the studio, lunch out, lots of online orders."""
    m = u.merchant_id
    return (
        by_weekday(m("starbucks"), (0.5, 0.5, 0.45, 0.5, 0.4, 0.3, 0.2), _coffee(TX_TAX),
                   lambda rng: rng.choice(("STARBUCKS STORE 06112 AUSTIN TX", "STARBUCKS STORE 23014 AUSTIN TX"))),
        by_weekday(m("chipotle"), (0.15, 0.2, 0.2, 0.2, 0.3, 0.12, 0.05), _chipotle(TX_TAX),
                   _fixed("CHIPOTLE 1847 AUSTIN TX")),
        every(m("amazon"), 4, 9, _basket(28, 0.55, 7, 180, TX_TAX), _amazon),
        monthly(m("amazon"), 12, 14.99, _prime),
        monthly(m("apple"), 2, 2.99, _fixed(APPLE_BILL)),
        monthly(m("apple"), 18, 10.99, _fixed(APPLE_BILL)),
        once(m("apple"), date(2026, 8, 28), _taxed(1099.00, TX_TAX), "APPLE STORE R462 AUSTIN TX"),
        every(m("target"), 9, 16, _basket(38, 0.45, 9, 140, TX_TAX / 2), _fixed("TARGET 00027631 AUSTIN TX")),
        once(m("nike"), date(2026, 7, 5), _taxed(129.99, TX_TAX), "NIKE.COM BEAVERTON OR"),
        once(m("nike"), date(2026, 10, 18), _taxed(64.99, TX_TAX), "NIKE.COM BEAVERTON OR"),
    )


def _marcus(u: "DemoUser") -> tuple:
    """Nurse in Houston with two kids and a long commute: big weekly grocery runs, lots of gas."""
    m = u.merchant_id
    return (
        every(m("walmart"), 5, 8, _basket(86, 0.35, 25, 260, TX_TAX / 3), _fixed("WAL-MART #1129 HOUSTON TX")),
        every(m("shell"), 4, 6, _gas(TX_GAS, (13.0, 17.5)), _fixed("SHELL OIL 57444213009 HOUSTON TX")),
        by_weekday(m("chipotle"), (0.04, 0.04, 0.05, 0.08, 0.25, 0.2, 0.1), _chipotle(TX_TAX, (2, 3, 4)),
                   _fixed("CHIPOTLE 3305 HOUSTON TX")),
        every(m("amazon"), 7, 15, _basket(32, 0.5, 8, 160, TX_TAX), _amazon),
        monthly(m("amazon"), 4, 14.99, _prime),
        every(m("target"), 12, 20, _basket(55, 0.4, 15, 180, TX_TAX / 2), _fixed("TARGET 00011408 HOUSTON TX")),
        once(m("nike"), date(2026, 8, 8), _taxed(139.97, TX_TAX), "NIKE FACTORY STORE 0294 HOUSTON TX"),
        once(m("nike"), date(2026, 11, 1), _taxed(59.99, TX_TAX), "NIKE.COM BEAVERTON OR"),
        # last in the tuple so it does not shift the random draws of the patterns above
        by_weekday(m("starbucks"), (0.3, 0.35, 0.3, 0.35, 0.3, 0.2, 0.1), _coffee(TX_TAX),
                   lambda rng: rng.choice(("STARBUCKS STORE 08841 HOUSTON TX", "STARBUCKS STORE 14502 HOUSTON TX"))),
    )


def _lena(u: "DemoUser") -> tuple:
    """Middle-school teacher in Seattle: cooks at home, drives a small car now and then, buys clothes often."""
    m = u.merchant_id
    return (
        every(m("traderjoes"), 5, 8, _basket(46, 0.3, 15, 120), _fixed("TRADER JOE S #131 SEATTLE WA")),
        by_weekday(m("starbucks"), (0.3, 0.3, 0.3, 0.3, 0.25, 0.35, 0.2), _coffee(SEATTLE_TAX),
                   lambda rng: rng.choice(("STARBUCKS STORE 00217 SEATTLE WA", "STARBUCKS STORE 19870 SEATTLE WA"))),
        every(m("hm"), 18, 35, _basket(48, 0.4, 15, 140, SEATTLE_TAX), _fixed("H&M 0273 SEATTLE WA")),
        monthly(m("apple"), 7, 0.99, _fixed(APPLE_BILL)),
        monthly(m("apple"), 7, 10.99, _fixed(APPLE_BILL)),
        every(m("amazon"), 12, 25, _basket(22, 0.5, 6, 90, SEATTLE_TAX), _amazon),
        every(m("shell"), 14, 21, _gas(SEATTLE_GAS, (7.0, 10.0)), _fixed("SHELL OIL 57441880014 SEATTLE WA")),
    )


DEMO_USERS: tuple[DemoUser, ...] = (
    DemoUser("maya", "maya@accountable.demo", "Maya Chen", "Austin", "TX", "78701", 30.2672, -97.7431, 1101,
             UserProfile(name="Maya Chen", weights={"labor": 4, "environment": 3, "animal_welfare": 1,
                                                     "community": 2, "privacy": 5},
                         stances="I work in tech, so I care how companies use my data. I also want warehouse "
                                 "and delivery workers treated fairly.", prefer_local=False),
             _maya),
    DemoUser("marcus", "marcus@accountable.demo", "Marcus Johnson", "Houston", "TX", "77002", 29.7604, -95.3698, 2202,
             UserProfile(name="Marcus Johnson", weights={"labor": 5, "environment": 2, "animal_welfare": 1,
                                                          "community": 5, "privacy": 2},
                         stances="I'm a nurse. Fair pay, safe workplaces and keeping money in Houston matter "
                                 "most to me.", prefer_local=True),
             _marcus),
    DemoUser("lena", "lena@accountable.demo", "Lena Park", "Seattle", "WA", "98101", 47.6062, -122.3321, 3303,
             UserProfile(name="Lena Park", weights={"labor": 3, "environment": 5, "animal_welfare": 5,
                                                     "community": 3, "privacy": 2},
                         stances="Climate and animal welfare come first. I'm trying to buy less fast fashion "
                                 "and drive less.", prefer_local=True),
             _lena),
)


# ---------- Nessie-shaped data ----------
def _drafts(user: DemoUser) -> list[Draft]:
    rng, days = random.Random(user.seed), _days(START, END)
    drafts = [d for make in user.patterns(user) for d in make(rng, days)]
    return _with_voided_duplicate(rng, _with_status(drafts, END))


def _purchases(user: DemoUser) -> list[dict]:
    ordered = sorted(_drafts(user), key=lambda d: (d.day, d.merchant_id, d.amount, d.status))
    return [
        {"_id": f"{user.key}_p_{i:04d}", "merchant_id": d.merchant_id, "payer_id": user.account_id,
         "purchase_date": d.day.isoformat(), "amount": d.amount, "status": d.status, "medium": "balance",
         "description": d.description}
        for i, d in enumerate(ordered, start=1)
    ]


def build_customer_data(user: DemoUser) -> dict:
    """The user's customer, card account, merchants and purchases, in Nessie's shape."""
    purchases = _purchases(user)
    merchants = []
    for merchant_id in sorted({p["merchant_id"] for p in purchases}):
        name, category = BRAND_MERCHANT[user.brand_of(merchant_id)]
        merchants.append({
            "_id": merchant_id, "name": name, "category": category,
            "address": {"street_number": "", "street_name": "", "city": user.city, "state": user.state, "zip": user.zip},
            "geocode": {"lat": user.lat, "lng": user.lng},
        })
    first, last = user.name.split(" ", 1)
    return {
        "customer": {"_id": user.customer_id, "first_name": first, "last_name": last,
                     "address": {"city": user.city, "state": user.state, "zip": user.zip}},
        "accounts": [{"_id": user.account_id, "type": "Credit Card", "nickname": f"{first}'s card",
                      "customer_id": user.customer_id}],
        "merchants": merchants,
        "purchases": purchases,
    }


def _fetched(data: dict) -> ingest.Fetched:
    account_id = data["accounts"][0]["_id"]
    return (data["customer"], data["accounts"], [(account_id, p) for p in data["purchases"]],
            {m["_id"]: m for m in data["merchants"]})


# ---------- load ----------
def seed(db, users: tuple[DemoUser, ...] = DEMO_USERS, passwords: dict[str, str] | None = None,
         reset_passwords: bool = False) -> list[tuple[str, str]]:
    """Load every user's data, profile and account. Returns (email, password) for each password set now."""
    passwords = passwords or {}
    issued: list[tuple[str, str]] = []
    for user in users:
        result = ingest.sync_customer(db, user.customer_id, fetched=_fetched(build_customer_data(user)), source="mock")
        if result["errors"]:
            raise RuntimeError(f"{user.email}: {result['errors']}")
        if repos.get_profile(db, user.customer_id) is None:  # never overwrite a profile the user edited
            repos.save_profile(db, user.customer_id, user.profile.model_dump())
        exists = db[auth.USERS].find_one({"_id": user.email}, {"_id": 1}) is not None
        password = passwords.get(user.email)
        if password is None and (reset_passwords or not exists):
            password = secrets.token_urlsafe(PASSWORD_BYTES)
        auth.upsert_user(db, email=user.email, name=user.name, customer_id=user.customer_id, password=password)
        if password is not None:
            issued.append((user.email, password))
    return issued


def _spent(db, customer_id: str) -> float:
    rows = db.purchases.find({"customer_id": customer_id, "status": {"$ne": "cancelled"}}, {"amount_cents": 1})
    return sum(p["amount_cents"] for p in rows) / 100


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset-passwords", action="store_true", help="give every demo account a new password")
    args = parser.parse_args(argv)
    try:
        db = get_db()
        ping(db)
        ensure_indexes(db)
        issued = seed(db, reset_passwords=args.reset_passwords)
        for user in DEMO_USERS:
            print(f"{user.email:<26} {user.name:<16} {user.city:<8} {', '.join(user.brands):<55} "
                  f"${_spent(db, user.customer_id):,.2f}")
    except PyMongoError as exc:
        print(f"Seeding failed: MongoDB error ({type(exc).__name__}). Check MONGODB_URI.", file=sys.stderr)
        return 1
    if issued:
        print("\nPasswords (shown once; share them privately):")
        for email, password in issued:
            print(f"  {email:<26} {password}")
    else:
        print("\nExisting passwords kept. Use --reset-passwords to issue new ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

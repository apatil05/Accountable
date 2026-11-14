import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATA_DIR = Path(__file__).resolve().parent / "data"


class ConfigError(RuntimeError):
    """Raised at startup when required settings are missing."""


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


def _nessie_base_url() -> str:
    url = os.getenv("NESSIE_BASE_URL", "https://api.nessieisreal.com").rstrip("/")
    # Plain http is refused by the Nessie host; upgrade stale .env files copied from old examples.
    return url.replace("http://api.nessieisreal.com", "https://api.nessieisreal.com")


USE_MOCK_DATA = _bool("USE_MOCK_DATA", True)
NESSIE_API_KEY = os.getenv("NESSIE_API_KEY", "")
NESSIE_BASE_URL = _nessie_base_url()
NESSIE_CUSTOMER_ID = os.getenv("NESSIE_CUSTOMER_ID", "")

# MongoDB Atlas. Everyone on the team shares one database (see README).
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB = os.getenv("MONGODB_DB", "accountable")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# US Department of Labor open data (wage and hour cases). Only `python -m scripts.load_data dol`
# needs it; the API itself never calls DOL. Free key: https://dataportal.dol.gov/api-keys
DOL_API_KEY = os.getenv("DOL_API_KEY", "")

# Comma-separated list of allowed frontend origins (CORS).
FRONTEND_ORIGINS = tuple(
    o.strip() for o in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if o.strip()
)

# Anything other than an explicit development value counts as production (strict CORS, no
# /docs), so a typo like "prod" fails safe.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
DEVELOPMENT_ENVS = frozenset({"development", "dev", "local", "test"})

# Shared secret for POST /api/sync (sent as the X-Admin-Token header). Empty = HTTP sync disabled;
# `python -m scripts.sync` always works because it runs with direct database access.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
MIN_ADMIN_TOKEN_LENGTH = 16

# Lets API callers pick a customer with ?customer_id=. Off by default so a public deploy
# only ever shows the configured customer.
ALLOW_CUSTOMER_OVERRIDE = _bool("ALLOW_CUSTOMER_OVERRIDE", False)


def is_production() -> bool:
    return APP_ENV not in DEVELOPMENT_ENVS


def validate() -> None:
    """Fail fast at startup with one clear message instead of a stack trace later."""
    problems = []
    if not MONGODB_URI:
        problems.append("MONGODB_URI is not set (see README: MongoDB Atlas setup)")
    if not USE_MOCK_DATA:
        if not NESSIE_API_KEY:
            problems.append("NESSIE_API_KEY is not set (required when USE_MOCK_DATA=false)")
        if not NESSIE_CUSTOMER_ID:
            problems.append("NESSIE_CUSTOMER_ID is not set (required when USE_MOCK_DATA=false)")
    if ADMIN_TOKEN and len(ADMIN_TOKEN) < MIN_ADMIN_TOKEN_LENGTH:
        problems.append(
            f"ADMIN_TOKEN must be at least {MIN_ADMIN_TOKEN_LENGTH} characters "
            "(generate one: python -c 'import secrets; print(secrets.token_urlsafe(32))')"
        )
    if problems:
        raise ConfigError("; ".join(problems))

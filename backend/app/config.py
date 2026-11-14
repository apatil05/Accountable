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

# Comma-separated list of allowed frontend origins (CORS).
FRONTEND_ORIGINS = tuple(
    o.strip() for o in os.getenv("FRONTEND_ORIGIN", "http://localhost:5173").split(",") if o.strip()
)


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
    if problems:
        raise ConfigError("; ".join(problems))

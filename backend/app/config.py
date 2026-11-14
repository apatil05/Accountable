import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATA_DIR = Path(__file__).resolve().parent / "data"


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes"}


USE_MOCK_DATA = _bool("USE_MOCK_DATA", True)
NESSIE_API_KEY = os.getenv("NESSIE_API_KEY", "")
NESSIE_BASE_URL = os.getenv("NESSIE_BASE_URL", "http://api.nessieisreal.com").rstrip("/")
NESSIE_CUSTOMER_ID = os.getenv("NESSIE_CUSTOMER_ID", "")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

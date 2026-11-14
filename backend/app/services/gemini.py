"""Shared Gemini client. Owner: Person B (Research engine).

google-genai is imported lazily so mock mode runs without the package or a key.
"""

from functools import lru_cache

from .. import config


def enabled() -> bool:
    """Gemini only runs with a key AND USE_MOCK_DATA=false (mock is the demo fallback)."""
    return bool(config.GEMINI_API_KEY) and not config.USE_MOCK_DATA


@lru_cache
def client():
    from google import genai

    return genai.Client(api_key=config.GEMINI_API_KEY)

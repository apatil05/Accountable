"""Shared Gemini client. Owner: Person B (Research engine).

google-genai is imported lazily so mock mode runs without the package or a key.
"""

import threading

from .. import config


def enabled() -> bool:
    """Gemini only runs with a key AND USE_MOCK_DATA=false (mock is the demo fallback)."""
    return bool(config.GEMINI_API_KEY) and not config.USE_MOCK_DATA


_client = None
_client_lock = threading.Lock()


def client():
    """One shared client, created under a lock. (An unlocked lru_cache let concurrent first
    calls build duplicate Clients; the uncached ones were garbage-collected mid-request, and
    genai.Client.__del__ closes the HTTP client: "client has been closed".)"""
    global _client
    with _client_lock:
        if _client is None:
            from google import genai

            _client = genai.Client(api_key=config.GEMINI_API_KEY)
        return _client

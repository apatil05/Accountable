"""Shared pieces of the research pipeline (ethics reports and supply chains)."""


class ResearchUnavailable(RuntimeError):
    """Live research cannot run here: no Gemini key, or the researcher is not implemented yet.

    Distinct from a research *failure* (timeout, bad JSON): prewarm stops on this instead of
    retrying every remaining brand, and the API falls back to mock data."""


def ensure_available(api_key: str) -> None:
    if not api_key:
        raise ResearchUnavailable("GEMINI_API_KEY is not set")

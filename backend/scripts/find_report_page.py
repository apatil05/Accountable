"""Find the emissions table in a sustainability report PDF, to add an entry to company_reports.json.

Usage (from backend/, dev dependencies installed):
    python -m scripts.find_report_page https://example.com/report-2024.pdf

Prints the pages that mention Scope 1/2/3 alongside large numbers, with a snippet of each. Copy
the page number and the figures into app/data/company_reports.json; `python -m scripts.load_data
reports` then re-checks every figure against that page before anything is stored.
"""

import argparse
import io
import re
import sys

import httpx

from app.services import company_reports

SCOPE = re.compile(r"(?i)scope\s*[123]")
BIG_NUMBER = re.compile(r"\b\d{1,3}(?:,\d{3})+\b")
MIN_SCOPE_MENTIONS = 2
SNIPPET_CHARS = 600


def candidate_pages(page_texts: list[str]) -> list[tuple[int, int, str]]:
    """(1-based page, score, snippet) for pages that look like an emissions table, best first."""
    found = []
    for i, text in enumerate(page_texts, start=1):
        flat = re.sub(r"\s+", " ", text)
        scopes, numbers = len(SCOPE.findall(flat)), len(BIG_NUMBER.findall(flat))
        if scopes >= MIN_SCOPE_MENTIONS and numbers:
            start = SCOPE.search(flat).start()
            found.append((i, scopes + numbers, flat[max(start - 100, 0): start + SNIPPET_CHARS]))
    return sorted(found, key=lambda c: -c[1])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url")
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args(argv)
    from pypdf import PdfReader  # dev-only dependency

    try:
        with httpx.Client() as client:
            pdf = company_reports.download(client, args.url)
    except (httpx.HTTPError, company_reports.ReportTooLarge) as exc:
        print(f"Download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]
    candidates = candidate_pages(pages)
    if not candidates:
        print(f"No emissions table found in {len(pages)} pages (scanned image PDF, or numbers in a chart?).")
        return 1
    for page, score, snippet in candidates[: args.top]:
        print(f"\n--- page {page} (score {score}) ---\n{snippet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

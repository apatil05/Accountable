"""Feature 4 (starred on the whiteboard): validate LLM output before showing it.

Owner: Person B (Research engine). Design: docs/VALIDATION_PLAN.md.

Rule: the LLM proposes claims, this module decides whether they're validated.
Claims with no source are never validated.

Per claim:
  1. domain check: blocked domains fail; others get a tier (1 = regulator .. 4 = advocacy/unknown)
  2. claims are grouped by source URL; each page is fetched once (HTML or PDF)
  3. one Gemini "judge" call per page returns, per claim: supported / partial /
     unsupported, a verbatim quote, a rewritten claim, and the event year
  4. code decides `validated`:
       fetched by us -> verdict supported/partial AND page mentions the merchant
                        AND the quote really appears in the page text
       url_context   -> (we could not fetch it) judge verdict supported/partial
       unreadable    -> not validated
Validation details are logged, never returned by the API.
"""

import ipaddress
import json
import logging
import re
import socket
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel

from .. import config
from ..models import CATEGORIES, CategoryScore, Claim

log = logging.getLogger(__name__)
logging.getLogger("pypdf").setLevel(logging.ERROR)  # malformed-PDF warnings are noise

# ---------- 1. domain tiers ----------

BLOCKED = {
    "reddit.com", "x.com", "twitter.com", "facebook.com", "instagram.com", "tiktok.com",
    "quora.com", "medium.com", "youtube.com", "glassdoor.com", "indeed.com", "pinterest.com",
    "linkedin.com", "threads.net", "wikipedia.org",
}
TIER1_SUFFIXES = (".gov", ".mil", ".gov.uk", ".europa.eu", ".gc.ca", ".gov.au")
TIER1 = {"europa.eu", "uscourts.gov", "un.org", "oecd.org", "ilo.org"}
TIER2 = {
    "apnews.com", "reuters.com", "nytimes.com", "wsj.com", "bloomberg.com", "ft.com",
    "bbc.co.uk", "bbc.com", "theguardian.com", "npr.org", "washingtonpost.com", "cnbc.com",
    "economist.com", "axios.com", "propublica.org", "latimes.com", "forbes.com",
    "businessinsider.com", "cnn.com", "nbcnews.com", "cbsnews.com", "abcnews.go.com",
    # international news
    "koreatimes.co.kr", "koreaherald.com", "asia.nikkei.com", "nikkei.com", "japantimes.co.jp",
    "scmp.com", "straitstimes.com", "channelnewsasia.com", "thehindu.com", "hindustantimes.com",
    "aljazeera.com", "dw.com", "france24.com", "lemonde.fr", "spiegel.de", "elpais.com",
    "theglobeandmail.com", "cbc.ca", "abc.net.au", "smh.com.au", "independent.co.uk",
    "telegraph.co.uk", "thetimes.co.uk", "irishtimes.com", "afp.com",
    # US news and business press
    "usatoday.com", "politico.com", "theatlantic.com", "time.com", "vox.com", "fortune.com",
    "marketwatch.com", "barrons.com", "theverge.com", "wired.com", "arstechnica.com",
    "techcrunch.com", "pbs.org", "seattletimes.com", "chicagotribune.com", "bostonglobe.com",
    # trade press (Industry Dive and similar)
    "esgdive.com", "packagingdive.com", "retaildive.com", "fooddive.com", "supplychaindive.com",
    "hrdive.com", "restaurantdive.com", "grocerydive.com", "bleepingcomputer.com",
    "therecord.media", "securityweek.com", "insideclimatenews.org", "grist.org", "trellis.net",
    "greenbiz.com", "just-style.com", "businessoffashion.com",
    # ratings and research
    "cdp.net", "violationtracker.goodjobsfirst.org", "opensecrets.org", "sciencebasedtargets.org",
    "knowthechain.org", "corporatebenchmark.org", "ewg.org", "consumerreports.org",
}
TIER_LABEL = {1: "government or court", 2: "established news or ratings", 3: "the company itself", 4: "advocacy group, law firm, or other"}

# Extra names a page may use for a merchant (legal name, parent company).
ALIASES: dict[str, list[str]] = {
    "H&M": ["Hennes & Mauritz", "Hennes and Mauritz", "H & M"],
    "Shell": ["Royal Dutch Shell", "Shell plc"],
    "Walmart": ["Wal-Mart", "Wal Mart"],
    "Trader Joe's": ["Trader Joes", "Aldi Nord"],
    "Target": ["Target Corporation", "Target Corp"],
    "Apple": ["Apple Inc"],
    "Amazon": ["Amazon.com"],
    "Chipotle": ["Chipotle Mexican Grill"],
    "Nike": ["Nike Inc", "NIKE"],
    "Starbucks": ["Starbucks Corporation"],
}


def _host(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def _matches(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(s))


def domain_tier(url: str, merchant: str) -> int | None:
    """None = blocked. 1 regulator/court, 2 news/ratings, 3 company, 4 other."""
    host = _host(url)
    if not host or _matches(host, BLOCKED):
        return None
    if host.endswith(TIER1_SUFFIXES) or _matches(host, TIER1):
        return 1
    if _matches(host, TIER2):
        return 2
    if any(_slug(n) and _slug(n) in host.replace(".", "").replace("-", "") for n in [merchant, *ALIASES.get(merchant, [])]):
        return 3
    return 4


# ---------- text normalization ----------


_FOLD = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-", 0x00A0: " "}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return s.translate(_FOLD)


def _words(s: str) -> str:
    """Alphanumeric words joined by single spaces, for punctuation-insensitive matching."""
    return " " + " ".join(re.findall(r"[a-z0-9]+", _fold(s).replace("'", ""))) + " "


def mentions_merchant(page_words: str, merchant: str) -> bool:
    return any(_words(n) in page_words for n in [merchant, *ALIASES.get(merchant, [])] if n.strip())


def quote_in_page(quote: str, page_words: str) -> bool:
    q = _words(quote)
    if len(q.split()) < 4:  # too short to prove anything
        return False
    if q in page_words:
        return True
    # PDFs and HTML break lines/hyphenate unpredictably: accept if most 6-word windows match.
    toks = q.split()
    windows = [" " + " ".join(toks[i : i + 6]) + " " for i in range(0, max(1, len(toks) - 5))]
    hits = sum(w in page_words for w in windows)
    return hits / len(windows) >= 0.8


# ---------- 2. fetching ----------

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_MAX_BYTES = 3_000_000
_MAX_REDIRECTS = 5


def _public_host(host: str) -> bool:
    """Refuse private/loopback addresses so a URL can't make us hit internal services."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "template"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if not self.depth and data.strip():
            self.parts.append(data.strip())


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages[:60])


@dataclass
class Page:
    url: str  # final URL after redirects
    text: str | None  # None = we could not read it ourselves


def fetch_page(url: str) -> Page:
    """Fetch and extract text. Returns text=None when blocked, paywalled, or unreadable."""
    current = url
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False, headers={"User-Agent": _UA, "Accept-Language": "en"}) as c:
            for _ in range(_MAX_REDIRECTS + 1):
                p = urlparse(current)
                if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
                    return Page(current, None)
                with c.stream("GET", current) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code != 200:
                        log.debug("fetch %s -> HTTP %s", current, r.status_code)
                        return Page(current, None)
                    data = b""
                    for chunk in r.iter_bytes():
                        data += chunk
                        if len(data) > _MAX_BYTES:
                            break
                    ctype = r.headers.get("content-type", "").lower()
                break
            else:
                return Page(current, None)
    except (httpx.HTTPError, ValueError) as e:
        log.debug("fetch %s failed: %s", current, e)
        return Page(current, None)

    try:
        if "pdf" in ctype or data[:5] == b"%PDF-":
            text = _pdf_text(data)
        else:
            parser = _TextExtractor()
            parser.feed(data.decode(r.encoding or "utf-8", errors="replace"))
            text = "\n".join(parser.parts)
    except Exception as e:  # malformed PDF/HTML
        log.debug("extract %s failed: %s", current, e)
        return Page(current, None)
    # Bot walls and JS shells return a page with almost no text.
    return Page(current, text if len(text) >= 500 else None)


# ---------- 3. judge ----------

_PASSAGE_CHARS = 9_000  # judge input budget; the quote check still uses the full page
# Hard cap per judge call. url_context reads took up to ~100s on PDFs and usually fail on
# bot-walled sites anyway; fetched pages occasionally took ~50s. On timeout the claims are dropped.
_JUDGE_TIMEOUT_MS = 30_000
# Global cap across merchants (warm_cache researches several at once) to stay under rate limits.
_judge_slots = threading.BoundedSemaphore(8)

_JUDGE_PROMPT = """You are checking claims about the company "{merchant}" against one source.
The source is a {tier_label} ({url}).

Each claim is filed under a category. Categories:
{categories}

For each numbered claim, decide from the SOURCE TEXT ONLY (not your own knowledge):
- verdict:
  "supported": the source states the claim's facts. Rewording or adding attribution
    does NOT make a claim partial.
  "partial": ONLY when you had to remove or correct a specific detail (a number, date, scope,
    or who did it) that the source does not support. The rewritten claim must then be fully
    supported by the source.
  "unsupported": the source does not state it, or it is about a different company.
- quote: one sentence copied EXACTLY, character for character, from the source that best supports
  the claim. Empty string if unsupported.
- rewritten_text: the claim, rewritten to state only what the source supports.{attribution}
  Keep it one or two neutral sentences. Empty string if unsupported.
- event_year: the year the event happened, or 0 if unclear.
- category: the category this claim is actually evidence about (usually the one it was filed
  under), or "none" if it says nothing about {merchant}'s conduct in any category
  (e.g. a business statistic, a product description, or general industry context).

CLAIMS:
{claims}
{source}"""

_ATTRIBUTE = (
    '\n  Because this source is {tier_label}, attribute the claim to it instead of stating it as fact '
    '(e.g. "A lawsuit alleges...", "{merchant} says...", "<organization> reports...").'
)

_URL_CONTEXT_FORMAT = """
Read the source at {url} with the URL tool. If you cannot open it, mark every claim "unsupported".
Answer with ONLY a JSON object, no other text, of this form:
{{"claims": [{{"index": 1, "verdict": "...", "quote": "...", "rewritten_text": "...", "event_year": 0, "category": "..."}}]}}"""

_CATEGORY_HINTS = {
    "labor": "wages, unions, working conditions, safety, supply-chain labor",
    "environment": "emissions, climate targets, pollution, packaging, waste, water",
    "animal_welfare": "animal testing, sourcing of animal products, welfare commitments",
    "community": "local businesses and communities, taxes paid, philanthropy",
    "privacy": "customer data collection or sale, breaches, privacy fines and lawsuits",
}


_STOP = set(
    "the and for that with from this have has had was were are its into than then they their "
    "which while about after over also more most such been being other company companies".split()
)


def relevant_passages(text: str, merchant: str, claims: list[str], budget: int = _PASSAGE_CHARS) -> str:
    """Pick the page blocks that share the most terms with the claims (plus a neighbor on each
    side for context), in page order, up to `budget` chars. Long pages made judge calls take ~40s."""
    if len(text) <= budget:
        return text
    blocks = [b for b in re.split(r"\n+", text) if b.strip()]
    # Split very long blocks (PDF pages often come out as one block).
    blocks = [b[i : i + 1200] for b in blocks for i in range(0, len(b), 1200)]
    terms: dict[str, float] = {}
    for c in claims:
        for t in set(_words(c).split()):
            if (len(t) >= 4 or t.isdigit()) and t not in _STOP:
                terms[t] = terms.get(t, 0) + (2.0 if t.isdigit() else 1.0)  # numbers are specific
    for n in [merchant, *ALIASES.get(merchant, [])]:
        for t in _words(n).split():
            terms.setdefault(t, 0.5)
    scores = [sum(terms.get(t, 0) for t in set(_words(b).split())) for b in blocks]
    keep: set[int] = set()
    used = 0
    for i in sorted(range(len(blocks)), key=lambda i: -scores[i]):
        if scores[i] <= 0 or used >= budget:
            break
        for k in (i - 1, i, i + 1):
            if 0 <= k < len(blocks) and k not in keep and used + len(blocks[k]) <= budget:
                keep.add(k)
                used += len(blocks[k])
    out, prev = [], -2
    for k in sorted(keep):
        if k != prev + 1:
            out.append("[...]")
        out.append(blocks[k])
        prev = k
    return "\n".join(out) if keep else text[:budget]


class _Judged(BaseModel):
    index: int
    verdict: str
    quote: str = ""
    rewritten_text: str = ""
    event_year: int = 0
    category: str = ""


class _JudgeOut(BaseModel):
    claims: list[_Judged]


def judge(merchant: str, url: str, tier: int, claims: list[tuple[str, str]], page_text: str | None) -> dict[int, _Judged]:
    """One Gemini call for all claims citing this page. Keys are 0-based claim indices."""
    from google.genai import types

    from . import gemini

    label = TIER_LABEL[tier]
    prompt = _JUDGE_PROMPT.format(
        merchant=merchant,
        tier_label=label,
        url=url,
        attribution=_ATTRIBUTE.format(tier_label=label, merchant=merchant) if tier >= 3 else "",
        categories="\n".join(f"- {k}: {_CATEGORY_HINTS.get(k, k)}" for k in CATEGORIES),
        claims="\n".join(f"{i + 1}. [{cat}] {text}" for i, (cat, text) in enumerate(claims)),
        source=(
            f"\nSOURCE TEXT (relevant passages; [...] marks skipped text):\n"
            f"{relevant_passages(page_text, merchant, [t for _, t in claims])}"
            if page_text is not None
            else _URL_CONTEXT_FORMAT.format(url=url)
        ),
    )
    if page_text is not None:
        cfg = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_JudgeOut,
            temperature=0,
            http_options=types.HttpOptions(timeout=_JUDGE_TIMEOUT_MS),
        )
    else:
        # Tools can't be combined with a response schema, so parse JSON from text.
        cfg = types.GenerateContentConfig(
            tools=[types.Tool(url_context=types.UrlContext())],
            temperature=0,
            http_options=types.HttpOptions(timeout=_JUDGE_TIMEOUT_MS),
        )

    with _judge_slots:
        resp = gemini.client().models.generate_content(model=config.GEMINI_MODEL, contents=prompt, config=cfg)

    if isinstance(resp.parsed, _JudgeOut):
        out = resp.parsed
    else:
        raw = resp.text or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError(f"judge returned no JSON for {url}")
        out = _JudgeOut.model_validate(json.loads(m.group(0)))
    return {j.index - 1: j for j in out.claims if 1 <= j.index <= len(claims)}


# ---------- 4. decision ----------


@dataclass
class _Item:
    cat: CategoryScore
    claim: Claim
    move_to: str | None = None  # judge says the claim belongs in another category

    @property
    def category(self) -> str:
        return self.cat.category


def _validate_source(merchant: str, url: str, items: list[_Item]) -> bool:
    """Validate every claim that cites `url`, in place. Returns False if the judge call failed
    (claims are then left unvalidated and the caller must not cache the report)."""
    t0 = time.monotonic()
    page = fetch_page(url)
    t_fetch = time.monotonic() - t0
    tier = domain_tier(page.url, merchant)
    if tier is None:
        for it in items:
            _log(merchant, it, page.url, None, "blocked", None, False)
        return True

    try:
        judged = judge(merchant, page.url, tier, [(it.category, it.claim.text) for it in items], page.text)
    except Exception as e:
        if _is_timeout(e):
            # Hit the judge cap: drop these claims, but the report is still complete (and cached).
            method = "fetched" if page.text is not None else "url_context"
            log.info("timing merchant=%r method=%s fetch=%.1fs judge=timeout claims=%d url=%s",
                     merchant, method, t_fetch, len(items), page.url)
            for it in items:
                _log(merchant, it, page.url, tier, method + "_timeout", None, False)
            return True
        log.warning("judge failed for %s (%s) after fetch %.1fs: %s", merchant, page.url, t_fetch, e)
        for it in items:
            _log(merchant, it, page.url, tier, "judge_error", None, False)
        return False

    t_judge = time.monotonic() - t0 - t_fetch
    method = "fetched" if page.text is not None else "url_context"
    log.info(
        "timing merchant=%r method=%s fetch=%.1fs judge=%.1fs claims=%d url=%s",
        merchant, method, t_fetch, t_judge, len(items), page.url,
    )

    page_words = _words(page.text) if page.text is not None else None
    for i, it in enumerate(items):
        j = judged.get(i)
        ok = j is not None and j.verdict in ("supported", "partial") and bool(j.rewritten_text.strip())
        if j is not None and ok and page_words is not None:
            ok = mentions_merchant(page_words, merchant) and quote_in_page(j.quote, page_words)
        if j is not None and ok:
            judged_cat = j.category.strip().lower()
            if judged_cat == "none":
                ok = False  # not evidence about the merchant's conduct in any category
            elif judged_cat in CATEGORIES and judged_cat != it.category:
                it.move_to = judged_cat
        if j is not None and ok:
            # Rewritten text carries attribution (low tiers) or is trimmed to what's supported.
            if j.verdict == "partial" or tier >= 3:
                it.claim.text = j.rewritten_text.strip()
            it.claim.source_url = page.url
            it.claim._event_year = j.event_year
            it.claim.confidence = round((1.0 if j.verdict == "supported" else 0.7) * (1.0, 0.95, 0.85, 0.7)[tier - 1], 2)
        else:
            it.claim.confidence = 0.0
        it.claim.validated = ok
        _log(merchant, it, page.url, tier, method, j, ok)
    return True


def _is_timeout(e: BaseException) -> bool:
    while e is not None:
        msg = str(e).lower()
        # Client-side timeout, or Gemini enforcing our deadline server-side (504 DEADLINE_EXCEEDED).
        if isinstance(e, httpx.TimeoutException) or "timed out" in msg or "deadline_exceeded" in msg:
            return True
        e = e.__cause__ or e.__context__
    return False


def _log(merchant, it: _Item, url, tier, method, j: _Judged | None, ok: bool) -> None:
    log.info(
        "validate merchant=%r category=%s judged_category=%s tier=%s method=%s verdict=%s year=%s "
        "validated=%s url=%s claim=%r quote=%r",
        merchant, it.category, (j.category or None) if j else None, tier, method,
        j.verdict if j else None, (j.event_year or None) if j else None,
        ok, url, it.claim.text[:120], (j.quote[:120] if j else ""),
    )


def validate_claims(
    merchant_name: str, categories: list[CategoryScore]
) -> tuple[list[CategoryScore], bool]:
    """Sets claim.validated (and may rewrite claim text) in place.
    Returns (categories, complete); complete=False means a judge call failed."""
    t0 = time.monotonic()
    by_url: dict[str, list[_Item]] = {}
    for cat in categories:
        for claim in cat.claims:
            claim.validated = False
            if claim.source_url:
                by_url.setdefault(claim.source_url, []).append(_Item(cat, claim))
            else:
                _log(merchant_name, _Item(cat, claim), None, None, "no_source", None, False)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda kv: _validate_source(merchant_name, kv[0], kv[1]), by_url.items())
        )

    # Move claims the judge filed under a different category, if that category exists in the report.
    by_cat = {c.category: c for c in categories}
    for items in by_url.values():
        for it in items:
            if it.move_to and it.move_to in by_cat:
                it.cat.claims.remove(it.claim)
                by_cat[it.move_to].claims.append(it.claim)

    log.info(
        "validation merchant=%r sources=%d claims=%d validated=%d took=%.1fs complete=%s",
        merchant_name, len(by_url), sum(len(v) for v in by_url.values()),
        sum(c.validated for cat in categories for c in cat.claims), time.monotonic() - t0, all(results),
    )
    return categories, all(results)

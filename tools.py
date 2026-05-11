"""Source connectors. Each function is a tool the agent can call."""
import os
import re
import time
import httpx
import feedparser
from datetime import datetime, timedelta, timezone
from typing import Any


HTTP_TIMEOUT = 60.0
USER_AGENT = "quant-research-agent/0.1 (research; contact via github)"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Retry on transient HTTP failures: 429 (rate limit) and 5xx (server error).
# Other 4xx (e.g. 404, 400) fail fast — they are not transient.
_RETRY_STATUS = {429, 500, 502, 503, 504}
_RETRY_DELAYS = (0, 2, 5, 15)  # seconds; first attempt is immediate, then back off


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


def _get_with_retry(client: httpx.Client, url: str, **kw) -> httpx.Response:
    """GET with backoff on transient errors (429 + 5xx + network). Fails fast on other 4xx.

    Honors `Retry-After` when the server sends one on a 429.
    """
    last_exc: Exception | None = None
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            r = client.get(url, **kw)
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e
            continue
        if r.status_code in _RETRY_STATUS:
            last_exc = httpx.HTTPStatusError(
                f"transient {r.status_code} from {url}",
                request=r.request, response=r,
            )
            # Respect Retry-After on rate limits.
            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                if ra and ra.isdigit():
                    time.sleep(min(int(ra), 30))
            continue
        r.raise_for_status()
        return r
    assert last_exc is not None
    raise last_exc


# ---------- arXiv ----------

def search_arxiv(query: str, max_results: int = 20, days_back: int = 14) -> list[dict]:
    """Search arXiv q-fin.* by keyword. Returns recent papers with abstracts."""
    params = {
        "search_query": f"cat:q-fin.* AND all:{query}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    with _client() as c:
        r = _get_with_retry(c, "https://export.arxiv.org/api/query", params=params)
    feed = feedparser.parse(r.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    out = []
    for e in feed.entries:
        try:
            pub = datetime.strptime(e.published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except Exception:
            pub = cutoff
        if pub < cutoff:
            continue
        out.append({
            "url": e.link,
            "title": e.title.replace("\n", " ").strip(),
            "authors": ", ".join(a.name for a in getattr(e, "authors", [])),
            "published": e.published,
            "abstract": e.summary.replace("\n", " ").strip()[:2000],
            "categories": [t.term for t in getattr(e, "tags", [])],
        })
    return out


# ---------- GitHub ----------

def search_github(query: str, sort: str = "updated", max_results: int = 15) -> list[dict]:
    """Search GitHub repos. sort = 'updated' or 'stars'.

    If GITHUB_TOKEN env var is set, sends `Authorization: Bearer ...` for the 5000 req/h
    authenticated rate limit. Without it, GitHub allows 60 req/h for the search API.
    """
    params = {"q": query, "sort": sort, "per_page": max_results}
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    with _client() as c:
        r = _get_with_retry(
            c,
            "https://api.github.com/search/repositories",
            params=params,
            headers=headers,
        )
    data = r.json()
    return [{
        "url": it["html_url"],
        "title": it["full_name"],
        "description": it.get("description") or "",
        "stars": it["stargazers_count"],
        "updated": it["updated_at"],
        "language": it.get("language"),
        "topics": it.get("topics", []),
    } for it in data.get("items", [])]


# ---------- Reddit ----------

def fetch_reddit(subreddit: str, timeframe: str = "week", limit: int = 25) -> list[dict]:
    """Top posts from a subreddit. timeframe: hour/day/week/month/year/all."""
    params = {"t": timeframe, "limit": limit}
    with _client() as c:
        r = _get_with_retry(
            c, f"https://www.reddit.com/r/{subreddit}/top.json", params=params,
        )
    posts = r.json().get("data", {}).get("children", [])
    return [{
        "url": "https://reddit.com" + p["data"]["permalink"],
        "title": p["data"]["title"],
        "score": p["data"]["score"],
        "num_comments": p["data"].get("num_comments", 0),
        "selftext": (p["data"].get("selftext") or "")[:2000],
        "external_url": p["data"].get("url"),
        "author": p["data"].get("author"),
    } for p in posts]


# ---------- Quantocracy ----------

def fetch_quantocracy() -> list[dict]:
    """Latest from Quantocracy aggregator (curated quant blogs)."""
    with _client() as c:
        r = _get_with_retry(c, "https://quantocracy.com/feed/")
    feed = feedparser.parse(r.text)
    return [{
        "url": e.link,
        "title": e.title,
        "summary": re.sub(r"<[^>]+>", " ", getattr(e, "summary", ""))[:1500].strip(),
        "published": getattr(e, "published", ""),
    } for e in feed.entries[:30]]


# ---------- Hacker News ----------

def fetch_hn(query: str, days_back: int = 30) -> list[dict]:
    """Search HN via Algolia. Stories only."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff}",
        "hitsPerPage": 30,
    }
    with _client() as c:
        r = _get_with_retry(c, "https://hn.algolia.com/api/v1/search", params=params)
    return [{
        "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
        "title": h.get("title") or "",
        "points": h.get("points", 0),
        "num_comments": h.get("num_comments", 0),
        "created_at": h.get("created_at"),
    } for h in r.json().get("hits", []) if h.get("title")]


# ---------- NBER (working papers) ----------

def fetch_nber(days_back: int = 30) -> list[dict]:
    """NBER working papers via RSS. Strong for macro / labor / monetary research.

    NBER has moved their feed URL around historically; we try a small list and
    use whichever responds. Update _NBER_FEEDS if all fail in production.
    """
    _NBER_FEEDS = [
        "https://www.nber.org/api/v1/working_papers/rss",
        "https://www.nber.org/rss/new.xml",
        "https://www.nber.org/papers/rss",
    ]
    r = None
    last_err = None
    for url in _NBER_FEEDS:
        with _client() as c:
            try:
                r = _get_with_retry(c, url)
                break
            except Exception as e:
                last_err = e
                continue
    if r is None:
        return [{"error": f"NBER feed unavailable (tried {len(_NBER_FEEDS)} URLs): {last_err}"}]
    feed = feedparser.parse(r.text)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    out = []
    for e in feed.entries:
        try:
            pub = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pub = cutoff  # keep entries we can't date — better than dropping silently
        if pub < cutoff:
            continue
        out.append({
            "url": e.link,
            "title": e.title.replace("\n", " ").strip(),
            "authors": getattr(e, "author", ""),
            "published": getattr(e, "published", ""),
            "abstract": re.sub(r"<[^>]+>", " ", getattr(e, "summary", ""))[:2000].strip(),
        })
    return out


# ---------- Papers With Code ----------

def search_paperswithcode(query: str, max_results: int = 20) -> list[dict]:
    """Search Papers With Code — papers paired with open-source implementations.
    Strong for tradable ML methods where you want code, not just a paper."""
    # PWC's /api/v1/search/ endpoint has been serving HTML rather than JSON.
    # The /papers/ endpoint with q= still returns JSON in our testing.
    params = {"q": query, "items_per_page": max_results}
    headers = {"Accept": "application/json"}
    with _client() as c:
        try:
            r = _get_with_retry(
                c, "https://paperswithcode.com/api/v1/papers/",
                params=params, headers=headers,
            )
        except Exception as e:
            return [{"error": f"Papers With Code unavailable: {e}"}]
    try:
        data = r.json()
    except ValueError:
        snippet = r.text[:200].replace("\n", " ")
        return [{"error": f"Papers With Code returned non-JSON (HTTP {r.status_code}): {snippet}"}]
    out = []
    for item in data.get("results", []):
        p = item.get("paper") or {}
        repo = item.get("repository") or {}
        out.append({
            "url": p.get("url_abs") or p.get("url_pdf") or "",
            "title": p.get("title", ""),
            "authors": ", ".join(p.get("authors", []) or []),
            "published": p.get("published", ""),
            "abstract": (p.get("abstract") or "")[:2000],
            "repo_url": repo.get("url"),
            "repo_stars": repo.get("stars"),
            "repo_framework": repo.get("framework"),
        })
    return out


# ---------- Semantic Scholar (broad academic coverage) ----------

def search_semanticscholar(query: str, max_results: int = 20,
                           year_from: int | None = None) -> list[dict]:
    """Semantic Scholar search — indexes arXiv, SSRN, conferences, journals.

    Use as a broad-coverage backstop when arXiv q-fin alone is too narrow,
    or for any topic with strong academic-finance / econ literature.
    """
    fields = (
        "title,authors,abstract,year,venue,url,externalIds,"
        "citationCount,publicationDate,openAccessPdf"
    )
    params = {"query": query, "limit": max_results, "fields": fields}
    if year_from:
        params["year"] = f"{year_from}-"
    with _client() as c:
        try:
            r = _get_with_retry(
                c, "https://api.semanticscholar.org/graph/v1/paper/search", params=params,
            )
        except Exception as e:
            return [{"error": f"Semantic Scholar unavailable: {e}"}]
    data = r.json()
    out = []
    for p in data.get("data", []) or []:
        eids = p.get("externalIds") or {}
        # Prefer specific source URLs when available
        url = p.get("url") or ""
        if eids.get("ArXiv"):
            url = f"https://arxiv.org/abs/{eids['ArXiv']}"
        elif eids.get("DOI"):
            url = f"https://doi.org/{eids['DOI']}"
        out.append({
            "url": url,
            "title": p.get("title", ""),
            "authors": ", ".join(a.get("name", "") for a in (p.get("authors") or [])),
            "venue": p.get("venue") or "",
            "year": p.get("year"),
            "published": p.get("publicationDate", ""),
            "citations": p.get("citationCount", 0),
            "abstract": (p.get("abstract") or "")[:2000],
            "external_ids": eids,
            "open_pdf": (p.get("openAccessPdf") or {}).get("url"),
        })
    return out


# ---------- SSRN (proxied through Semantic Scholar) ----------

def search_ssrn(query: str, max_results: int = 20) -> list[dict]:
    """SSRN-indexed papers via Semantic Scholar.

    SSRN has no public API and is hostile to scraping. Semantic Scholar indexes
    SSRN content and tags it with externalIds.SSRN, so we filter S2 results for
    papers with an SSRN ID. Trade-off: SSRN-only papers without S2 indexing are
    missed; everything returned is verifiably on SSRN.
    """
    # Pre-sleep to space requests when the agent calls semanticscholar
    # and ssrn back-to-back — S2's anonymous tier is strict on rate (often
    # tighter than their published "100 / 5min" suggests).
    time.sleep(4.0)
    # Fetch a moderate over-sample (2x), not 4x — 4x was tripping S2 rate limits.
    raw = search_semanticscholar(query, max_results=min(max_results * 2, 50))
    ssrn = []
    for r in raw:
        if r.get("error"):
            return raw  # propagate the upstream error
        eids = r.get("external_ids") or {}
        # S2 has historically used "SSRN" or "SsrnId" — check both.
        if any(k for k in eids if "ssrn" in k.lower()):
            ssrn.append(r)
        if len(ssrn) >= max_results:
            break
    return ssrn


# ---------- Generic URL fetch ----------

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def fetch_url(url: str, max_chars: int = 12000) -> dict:
    """Fetch a URL and extract readable text. Use to deep-dive a lead."""
    with _client() as c:
        r = _get_with_retry(c, url)
    ct = r.headers.get("content-type", "")
    if "html" not in ct and "xml" not in ct and "text" not in ct:
        return {"url": url, "content_type": ct, "text": f"<non-text content: {ct}>"}
    text = _SCRIPT_RE.sub(" ", r.text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return {
        "url": str(r.url),
        "content_type": ct,
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
    }



TOOL_FUNCS = {
    "search_arxiv": search_arxiv,
    "search_github": search_github,
    "fetch_reddit": fetch_reddit,
    "fetch_quantocracy": fetch_quantocracy,
    "fetch_hn": fetch_hn,
    "fetch_nber": fetch_nber,
    "search_paperswithcode": search_paperswithcode,
    "search_semanticscholar": search_semanticscholar,
    "search_ssrn": search_ssrn,
    "fetch_url": fetch_url,
}

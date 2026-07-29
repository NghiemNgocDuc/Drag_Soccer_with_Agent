"""Semantic Scholar API client for academic paper search."""
from __future__ import annotations
import time
import logging
import requests

BASE_URL = "https://api.semanticscholar.org/graph/v1"

_API_KEY = ""

def _init_api_key():
    global _API_KEY
    if not _API_KEY:
        try:
            from config import SEMANTIC_SCHOLAR_API_KEY
            _API_KEY = SEMANTIC_SCHOLAR_API_KEY or ""
        except Exception:
            pass


def _headers() -> dict:
    _init_api_key()
    h = {"User-Agent": "AgentSoccer/1.0"}
    if _API_KEY:
        h["x-api-key"] = _API_KEY
    return h

_SEARCH_FIELDS = "title,url,abstract,authors,year,venue,citationCount,externalIds,paperId,publicationDate"
_DETAIL_FIELDS = "title,url,abstract,authors,year,venue,citationCount,referenceCount,externalIds,paperId,publicationDate,tldr,fieldsOfStudy"

_CACHE: dict[str, dict] = {}
_CACHE_TTL = 300
_CACHE_TIMESTAMPS: dict[str, float] = {}

_last_request = 0.0

def _rate_limit():
    global _last_request
    now = time.time()
    gap = now - _last_request
    if gap < 0.35:
        time.sleep(0.35 - gap)
    _last_request = time.time()

def _cached(key: str) -> dict | None:
    if key in _CACHE and time.time() - _CACHE_TIMESTAMPS.get(key, 0) < _CACHE_TTL:
        return _CACHE[key]
    return None

def _set_cache(key: str, data: dict):
    _CACHE[key] = data
    _CACHE_TIMESTAMPS[key] = time.time()

def search_papers(query: str, limit: int = 10) -> list[dict]:
    cache_key = f"search:{query}:{limit}"
    cached = _cached(cache_key)
    if cached:
        return cached.get("results", [])

    _rate_limit()
    try:
        resp = requests.get(
            f"{BASE_URL}/paper/search",
            params={"query": query, "limit": min(limit, 50), "fields": _SEARCH_FIELDS},
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(1)
            return search_papers(query, limit)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("data", [])
        _set_cache(cache_key, {"results": results})
        return results
    except requests.RequestException as e:
        logging.warning("Semantic Scholar search error: %s", e)
        return []

def get_paper_detail(paper_id: str) -> dict | None:
    cache_key = f"detail:{paper_id}"
    cached = _cached(cache_key)
    if cached:
        return cached

    _rate_limit()
    try:
        resp = requests.get(
            f"{BASE_URL}/paper/{paper_id}",
            params={"fields": _DETAIL_FIELDS},
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(1)
            return get_paper_detail(paper_id)
        resp.raise_for_status()
        data = resp.json()
        _set_cache(cache_key, data)
        return data
    except requests.RequestException as e:
        logging.warning("Semantic Scholar detail error: %s", e)
        return None

def get_recommended_papers(paper_id: str, limit: int = 5) -> list[dict]:
    cache_key = f"rec:{paper_id}:{limit}"
    cached = _cached(cache_key)
    if cached:
        return cached.get("results", [])

    _rate_limit()
    try:
        resp = requests.get(
            f"{BASE_URL}/paper/{paper_id}/recommendations",
            params={"limit": min(limit, 20), "fields": _SEARCH_FIELDS},
            headers=_headers(),
            timeout=15,
        )
        if resp.status_code == 429:
            time.sleep(1)
            return get_recommended_papers(paper_id, limit)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("recommendedPapers", [])
        _set_cache(cache_key, {"results": results})
        return results
    except requests.RequestException as e:
        logging.warning("Semantic Scholar recs error: %s", e)
        return []

def format_citation(paper: dict) -> str:
    authors = paper.get("authors", [])
    author_str = ", ".join(a.get("name", "") for a in authors[:3])
    if len(authors) > 3:
        author_str += " et al."
    year = paper.get("year", "n.d.")
    title = paper.get("title", "Untitled")
    venue = paper.get("venue", "")
    venue_str = f" ({venue})" if venue else ""
    return f"{author_str} ({year}). {title}.{venue_str}"

SUGGESTED_QUERIES = [
    "reinforcement learning soccer simulation",
    "minimax search game AI planning",
    "monte carlo tree search games",
    "multi-agent reinforcement learningRoboCup",
    "genetic algorithms soccer simulation",
    "deep reinforcement learningteam sports",
    "game tree search adversarial planning",
    "cooperative multi-agent systemsrobotics",
]

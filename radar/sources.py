"""Abruf neuer Artikel aus OpenAlex, Crossref, RSS und CiNii Research.

Alle Funktionen geben Artikel im gemeinsamen Format zurück:
    {id, doi, title, authors, abstract, url, published, journal_name, source}
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import date
from typing import Iterable

import requests

log = logging.getLogger(__name__)

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
CINII = "https://cir.nii.ac.jp/opensearch/articles"

session = requests.Session()
session.headers["User-Agent"] = "fachartikel-radar/1.0 (+https://github.com/fynnholm1-collab/fachartikel-radar)"

# Optional: Kontaktadresse für den "polite pool" von OpenAlex/Crossref, API-Schlüssel.
CONTACT = os.environ.get("RADAR_CONTACT_EMAIL", "")
OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "")
CINII_APPID = os.environ.get("CINII_APPID", "")


def _get(url: str, params: dict | None = None, *, tries: int = 3, timeout: int = 30) -> requests.Response:
    for attempt in range(tries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            if attempt == tries - 1:
                raise
            wait = 2 ** (attempt + 1)
            log.warning("Fehler bei %s (%s), neuer Versuch in %ss", url, exc, wait)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _clean(text: str | None) -> str:
    """JATS/HTML-Tags entfernen und Leerraum glätten."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _doi(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", value.strip(), flags=re.I).lower()


# ---------------------------------------------------------------- OpenAlex

def _oa_params(extra: dict) -> dict:
    params = dict(extra)
    if CONTACT:
        params["mailto"] = CONTACT
    if OPENALEX_KEY:
        params["api_key"] = OPENALEX_KEY
    return params


def openalex_find_source(name: str, issn: str | None = None) -> dict | None:
    """Sucht ein Journal in OpenAlex; bevorzugt exakte Namensgleichheit."""
    if issn:
        r = _get(f"{OPENALEX}/sources/issn:{issn}", _oa_params({}))
        return r.json()
    r = _get(f"{OPENALEX}/sources", _oa_params({"search": name, "per-page": 10}))
    results = [s for s in r.json().get("results", []) if s.get("type") == "journal"] or r.json().get("results", [])
    if not results:
        return None
    target = re.sub(r"\W+", " ", name).strip().lower()
    for s in results:
        if re.sub(r"\W+", " ", s.get("display_name", "")).strip().lower() == target:
            return s
    return results[0]


def _oa_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions = [(pos, word) for word, poss in inv.items() for pos in poss]
    return " ".join(word for _, word in sorted(positions))


def _oa_to_article(w: dict) -> dict:
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    doi = _doi(w.get("doi"))
    return {
        "id": doi or w.get("id", ""),
        "doi": doi,
        "title": _clean(w.get("title") or w.get("display_name")),
        "authors": [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])][:12],
        "abstract": _clean(_oa_abstract(w.get("abstract_inverted_index"))),
        "url": f"https://doi.org/{doi}" if doi else (loc.get("landing_page_url") or w.get("id", "")),
        "published": w.get("publication_date") or "",
        "journal_name": src.get("display_name", ""),
        "source_id": (src.get("id") or "").rsplit("/", 1)[-1],
        "openalex_type": w.get("type", ""),
        "band": str((w.get("biblio") or {}).get("volume") or ""),
        "heft": str((w.get("biblio") or {}).get("issue") or ""),
        "seite": str((w.get("biblio") or {}).get("first_page") or ""),
        "oa_url": (w.get("best_oa_location") or {}).get("pdf_url") or "",
        "source": "openalex",
    }


def openalex_works(source_ids: list[str], since: date, *, max_pages: int = 60, chunk: int = 10) -> list[dict]:
    """Neue Arbeiten aus mehreren Journals.

    Seitenweise mit "page" statt "cursor" (Cursor-Paging verlangt inzwischen einen API-Schlüssel).
    Scheitert eine Folgeseite, bleiben die bereits geholten Seiten erhalten.
    """
    out: list[dict] = []
    for i in range(0, len(source_ids), chunk):
        ids = [s.rsplit("/", 1)[-1] for s in source_ids[i:i + chunk]]
        for page in range(1, max_pages + 1):
            params = _oa_params({
                "filter": f"primary_location.source.id:{'|'.join(ids)},from_publication_date:{since.isoformat()}",
                "per-page": 200,
                "page": page,
            })
            try:
                data = _get(f"{OPENALEX}/works", params).json()
            except requests.RequestException as exc:
                if page == 1:
                    raise
                log.warning("OpenAlex Seite %s abgebrochen (%s); %s Artikel bis hierher behalten", page, exc, len(out))
                break
            results = data.get("results", [])
            out += [_oa_to_article(w) for w in results]
            if len(results) < 200:
                break
    return out


def openalex_search(query: str, since: date, *, subfield: str = "1202", limit: int = 50) -> list[dict]:
    """Volltextsuche in OpenAlex, beschränkt auf ein Fachgebiet (1202 = History)."""
    params = _oa_params({
        "search": query,
        "filter": f"primary_topic.subfield.id:{subfield},from_publication_date:{since.isoformat()}",
        "per-page": min(limit, 200),
    })
    data = _get(f"{OPENALEX}/works", params).json()
    return [_oa_to_article(w) for w in data.get("results", [])]


# ---------------------------------------------------------------- Crossref

def crossref_works(issn: str, since: date, rows: int = 200, max_pages: int = 10) -> list[dict]:
    out = []
    for page in range(max_pages):
        params = {
            "filter": f"from-pub-date:{since.isoformat()}",
            "rows": rows,
            "offset": page * rows,
            "select": "DOI,title,subtitle,author,abstract,published,container-title,URL,type,volume,issue,page",
        }
        if CONTACT:
            params["mailto"] = CONTACT
        items = _get(f"{CROSSREF}/journals/{issn}/works", params).json().get("message", {}).get("items", [])
        for it in items:
            parts = (it.get("published") or {}).get("date-parts") or [[]]
            published = "-".join(f"{p:02d}" for p in parts[0]) if parts[0] else ""
            doi = _doi(it.get("DOI"))
            title = " ".join(it.get("title") or [])
            if it.get("subtitle"):
                title += ": " + " ".join(it["subtitle"])
            out.append({
                "id": doi,
                "doi": doi,
                "title": _clean(title),
                "authors": [" ".join(filter(None, [a.get("given"), a.get("family")])) for a in it.get("author", [])][:12],
                "abstract": _clean(it.get("abstract")),
                "url": f"https://doi.org/{doi}",
                "published": published,
                "journal_name": " ".join(it.get("container-title") or []),
                "band": str(it.get("volume") or ""),
                "heft": str(it.get("issue") or ""),
                "seite": str(it.get("page") or "").split("-")[0],
                "source": "crossref",
            })
        if len(items) < rows:
            break
    return out


# ---------------------------------------------------------------- RSS

def rss_items(url: str) -> list[dict]:
    import feedparser

    feed = feedparser.parse(_get(url).content)
    out = []
    for e in feed.entries:
        doi = _doi(e.get("prism_doi") or e.get("dc_identifier") or "")
        published = ""
        if e.get("published_parsed"):
            published = time.strftime("%Y-%m-%d", e.published_parsed)
        out.append({
            "id": doi or e.get("link", ""),
            "doi": doi,
            "title": _clean(e.get("title")),
            "authors": [a.get("name", "") for a in e.get("authors", [])][:12],
            "abstract": _clean(e.get("summary")),
            "url": f"https://doi.org/{doi}" if doi else e.get("link", ""),
            "published": published,
            "journal_name": feed.feed.get("title", ""),
            "source": "rss",
        })
    return out


# ---------------------------------------------------------------- CiNii Research

def cinii_articles(journal_title: str, since: date, count: int = 200, issn: str | None = None, max_pages: int = 10) -> list[dict]:
    """Neue Aufsätze eines japanischen Journals über CiNii Research (OpenSearch).

    CiNii sucht im Zeitschriftentitel unscharf; deshalb werden nur Treffer behalten,
    deren Zeitschriftentitel genau passt.
    """
    params = {
        "from": since.strftime("%Y%m"),
        "count": count,
        "sortorder": 0,
        "format": "json",
    }
    if issn:
        params["issn"] = issn
    else:
        params["publicationTitle"] = journal_title
    if CINII_APPID:
        params["appid"] = CINII_APPID
    raw: list[dict] = []
    data: dict = {}
    for page in range(max_pages):
        params["start"] = page * count + 1
        data = _get(CINII, params).json()
        batch = data.get("items", []) or []
        raw += batch
        if len(batch) < count:
            break
    want = re.sub(r"\s+", "", journal_title).lower()
    out, other_names = [], set()
    for it in raw:
        name = it.get("prism:publicationName", "") or ""
        if not issn and not re.sub(r"\s+", "", name).lower().startswith(want):
            other_names.add(name)
            continue
        doi = ""
        for ident in it.get("dc:identifier", []) or []:
            if isinstance(ident, dict) and ident.get("@type") == "DOI":
                doi = _doi(ident.get("@value"))
        authors = it.get("dc:creator") or []
        if isinstance(authors, str):
            authors = [authors]
        link = it.get("link")
        link = link.get("@id", "") if isinstance(link, dict) else (link or "")
        out.append({
            "id": doi or it.get("@id", ""),
            "doi": doi,
            "title": _clean(it.get("title")),
            "authors": [a if isinstance(a, str) else a.get("@value", "") for a in authors][:12],
            "abstract": _clean(it.get("description")),
            "url": f"https://doi.org/{doi}" if doi else (link or it.get("@id", "")),
            "published": it.get("prism:publicationDate", ""),
            "journal_name": name or journal_title,
            "band": str(it.get("prism:volume") or ""),
            "heft": str(it.get("prism:number") or ""),
            "seite": str(it.get("prism:startingPage") or ""),
            "source": "cinii",
        })
    if not out and other_names:
        log.info("CiNii %s: %s Treffer mit anderem Zeitschriftentitel, z. B. %s",
                 journal_title, len(other_names), sorted(other_names)[:5])
    elif not raw:
        log.info("CiNii %s: keine Treffer seit %s (Gesamt laut CiNii: %s)",
                 journal_title, since.strftime("%Y-%m"), data.get("opensearch:totalResults"))
    return out


def dedupe(articles: Iterable[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for a in articles:
        key = a.get("doi") or a.get("id") or a.get("title", "").lower()
        if key and key not in seen:
            seen[key] = a
    return list(seen.values())

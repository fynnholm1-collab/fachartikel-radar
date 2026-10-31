"""Prüft, ob ein rezensiertes Buch in der UB Tübingen vorhanden ist.

Abfrage über die SRU-Schnittstelle des Verbundkatalogs K10plus. Ohne KI werden
Buchangaben nur über die ISBN zuverlässig erkannt; ohne ISBN wird mit dem
Rezensionstitel gesucht und das Ergebnis als "unsicher" markiert.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

import requests

log = logging.getLogger(__name__)

ISBN_RE = re.compile(r"\b(?:ISBN(?:-1[03])?:?\s*)?((?:97[89][\s\-]?)?(?:\d[\s\-]?){9}[\dXx])\b")


def find_isbns(text: str) -> list[str]:
    out = []
    for m in ISBN_RE.finditer(text or ""):
        digits = re.sub(r"[\s\-]", "", m.group(1)).upper()
        if len(digits) in (10, 13) and _valid_isbn(digits):
            out.append(digits)
    return list(dict.fromkeys(out))


def _valid_isbn(isbn: str) -> bool:
    if len(isbn) == 10:
        total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(isbn))
        return total % 11 == 0
    if len(isbn) == 13 and isbn.isdigit():
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(isbn))
        return total % 10 == 0
    return False


def book_query_from_title(title: str) -> str:
    """Grober Suchbegriff aus einem Rezensionstitel (ohne KI, daher unsicher)."""
    t = re.sub(r"^(book reviews?|reviews?( of)?|rezension( zu)?|compte[- ]rendu( de)?)[:\s]+", "", title or "", flags=re.I)
    t = re.split(r"\.\s+(?:[A-Z][a-z]+(?:,| \w+)*:|\(|Pp\.|pp\.)", t)[0]  # bis zu Verlagsort/Seitenangabe
    t = re.sub(r"[\"“”'‘’]", "", t)
    return " ".join(t.split()[:12])


def sru_count(base_url: str, cql: str, timeout: int = 20) -> int:
    params = {
        "version": "1.1",
        "operation": "searchRetrieve",
        "query": cql,
        "maximumRecords": "0",
    }
    r = requests.get(base_url, params=params, timeout=timeout)
    r.raise_for_status()
    m = re.search(r"<(?:\w+:)?numberOfRecords>(\d+)<", r.text)
    if not m:
        raise ValueError("Antwort ohne numberOfRecords")
    return int(m.group(1))


def check(title: str, abstract: str, cfg: dict) -> dict:
    """Ergebnis: {status, isbn, suche, link}.

    status = vorhanden | nicht_gefunden (beides nur per ISBN) | wahrscheinlich | unsicher | fehler
    """
    link_tpl = cfg.get("katalog_link", "")
    isbns = find_isbns(f"{title} {abstract}")
    query = isbns[0] if isbns else book_query_from_title(title)
    link = link_tpl.format(q=urllib.parse.quote_plus(query)) if link_tpl and query else ""
    result = {"isbn": isbns[0] if isbns else "", "suche": query, "buchtitel": book_query_from_title(title), "link": link}
    base = cfg.get("sru_url")
    if not base or not query:
        return {**result, "status": "unsicher"}
    try:
        if isbns:
            n = sum(sru_count(base, f"pica.isb={i}") for i in isbns[:2])
            return {**result, "status": "vorhanden" if n else "nicht_gefunden"}
        words = [w for w in re.findall(r"\w+", query) if len(w) > 3][:6]
        if not words:
            return {**result, "status": "unsicher"}
        n = sru_count(base, " and ".join(f"pica.tit={w}" for w in words))
        # Titelsuche ist ohne KI ungenau: Treffer = "wahrscheinlich", kein Treffer = "unsicher".
        return {**result, "status": "wahrscheinlich" if n else "unsicher"}
    except Exception as exc:  # noqa: BLE001
        log.warning("Katalogabfrage fehlgeschlagen (%s): %s", query, exc)
        return {**result, "status": "fehler"}

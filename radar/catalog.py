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
    """Grober Buchtitel aus einem Rezensionstitel (ohne KI, daher unsicher)."""
    title = title or ""
    m = re.search(r"『([^』]+)』", title)
    if m:
        return m.group(1).split(" : ")[0].strip()
    t = re.sub(r"^(book reviews?|reviews?( of)?|a review of|rezension( zu)?|compte[- ]rendu( de)?)[:\s]+", "", title, flags=re.I)
    t = re.sub(r"\s*\(review\)\s*$", "", t)
    t = re.split(r"\s(?:[Ee]dited |[Tt]ranslated )?[Bb]y\s+[A-Z]", t)[0]  # "Titel by Autor"
    t = re.split(r"\.\s+(?:[A-Z][a-z]+(?:,| \w+)*:|\(|Pp\.|pp\.)", t)[0]  # bis zu Verlagsort/Seitenangabe
    t = re.sub(r"[\"“”'‘’]", "", t)
    return " ".join(t.split()[:12])


def book_key(title: str) -> str:
    """Schlüssel, unter dem Rezensionen desselben Buches zusammengefasst werden."""
    t = book_query_from_title(title).lower()
    t = t.split(":")[0] if len(t.split(":")[0]) >= 12 else t
    words = re.findall(r"\w+", t)
    return " ".join(words[:8])


def sru_search(base_url: str, cql: str, max_records: int = 5, timeout: int = 20) -> tuple[int, str]:
    """Gibt (Trefferzahl, Roh-XML im PICA-Format) zurück."""
    params = {
        "version": "1.1",
        "operation": "searchRetrieve",
        "query": cql,
        "maximumRecords": str(max_records),
        "recordSchema": "picaxml",
    }
    r = requests.get(base_url, params=params, timeout=timeout)
    r.raise_for_status()
    m = re.search(r"<(?:\w+:)?numberOfRecords>(\d+)<", r.text)
    if not m:
        raise ValueError("Antwort ohne numberOfRecords")
    return int(m.group(1)), r.text


def held_locally(xml: str, cfg: dict) -> bool | None:
    """True/False, wenn der Bestand der eigenen Bibliothek erkennbar ist, sonst None.

    Erkannt wird über die ISIL (z. B. DE-21) oder die ILN der Bibliothek in den
    Exemplardaten (Feld 101@ $a). Ohne beides lässt sich nur "im Verbund" sagen.
    """
    isil, iln = cfg.get("isil"), cfg.get("iln")
    if iln:
        ilns = re.findall(r'tag="101@".*?code="a">(\d+)<', xml, flags=re.S)
        return str(iln) in ilns
    if isil:
        return f">{isil}<" in xml or None
    return None


_diagnosed = False


def _diagnose(xml: str) -> None:
    """Einmal pro Lauf zeigen, welche Bestandsangaben K10plus liefert (zum Einrichten)."""
    global _diagnosed
    if _diagnosed:
        return
    _diagnosed = True
    ilns = sorted(set(re.findall(r'tag="101@".*?code="a">(\d+)<', xml, flags=re.S)))
    isils = sorted(set(re.findall(r">(DE-[\w-]+)<", xml)))
    log.info("K10plus-Diagnose: ILN im Treffer %s, ISIL %s", ilns[:40], isils[:40])
    # Vollständige 101@-Felder (ILN plus weitere Unterfelder), um die Tübinger Kennung zu finden.
    for field in re.findall(r'<datafield tag="101@">(.*?)</datafield>', xml, flags=re.S)[:12]:
        subs = re.findall(r'code="(\w)">([^<]*)<', field)
        log.info("K10plus-Diagnose 101@: %s", " ".join(f"${c} {v}" for c, v in subs))


def check(title: str, abstract: str, cfg: dict) -> dict:
    """Ergebnis: {status, isbn, suche, link}.

    status = vorhanden | nicht_gefunden | im_verbund (beides nur per ISBN) | wahrscheinlich | unsicher | fehler
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
            n, xml = sru_search(base, " or ".join(f"pica.isb={i}" for i in isbns[:2]))
            if not n:
                return {**result, "status": "nicht_gefunden"}
            _diagnose(xml)
            local = held_locally(xml, cfg)
            status = {True: "vorhanden", False: "nicht_gefunden", None: "im_verbund"}[local]
            return {**result, "status": status}
        words = [w for w in re.findall(r"\w+", query) if len(w) > 3][:6]
        if not words:
            return {**result, "status": "unsicher"}
        n, _ = sru_search(base, " and ".join(f"pica.all={w}" for w in words), max_records=1)
        # Titelsuche ist ohne KI ungenau: Treffer = "wahrscheinlich", kein Treffer = "unsicher".
        return {**result, "status": "wahrscheinlich" if n else "unsicher"}
    except Exception as exc:  # noqa: BLE001
        log.warning("Katalogabfrage fehlgeschlagen (%s): %s", query, exc)
        return {**result, "status": "fehler"}

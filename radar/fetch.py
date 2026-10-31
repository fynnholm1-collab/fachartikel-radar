"""Täglicher Abruf: holt neue Artikel, filtert sie und schreibt docs/data/articles.json.

Aufruf:  python -m radar.fetch
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from radar import catalog, sources
from radar.filtering import Filters, decide

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
log = logging.getLogger("radar")


def load_config() -> tuple[list[dict], list[dict], Filters, dict, dict]:
    cfg_dir = ROOT / "config"
    j = yaml.safe_load((cfg_dir / "journals.yaml").read_text())
    filters = Filters(yaml.safe_load((cfg_dir / "filters.yaml").read_text()))
    settings = yaml.safe_load((cfg_dir / "settings.yaml").read_text())
    resolved_path = cfg_dir / "resolved.json"
    resolved = json.loads(resolved_path.read_text()) if resolved_path.exists() else {}
    return j["journals"], j.get("themensuche", []), filters, settings, resolved


def highlight(article: dict, journal: dict) -> bool:
    words = journal.get("hervorheben") or []
    text = article.get("title", "")
    return any(re.search(rf"\b{re.escape(w)}\b", text, re.I) for w in words)


def collect(journals, searches, resolved, since) -> tuple[list[tuple[dict, dict]], dict]:
    """Holt Rohartikel; gibt (artikel, journal)-Paare und einen Statusbericht zurück."""
    status: dict[str, str] = {}
    pairs: list[tuple[dict, dict]] = []

    by_source_id: dict[str, dict] = {}
    for j in journals:
        quelle = j.get("quelle", "openalex")
        if quelle == "keine":
            status[j["name"]] = "nicht automatisch abrufbar"
            continue
        if quelle == "openalex":
            r = resolved.get(j["name"])
            if r:
                by_source_id[r["openalex_id"]] = j
            elif j.get("issn"):
                j = {**j, "quelle": "crossref"}
            else:
                status[j["name"]] = "nicht zugeordnet (python -m radar.resolve)"
                continue
        if j.get("quelle") == "crossref":
            try:
                items = sources.crossref_works(j["issn"], since)
                pairs += [(a, j) for a in items]
                status[j["name"]] = f"{len(items)} über Crossref"
            except Exception as exc:  # noqa: BLE001
                status[j["name"]] = f"Fehler Crossref: {exc}"
        elif quelle == "rss":
            try:
                items = sources.rss_items(j["rss"])
                items = [a for a in items if not a["published"] or a["published"] >= since.isoformat()]
                pairs += [(a, j) for a in items]
                status[j["name"]] = f"{len(items)} über RSS"
            except Exception as exc:  # noqa: BLE001
                status[j["name"]] = f"Fehler RSS: {exc}"
        elif quelle == "cinii":
            try:
                items = sources.cinii_articles(j["name"], since, issn=j.get("issn"))
                pairs += [(a, j) for a in items]
                status[j["name"]] = f"{len(items)} über CiNii"
            except Exception as exc:  # noqa: BLE001
                status[j["name"]] = f"Fehler CiNii: {exc}"

    if by_source_id:
        try:
            items = sources.openalex_works(list(by_source_id), since)
            counts = Counter()
            for a in items:
                j = by_source_id.get(a.get("source_id", ""))
                if j:
                    pairs.append((a, j))
                    counts[j["name"]] += 1
            for j in by_source_id.values():
                status[j["name"]] = f"{counts[j['name']]} über OpenAlex"
        except Exception as exc:  # noqa: BLE001
            counts = Counter()
            for j in by_source_id.values():
                status[j["name"]] = f"Fehler OpenAlex: {exc}"
        # OpenAlex kennt manche Zeitschriften, indexiert neue Hefte aber verspätet.
        # Dann direkt bei Crossref über die ISSN nachfragen.
        for j in by_source_id.values():
            if counts[j["name"]]:
                continue
            r = resolved.get(j["name"], {})
            issns = [j.get("issn"), r.get("issn_l"), *(r.get("issn") or [])]
            for issn in dict.fromkeys(i for i in issns if i):
                try:
                    items = sources.crossref_works(issn, since)
                except Exception:  # noqa: BLE001
                    continue
                if items:
                    pairs += [(a, j) for a in items]
                    status[j["name"]] = f"{len(items)} über Crossref (OpenAlex leer)"
                    break

    for s in searches:
        try:
            items = sources.openalex_search(s["suche"], since)
            pseudo = {"name": "", "bereich": "fach", "prioritaet": "filter", "thema": s["thema"],
                      "hinweis": f"Themensuche: {s['suche']}"}
            pairs += [(a, {**pseudo, "name": a.get("journal_name") or "unbekannt"}) for a in items]
            status[f"Themensuche {s['suche']}"] = f"{len(items)} über OpenAlex"
        except Exception as exc:  # noqa: BLE001
            status[f"Themensuche {s['suche']}"] = f"Fehler: {exc}"
    return pairs, status


def build_record(a: dict, j: dict, d, filters: Filters, today: str, abstract_len: int) -> dict:
    abstract = a.get("abstract", "")
    if len(abstract) > abstract_len:
        abstract = abstract[:abstract_len].rsplit(" ", 1)[0] + " …"
    return {
        "id": a["id"],
        "doi": a.get("doi", ""),
        "title": a.get("title", ""),
        "authors": [x for x in a.get("authors", []) if x],
        "abstract": abstract,
        "url": a.get("url", ""),
        "oa_url": a.get("oa_url", ""),
        "published": a.get("published", ""),
        "added": today,
        "journal": j["name"],
        "bereich": j.get("bereich", "fach"),
        "prioritaet": j.get("prioritaet", "mittel"),
        "thema": j.get("thema", ""),
        "hinweis": j.get("hinweis", ""),
        "rezension": filters.is_review(a.get("title", "")),
        "hervorheben": highlight(a, j),
        "themen": d.themes,
        "treffer": d.matched,
        "quelle": a.get("source", ""),
    }


def merge(existing: list[dict], new: list[dict], keep_days: int, today: date) -> list[dict]:
    by_id = {a["id"]: a for a in existing}
    for a in new:
        if a["id"] in by_id:
            old = by_id[a["id"]]
            a["added"] = old.get("added", a["added"])
            if old.get("katalog"):
                a["katalog"] = old["katalog"]
        by_id[a["id"]] = a
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    items = [a for a in by_id.values() if a.get("added", "") >= cutoff]
    items.sort(key=lambda a: (a.get("added", ""), a.get("published", "")), reverse=True)
    return items


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    journals, searches, filters, settings, resolved = load_config()
    today = date.today()
    path = DATA / "articles.json"
    first_run = not path.exists() or path.read_text().strip() in ("", "[]")
    days = int(settings.get("erster_abruf_tage", 180) if first_run else settings.get("rueckblick_tage", 60))
    since = today - timedelta(days=days)
    print(f"Abruf seit {since.isoformat()} ({'erster Abruf' if first_run else 'täglich'})")
    pairs, status = collect(journals, searches, resolved, since)

    records, dropped = [], Counter()
    seen: set[str] = set()
    for a, j in pairs:
        if not a.get("id") or not a.get("title") or a["id"] in seen:
            continue
        d = decide(a, j, filters)
        if not d.keep:
            dropped[d.reason] += 1
            continue
        seen.add(a["id"])
        records.append(build_record(a, j, d, filters, today.isoformat(), int(settings.get("abstract_laenge", 1500))))

    DATA.mkdir(parents=True, exist_ok=True)
    existing = json.loads(path.read_text()) if path.exists() else []
    items = merge(existing, records, int(settings.get("aufbewahren_tage", 400)), today)

    kat = settings.get("katalog", {})
    checked = 0
    for a in items:
        if a.get("rezension") and "katalog" not in a and checked < 60:
            a["katalog"] = catalog.check(a["title"], a.get("abstract", ""), kat)
            checked += 1

    path.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n")
    meta = {
        "stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "artikel": len(items),
        "neu_heute": sum(1 for a in items if a["added"] == today.isoformat()),
        "verworfen": dict(dropped),
        "journals": status,
        "katalog": {k: v for k, v in kat.items()},
        "zotero": settings.get("zotero", {}),
    }
    (DATA / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(items)} Artikel gespeichert, {meta['neu_heute']} neu, verworfen: {dict(dropped)}")
    samples: dict[str, list[str]] = {}
    for a in records:
        samples.setdefault(a["journal"], [])
        if len(samples[a["journal"]]) < 2:
            samples[a["journal"]].append(a["title"][:90])
    for name, s in sorted(status.items()):
        print(f"  {name}: {s}")
        for t in samples.get(name, []):
            print(f"      · {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

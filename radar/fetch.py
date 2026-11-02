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
                note = ""
                if j.get("jstage"):
                    note = jstage_redirect(items, j["jstage"], since, j.get("verlag"))
                pairs += [(a, j) for a in items]
                status[j["name"]] = f"{len(items)} über CiNii{note}"
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
                      "filter": s.get("filter", "japan"),
                      "hinweis": f"Themensuche: {s['suche']}"}
            pairs += [(a, {**pseudo, "name": "Themensuche",
                           "hinweis": f"Themensuche {s['suche']}, erschienen in {a.get('journal_name') or 'unbekannt'}"})
                      for a in items]
            status[f"Themensuche {s['suche']}"] = f"{len(items)} über OpenAlex"
        except Exception as exc:  # noqa: BLE001
            status[f"Themensuche {s['suche']}"] = f"Fehler: {exc}"
    return pairs, status


def jstage_redirect(items: list[dict], cdjournal: str, since: date, publishers: list[str] | None = None) -> str:
    """Links von CiNii auf den passenden J-STAGE-Artikel umstellen.

    CiNii liefert manche Aufsätze doppelt (Repositorium mit Untertitel und Monatsdatum,
    J-STAGE mit DOI und nur Jahr). Abgleich über den Haupttitel; Dubletten werden
    zusammengeführt (DOI und J-STAGE-Link, genaueres Datum, längerer Titel/Abstract).
    """
    from urllib.parse import quote

    if publishers:
        # CiNii mischt gleichnamige Zeitschriften (z. B. "Japan Review" des JIIA) unter.
        wanted = [p.lower() for p in publishers]
        items[:] = [a for a in items if not a.get("publisher")
                    or any(w in a["publisher"].lower() for w in wanted)]
    try:
        links = sources.jstage_links(cdjournal, since)
    except Exception as exc:  # noqa: BLE001
        links = {}
        log.warning("J-STAGE %s: %s", cdjournal, exc)
    groups: dict[str, list[dict]] = {}
    for a in items:
        key = sources._norm_title(a.get("title", ""))
        hit = links.get(key)
        if hit:
            a["url"] = hit["url"]
            a["doi"] = a.get("doi") or hit["doi"]
        elif a.get("doi"):
            a["url"] = f"https://doi.org/{a['doi']}"
        groups.setdefault(a.get("doi") or f"ohne:{key}", []).append(a)
    merged, unmatched = [], []
    for key, grp in groups.items():
        base = dict(max(grp, key=lambda x: len(x.get("published") or "")))
        base["title"] = max((x.get("title", "") for x in grp), key=len)
        base["abstract"] = max((x.get("abstract", "") for x in grp), key=len)
        base["authors"] = max((x.get("authors", []) for x in grp), key=len)
        if key.startswith("ohne:"):
            # Noch nicht auf J-STAGE: Titelsuche auf J-STAGE statt CiNii-Seite.
            main = re.split(r"\s*[:：]\s", base["title"] + " ", maxsplit=1)[0].strip()
            base["url"] = f"https://www.jstage.jst.go.jp/result/global/-char/en?globalSearchKey={quote(main)}"
            unmatched.append(base)
        else:
            base["doi"], base["id"], base["url"] = key, key, grp[0]["url"]
        merged.append(base)
    items[:] = merged
    for a in unmatched[:15]:
        log.info("J-STAGE ohne Treffer: %s | %s | %s | %s", a.get("published"), a.get("title", "")[:70],
                 a.get("publisher"), a.get("issn"))
    hits = len(merged) - len(unmatched)
    return f", davon {hits} mit J-STAGE-Link, {len(unmatched)} ohne (J-STAGE kennt {len(links)})"


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
        "oa": bool(a.get("oa") or a.get("oa_url")),
        "published": a.get("published", ""),
        "added": today,
        "journal": j["name"],
        "bereich": j.get("bereich", "fach"),
        "prioritaet": j.get("prioritaet", "mittel"),
        "thema": j.get("thema", ""),
        "hinweis": j.get("hinweis", ""),
        "seiten": a.get("seiten"),
        "sprache": a.get("language", ""),
        "rez_grund": (grund := filters.review_reason(a)),
        "rezension": (rez := bool(grund)),
        "buch": catalog.book_key(a.get("title", "")) if rez else "",
        "buchtitel": catalog.book_query_from_title(a.get("title", "")) if rez else "",
        "kategorie": filters.category(a, j),
        "band": a.get("band", ""),
        "heft": a.get("heft", ""),
        "seite": a.get("seite", ""),
        "hervorheben": highlight(a, j),
        "themen": d.themes,
        "treffer": d.matched,
        "quelle": a.get("source", ""),
    }


def merge(existing: list[dict], new: list[dict], keep_days: int, today: date, oldest: str = "") -> list[dict]:
    by_id = {a["id"]: a for a in existing}
    for a in new:
        if a["id"] in by_id:
            old = by_id[a["id"]]
            a["added"] = old.get("added", a["added"])
            if old.get("katalog"):
                a["katalog"] = old["katalog"]
            if "katalog_buch" in old and "katalog_buch" not in a:
                a["katalog_buch"] = old["katalog_buch"]
        by_id[a["id"]] = a
    cutoff = (today - timedelta(days=keep_days)).isoformat()

    def keep(a: dict) -> bool:
        if a.get("published"):
            return a["published"] >= oldest
        return a.get("added", "") >= cutoff

    items = [a for a in by_id.values() if keep(a)]
    items.sort(key=lambda a: (a.get("published", ""), a.get("added", "")), reverse=True)
    return items


def split_recent(items: list[dict], today: date, months: int) -> tuple[list[dict], list[dict]]:
    """Posteingang (letzte Monate) und Backlog (älter). Backlog nur mit echten Artikeln."""
    border = (today - timedelta(days=round(months * 30.44))).isoformat()
    recent = [a for a in items if (a.get("published") or a.get("added", "")) >= border]
    older = [a for a in items if (a.get("published") or a.get("added", "")) < border and not a.get("rezension")]
    return recent, older


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    journals, searches, filters, settings, resolved = load_config()
    today = date.today()
    path = DATA / "articles.json"
    backlog_path = DATA / "backlog.json"
    meta_path = DATA / "meta.json"
    old_meta = json.loads(meta_path.read_text()) if meta_path.exists() and meta_path.read_text().strip() else {}
    oldest = str(settings.get("backlog_ab", "2023-01-01"))
    stand = int(settings.get("datenstand", 1))
    # Neu nachholen, wenn backlog_ab weiter zurückreicht oder sich die Regeln geändert haben (datenstand).
    backfill = (old_meta.get("abruf_ab") or "9999") > oldest or int(old_meta.get("datenstand", 1)) < stand
    if backfill:
        since = date.fromisoformat(oldest)
    else:
        since = today - timedelta(days=int(settings.get("rueckblick_tage", 60)))
    print(f"Abruf seit {since.isoformat()} ({'Nachholen bis ' + oldest if backfill else 'täglich'})")
    pairs, status = collect(journals, searches, resolved, since)

    records, dropped = [], Counter()
    seen: set[str] = set()
    for a, j in pairs:
        if not a.get("id") or not a.get("title") or a["id"] in seen:
            continue
        if a.get("published") and a["published"] < oldest:
            dropped[f"vor {oldest}"] += 1
            continue
        d = decide(a, j, filters)
        if not d.keep:
            dropped[d.reason] += 1
            continue
        seen.add(a["id"])
        records.append(build_record(a, j, d, filters, today.isoformat(), int(settings.get("abstract_laenge", 1500))))

    DATA.mkdir(parents=True, exist_ok=True)
    existing = []
    for p in (path, backlog_path):
        if p.exists() and p.read_text().strip():
            existing += json.loads(p.read_text())
    if backfill:
        # Vollständiger Neuabruf: alte Einträge nur behalten, wenn sie wieder geliefert wurden
        # oder ihre Zeitschrift diesmal nicht erreichbar war.
        new_ids = {a["id"] for a in records}
        failed = {n for n, st in status.items() if st.startswith("Fehler")}
        existing = [a for a in existing if a["id"] in new_ids or a.get("journal") in failed]
    names = {j["name"] for j in journals} | {"Themensuche"}
    existing = [a for a in existing if a.get("journal") in names]
    items = merge(existing, records, int(settings.get("aufbewahren_tage", 400)), today, oldest)

    # Katalogabgleich: Kandidaten ohne Abstract, deren Titel einem Buch entsprechen könnte.
    kat = settings.get("katalog", {})
    border = (today - timedelta(days=round(int(settings.get("posteingang_monate", 6)) * 30.44))).isoformat()
    probed = 0
    for a in items:
        if probed >= int(kat.get("titelabgleich_max", 250)):
            break
        if ("katalog_buch" in a or (a.get("abstract") or "").strip() or a.get("rezension")
                or (a.get("published") or "") < border or (a.get("seiten") or 0) > 12):
            continue
        a["katalog_buch"] = catalog.is_book_title(a["title"], kat)
        probed += 1
    # Ältere Einträge mit den aktuellen Regeln neu einordnen (z. B. neue Rezensionsmuster).
    for a in items:
        j = {"name": a["journal"], "bereich": a.get("bereich"), "thema": a.get("thema")}
        if filters.is_junk(a.get("title", ""), a["journal"]):
            a["_weg"] = True
            continue
        a["rez_grund"] = filters.review_reason(a)
        a["rezension"] = bool(a["rez_grund"])
        a["buch"] = catalog.book_key(a["title"]) if a["rezension"] else ""
        a["buchtitel"] = catalog.book_query_from_title(a["title"]) if a["rezension"] else ""
        a["kategorie"] = filters.category(a, j)
    items = [a for a in items if not a.pop("_weg", False)]
    recent, older = split_recent(items, today, int(settings.get("posteingang_monate", 6)))
    # Backlog schlank halten (wird auf dem iPad geladen): Abstracts kürzen.
    for a in older:
        if len(a.get("abstract", "")) > 600:
            a["abstract"] = a["abstract"][:600].rsplit(" ", 1)[0] + " …"

    checked = 0
    for a in recent:
        if a.get("rezension") and "katalog" not in a and checked < 60:
            a["katalog"] = catalog.check(a["title"], a.get("abstract", ""), kat)
            checked += 1

    path.write_text(json.dumps(recent, ensure_ascii=False, indent=1) + "\n")
    backlog_path.write_text(json.dumps(older, ensure_ascii=False, separators=(",", ":")) + "\n")
    meta = {
        "stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "abruf_ab": oldest if backfill else old_meta.get("abruf_ab", oldest),
        "datenstand": stand,
        # Tage mit vollständigem Neuabruf: was dort neu hinzukam, ist für die Wochenmail nicht "neu".
        "neuabrufe": sorted(set(old_meta.get("neuabrufe", [])) | ({today.isoformat()} if backfill else set()))[-10:],
        "rezensionen_nach_grund": dict(Counter(a.get("rez_grund") or "keine" for a in recent)),
        "posteingang_monate": int(settings.get("posteingang_monate", 6)),
        "artikel": len(recent),
        "backlog": len(older),
        "neu_heute": sum(1 for a in items if a["added"] == today.isoformat()),
        "verworfen": dict(dropped),
        "reihenfolge": [j["name"] for j in journals] + ["Themensuche"],
        "kategorien": filters.categories + ["Weitere Geschichte"],
        "journals": status,
        "katalog": {k: v for k, v in kat.items()},
        "zotero": settings.get("zotero", {}),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n")
    print(f"Titelabgleich im Katalog: {probed}; Rezensionen im Posteingang nach Grund: {meta['rezensionen_nach_grund']}")
    print(f"{len(recent)} im Posteingang, {len(older)} im Backlog, {meta['neu_heute']} neu, verworfen: {dict(dropped)}")
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

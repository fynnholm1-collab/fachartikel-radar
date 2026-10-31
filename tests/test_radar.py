from datetime import date
from pathlib import Path

import yaml

from radar import catalog, digest
from radar.fetch import merge
from radar.filtering import Filters, decide

ROOT = Path(__file__).resolve().parent.parent
F = Filters(yaml.safe_load((ROOT / "config" / "filters.yaml").read_text()))
JOURNALS = yaml.safe_load((ROOT / "config" / "journals.yaml").read_text())["journals"]

JAPAN = {"name": "Monumenta Nipponica", "bereich": "japan", "prioritaet": "hoch"}
RAND = {"name": "Modern Asian Studies", "bereich": "rand", "prioritaet": "filter", "filter": "japan"}
EH = {"name": "Environmental Humanities", "bereich": "fach", "prioritaet": "filter", "filter": "environmental_humanities"}
FACH = {"name": "American Historical Review", "bereich": "fach", "prioritaet": "hoch"}


def art(title, abstract=""):
    return {"id": title, "title": title, "abstract": abstract}


def test_word_boundaries_and_diacritics():
    assert F.set_matches("japan", "Village life under the Tokugawa")
    assert F.set_matches("japan", "Taishō democracy revisited")
    assert not F.set_matches("japan", "A credo for historians")  # "edo" nur als ganzes Wort
    assert F.set_matches("japan", "日本近世史の課題")


def test_rand_needs_japan_reference():
    assert not decide(art("Mughal court culture"), RAND, F).keep
    assert decide(art("Ryūkyū envoys in Qing China"), RAND, F).keep


def test_epoch_rule():
    # vor Edo ohne weiteres Thema: verworfen
    assert not decide(art("Poetry and politics at the Heian court"), JAPAN, F).keep
    # vor Edo kombiniert mit Tiergeschichte: bleibt
    assert decide(art("Horses and aristocrats in Heian Japan"), JAPAN, F).keep
    # ab Edo: bleibt immer
    assert decide(art("Merchant houses in Edo"), JAPAN, F).keep
    # Bezug über die Epoche hinweg (Kamakura bis Meiji): bleibt
    assert decide(art("Temples from Kamakura to Meiji"), JAPAN, F).keep
    # ohne erkennbare Epoche: bleibt
    assert decide(art("Ghosts and the state"), JAPAN, F).keep


def test_epoch_rule_ignores_non_japan_articles():
    assert decide(art("Kingship in medieval France"), FACH, F).keep


def test_environmental_humanities_filter():
    assert decide(art("Multispecies agency on the shoreline"), EH, F).keep
    assert decide(art("Mountains as archives"), EH, F).keep
    assert not decide(art("Urban gardening in Berlin"), EH, F).keep


def test_combined_filter_set():
    j = {"name": "Humanimalia", "bereich": "fach", "prioritaet": "filter", "filter": "geschichte_oder_japan"}
    assert decide(art("A history of zoo architecture"), j, F).keep
    assert decide(art("Cats in Japanese shrines"), j, F).keep
    assert not decide(art("Pet ownership survey 2024"), j, F).keep


def test_review_detection():
    assert F.is_review("Book Review: Edo Kabuki in Transition")
    assert F.is_review("Merchants of Osaka. By Jane Doe. Honolulu: University of Hawaiʻi Press, 2024. Pp. xii, 300.")
    assert not F.is_review("Reviewing the archive: a methodological essay")


def test_isbn_and_book_title():
    assert catalog.find_isbns("ISBN 978-0-8248-9043-8, paperback") == ["9780824890438"]
    assert catalog.find_isbns("Pp. 300, 1234567890") == []  # ungültige Prüfziffer
    q = catalog.book_query_from_title("Book Review: Fishing Villages of Tokugawa Japan. Honolulu: University of Hawaii Press, 2024.")
    assert q.startswith("Fishing Villages of Tokugawa Japan")


def test_merge_keeps_first_seen_date_and_prunes():
    old = [{"id": "a", "added": "2026-10-01", "title": "x", "katalog": {"status": "vorhanden"}},
           {"id": "b", "added": "2024-01-01", "title": "alt"}]
    new = [{"id": "a", "added": "2026-10-31", "title": "x"}, {"id": "c", "added": "2026-10-31", "title": "y"}]
    out = merge(old, new, 400, date(2026, 10, 31))
    ids = {a["id"]: a for a in out}
    assert ids["a"]["added"] == "2026-10-01" and ids["a"]["katalog"]["status"] == "vorhanden"
    assert "b" not in ids and "c" in ids


def test_journal_config_is_consistent():
    themes = set(yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["zotero"]["unterordner"])
    names = [j["name"] for j in JOURNALS]
    assert len(names) == len(set(names))
    for j in JOURNALS:
        assert j["bereich"] in {"japan", "fach", "rand"}, j
        assert j["prioritaet"] in {"hoch", "filter"}, j
        assert j.get("thema") in themes, j
        if j["prioritaet"] == "filter":
            assert j.get("filter"), j
        if j.get("filter"):
            assert F.known_filter(j["filter"]), j


def test_digest_render():
    items = [
        {"id": "1", "title": "History Lab: Futures", "authors": ["A B"], "journal": "American Historical Review",
         "bereich": "fach", "prioritaet": "hoch", "hervorheben": True, "url": "https://doi.org/x", "added": "2026-10-30"},
        {"id": "2", "title": "Edo fires", "authors": [], "journal": "Monumenta Nipponica",
         "bereich": "japan", "prioritaet": "hoch", "url": "https://doi.org/y", "added": "2026-10-30"},
    ]
    subject, body = digest.render(digest.week_items(items, date(2026, 10, 31)), "https://example.org")
    assert "2 Highlights" in subject and "Edo fires" in body and "Hervorgehoben" in body


def test_junk_entries_are_dropped():
    for t in ["Editorial", "Issue Information", "Front Matter", "Erratum: wrong table", "Ebisu"]:
        assert not decide(art(t), {"name": "Ebisu", "bereich": "japan", "prioritaet": "hoch"}, F).keep, t
    assert decide(art("Editorial practices in Meiji newspapers"), JAPAN, F).keep


def test_catalog_holdings_detection():
    xml = '<record><datafield tag="101@"><subfield code="a">21</subfield></datafield></record>'
    assert catalog.held_locally(xml, {"iln": 21}) is True
    assert catalog.held_locally(xml, {"iln": 99}) is False
    assert catalog.held_locally("<x>DE-21</x>", {"isil": "DE-21"}) is True
    assert catalog.held_locally("<x/>", {"isil": "DE-21"}) is None


def test_crossref_fallback_when_openalex_empty(monkeypatch):
    from radar import fetch, sources
    j = {"name": "Monumenta Nipponica", "bereich": "japan", "prioritaet": "hoch"}
    resolved = {"Monumenta Nipponica": {"openalex_id": "S1", "issn_l": "0027-0741", "issn": ["0027-0741", "1880-1390"]}}
    monkeypatch.setattr(sources, "openalex_works", lambda ids, since: [])
    calls = []

    def fake_crossref(issn, since):
        calls.append(issn)
        return [{"id": "10.1/x", "title": "Edo fires"}] if issn == "1880-1390" else []

    monkeypatch.setattr(sources, "crossref_works", fake_crossref)
    pairs, status = fetch.collect([j], [], resolved, date(2026, 5, 1))
    assert calls == ["0027-0741", "1880-1390"]
    assert len(pairs) == 1 and "Crossref" in status["Monumenta Nipponica"]


def test_more_review_forms():
    assert F.is_review("Strange Tales from Edo: Rewriting Chinese Fiction in Early Modern Japan by William D. Fleming")
    assert F.is_review("Unsilent Strangers: Music, Minorities, Coexistence, Japan Edited by Hugh de Ferranti , Mason Smith")
    assert not F.is_review("Strange Tales from Edo by William D. Fleming", "This article argues …")
    assert not F.is_review("Stories Told by Women")
    assert F.is_review("書評と紹介 日比嘉高著『帝国の書店』")
    assert F.is_review("アニカ・Ａ・カルヴァー『鳥類の大日本帝国 : 貴族、アングロ・アメリカ人』")
    assert not F.is_review("『古事記』の成立と天武朝")


def test_more_junk():
    for t in ["Eightieth Anniversary Message", "特集にあたって", "史学文献目録 : 日本史(4)", "In Memoriam: John Doe"]:
        assert F.is_junk(t), t
    assert not decide({"id": "x", "title": "Merchant houses", "openalex_type": "paratext"}, JAPAN, F).keep


def test_categories():
    assert F.category(art("Merchant houses in Edo"), JAPAN) == "Japanische Geschichte"
    assert F.category(art("Robots and care work today"), {"name": "Contemporary Japan", "bereich": "rand", "thema": "Japan"}) == "Japan allgemein"
    assert F.category(art("Japanese settlers in Truk"), FACH) == "Japanische Geschichte"
    assert F.category(art("Silver flows"), {"name": "Journal of Global History", "bereich": "fach", "thema": "Globalgeschichte"}) == "Globalgeschichte"


def test_book_key_groups_reviews_of_same_book():
    a = catalog.book_key("Strange Tales from Edo: Rewriting Chinese Fiction in Early Modern Japan by William D. Fleming")
    b = catalog.book_key("Book Review: Strange Tales from Edo: Rewriting Chinese Fiction in Early Modern Japan")
    assert a == b
    assert catalog.book_query_from_title("書評と紹介 日比嘉高著『帝国の書店 : 書物が編んだ近代日本』") == "帝国の書店"


def test_split_recent_and_backlog():
    from radar.fetch import split_recent
    items = [{"id": "1", "published": "2026-09-01"}, {"id": "2", "published": "2025-01-01"},
             {"id": "3", "published": "2025-01-01", "rezension": True}]
    recent, older = split_recent(items, date(2026, 10, 31), 6)
    assert [a["id"] for a in recent] == ["1"] and [a["id"] for a in older] == ["2"]


def test_composite_filters():
    assert F.filter_hits("japan_oder_thema", "Whaling and the state in Norway")  # Thema
    assert F.filter_hits("japan_oder_thema", "Meiji constitution")  # Japan
    assert not F.filter_hits("japan_oder_thema", "Parliament in Victorian Britain")
    assert F.filter_hits("japan_und_geschichte_oder_thema", "Japanese settlers in Truk in the 1890s")
    assert not F.filter_hits("japan_und_geschichte_oder_thema", "Japan's defense policy under Kishida")
    assert F.filter_hits("ab_edo_oder_thema", "江戸幕府の対外政策")
    assert not F.filter_hits("ab_edo_oder_thema", "鎌倉幕府地頭制の成立")
    assert not F.theme_hits("A category of catholic thought")  # keine Teilwort-Treffer mehr


def test_language_filter():
    assert not decide({"id": "x", "title": "Le Japon moderne", "language": "fr"}, JAPAN, F).keep
    assert decide({"id": "y", "title": "Merchant houses in Edo", "language": "en"}, JAPAN, F).keep


def test_review_signals():
    assert F.review_reason({"title": "Dissecting the Danchi", "abstract": "", "seiten": 3}) == "kurz ohne Abstract"
    assert F.review_reason({"title": "Dissecting the Danchi", "abstract": "We argue …", "seiten": 3}) == ""
    assert F.review_reason({"title": "Dissecting the Danchi", "abstract": "", "seiten": 25}) == ""
    assert F.review_reason({"title": "Dissecting the Danchi", "abstract": "", "katalog_buch": True}) == "Buch im Katalog"


def test_catalog_book_title(monkeypatch):
    rec = ('<record><datafield tag="002@"><subfield code="0">{bbg}</subfield></datafield>'
           '<datafield tag="021A"><subfield code="a">Dissecting the @Danchi</subfield></datafield></record>')
    monkeypatch.setattr(catalog, "sru_search", lambda base, cql, max_records=5: (1, rec.format(bbg="Aau")))
    assert catalog.is_book_title("Dissecting the Danchi: Inside Japan's Largest Housing Experiment", {"sru_url": "x"}) is True
    monkeypatch.setattr(catalog, "sru_search", lambda base, cql, max_records=5: (1, rec.format(bbg="Asu")))
    assert catalog.is_book_title("Dissecting the Danchi", {"sru_url": "x"}) is False

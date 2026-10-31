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
    sets = set(yaml.safe_load((ROOT / "config" / "filters.yaml").read_text())["sets"])
    themes = set(yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())["zotero"]["unterordner"])
    names = [j["name"] for j in JOURNALS]
    assert len(names) == len(set(names))
    for j in JOURNALS:
        assert j["bereich"] in {"japan", "fach", "rand"}, j
        assert j["prioritaet"] in {"hoch", "mittel", "filter"}, j
        assert j.get("thema") in themes, j
        if j["prioritaet"] == "filter":
            assert j.get("filter") in sets, j
        if j.get("filter"):
            assert j["filter"] in sets, j


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

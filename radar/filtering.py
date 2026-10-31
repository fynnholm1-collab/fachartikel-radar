"""Stichwortfilter, Epochenregel und Rezensionserkennung."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field


def normalize(text: str) -> str:
    """Kleinschreibung, Längenstriche und Akzente entfernen (ō -> o, é -> e)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def _compile_term(term: str) -> re.Pattern:
    term = normalize(term).strip()
    wildcard = term.endswith("*")
    if wildcard:
        term = term[:-1]
    body = re.escape(term).replace(r"\ ", r"[\s\-]+")
    # CJK-Zeichen haben keine Wortgrenzen, dort reicht ein Teilstring-Treffer.
    if re.search(r"[぀-ヿ一-鿿]", term):
        return re.compile(body)
    suffix = r"[\w\-]*" if wildcard else ""
    return re.compile(rf"(?<![\w]){body}{suffix}(?![\w])")


@dataclass
class TermSet:
    terms: list[str]
    patterns: list[re.Pattern] = field(init=False)

    def __post_init__(self):
        self.patterns = [_compile_term(t) for t in self.terms]

    def matches(self, text: str) -> list[str]:
        norm = normalize(text)
        return [t for t, p in zip(self.terms, self.patterns) if p.search(norm)]


class Filters:
    def __init__(self, config: dict):
        raw_sets = config.get("sets", {})
        self.sets: dict[str, TermSet] = {}
        self.combined: dict[str, list[str]] = {}
        for name, terms in raw_sets.items():
            if all(t in raw_sets for t in terms):
                self.combined[name] = list(terms)
            else:
                self.sets[name] = TermSet(terms)
        epochs = config.get("epochen", {})
        self.pre_edo = TermSet(epochs.get("vor_edo", []))
        self.edo_on = TermSet(epochs.get("ab_edo", []))
        self.themes = {k: TermSet(v) for k, v in config.get("themen", {}).items()}
        self.review_patterns = [re.compile(p, re.I) for p in config.get("rezension_muster", [])]
        self.junk_patterns = [re.compile(rf"^\s*(?:{p})\s*[.:]?\s*$", re.I) for p in config.get("ausschliessen", [])]

    def set_matches(self, name: str, text: str) -> list[str]:
        if name in self.combined:
            hits: list[str] = []
            for sub in self.combined[name]:
                hits += self.set_matches(sub, text)
            return hits
        if name not in self.sets:
            raise KeyError(f"Unbekanntes Stichwort-Set: {name}")
        return self.sets[name].matches(text)

    def theme_hits(self, text: str) -> list[str]:
        return [name for name, ts in self.themes.items() if ts.matches(text)]

    def is_pre_edo_only(self, text: str) -> bool:
        return bool(self.pre_edo.matches(text)) and not self.edo_on.matches(text)

    def is_junk(self, title: str, journal_name: str = "") -> bool:
        t = (title or "").strip()
        if not t or (journal_name and normalize(t) == normalize(journal_name)):
            return True
        return any(p.match(t) for p in self.junk_patterns)

    def is_review(self, title: str) -> bool:
        return any(p.search(title or "") for p in self.review_patterns)


@dataclass
class Decision:
    keep: bool
    reason: str
    matched: list[str]
    themes: list[str]


def decide(article: dict, journal: dict, filters: Filters) -> Decision:
    """Entscheidet, ob ein Artikel im Radar erscheint.

    Reihenfolge:
    1. Hat das Journal ein Stichwort-Set ("filter"), muss der Artikel es treffen.
    2. Für Artikel mit Japan-Bezug gilt die Epochenregel: nur vor der Edo-Zeit
       -> erscheint nur, wenn zusätzlich ein Thema getroffen wird.
    Rezensionen werden nicht gefiltert, sondern nur markiert (siehe fetch).
    """
    if filters.is_junk(article.get("title", ""), journal.get("name", "")):
        return Decision(False, "kein Artikel (Editorial, Inhaltsverzeichnis o. Ä.)", [], [])
    text = " ".join(filter(None, [article.get("title"), article.get("abstract")]))
    matched: list[str] = []
    set_name = journal.get("filter")
    if set_name:
        matched = filters.set_matches(set_name, text)
        if not matched:
            return Decision(False, f"kein Treffer im Set '{set_name}'", [], [])

    themes = filters.theme_hits(text)
    japan_related = journal.get("bereich") == "japan" or bool(filters.set_matches("japan", text))
    if japan_related and filters.is_pre_edo_only(text) and not themes:
        return Decision(False, "vor der Edo-Zeit ohne zusätzliches Thema", matched, themes)
    return Decision(True, "ok", matched, themes)

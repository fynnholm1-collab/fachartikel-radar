"""Ordnet jedem Journal aus config/journals.yaml seine OpenAlex-ID zu.

Aufruf:  python -m radar.resolve [--neu]
Ergebnis: config/resolved.json. Bereits zugeordnete Journals werden übersprungen,
außer mit --neu. Das Ergebnis bitte einmal prüfen: Bei Namensgleichheit mit
anderen Zeitschriften kann OpenAlex das falsche Journal liefern. Falsche
Zuordnungen korrigiert man, indem man im Journal-Eintrag eine "issn" angibt.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import yaml

from radar import sources

ROOT = Path(__file__).resolve().parent.parent
RESOLVED = ROOT / "config" / "resolved.json"


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    journals = yaml.safe_load((ROOT / "config" / "journals.yaml").read_text())["journals"]
    resolved = json.loads(RESOLVED.read_text()) if RESOLVED.exists() else {}
    refresh = "--neu" in argv
    problems = 0
    for j in journals:
        name = j["name"]
        if j.get("quelle", "openalex") != "openalex":
            continue
        if name in resolved and not refresh and resolved[name].get("issn_wanted") == j.get("issn"):
            continue
        try:
            s = sources.openalex_find_source(name, j.get("issn"))
        except Exception as exc:  # noqa: BLE001
            print(f"FEHLER   {name}: {exc}")
            problems += 1
            continue
        if not s:
            print(f"FEHLT    {name}: nicht in OpenAlex gefunden")
            resolved.pop(name, None)
            problems += 1
            continue
        same = s.get("display_name", "").lower().replace("the ", "") == name.lower().replace("the ", "")
        resolved[name] = {
            "openalex_id": s["id"].rsplit("/", 1)[-1],
            "display_name": s.get("display_name"),
            "issn_l": s.get("issn_l"),
            "issn": s.get("issn") or [],
            "publisher": s.get("host_organization_name"),
            "works_count": s.get("works_count"),
            "letztes_jahr": max((c.get("year", 0) for c in s.get("counts_by_year", [])), default=None),
            "issn_wanted": j.get("issn"),
        }
        flag = "OK      " if same else "PRÜFEN  "
        if not same:
            problems += 1
        print(f"{flag} {name}  ->  {s.get('display_name')} | ISSN {s.get('issn_l')} | "
              f"{s.get('host_organization_name')} | {s.get('works_count')} Werke")
    RESOLVED.write_text(json.dumps(resolved, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(f"\n{len(resolved)} Journals zugeordnet, {problems} zu prüfen.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

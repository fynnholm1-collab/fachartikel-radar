# Fachartikel-Radar

Ein persönlicher Überblick über neue Artikel aus Japanologie und Geschichtswissenschaft, auf dem Computer und dem iPad.

- **Jeden Morgen** holt GitHub Actions neue Artikel aus OpenAlex, Crossref, RSS und CiNii Research, filtert sie und legt sie in `docs/data/` ab.
- **Die Web-App** (`docs/`) zeigt sie in Reitern: Posteingang, Fach, Rand mit Japan-Bezug, Rezensionen, Gemerkt, Bücher bestellen.
- **Wichtige Artikel** gehen mit Ihrem Kommentar als Notiz nach Zotero, in die Sammlung `_Eingang Fachliteratur/<Thema>`.
- **Rezensionen** werden gegen den Bestand der UB Tübingen (Verbundkatalog K10plus) geprüft. Vorgemerkte Bücher landen in `_Anschaffungswünsche`.
- **Freitags** kommt eine Mail mit den Highlights der Woche.

Alles läuft kostenlos und ohne KI.

## Bedienung

| Knopf | Wirkung |
| --- | --- |
| Öffnen | Artikel beim Verlag öffnen. Mit eingeschaltetem VPN oder nach der Anmeldung per Shibboleth („Login via Institution“, Universität Tübingen) ist der Volltext frei. |
| Wichtig | Kommentar schreiben, Zotero-Ordner wählen und speichern. |
| Später | In „Gemerkt“ ablegen, ohne Zotero. |
| Ignorieren | Ausblenden. |
| Buch vormerken | Bei Rezensionen: das besprochene Buch in die Liste „Bücher bestellen“ und nach Zotero übernehmen. |

Ob ein Artikel schon gesichtet ist, speichert jedes Gerät für sich. Unter **Einstellungen → Sichtungsstand übertragen** lässt sich der Stand exportieren und auf dem anderen Gerät importieren.

## Einrichtung

1. **Web-App veröffentlichen:** Settings → Pages → Source „Deploy from a branch“, Branch `main`, Ordner `/docs`.
2. **Wochenmail:** Settings → Secrets and variables → Actions.
   - Secrets: `SMTP_USER` (Gmail-Adresse, von der verschickt wird), `SMTP_PASSWORD` (ein [Gmail-App-Passwort](https://myaccount.google.com/apppasswords), nicht Ihr normales Passwort), `MAIL_TO` (Empfänger).
   - Variable: `APP_URL` (Adresse der Web-App).
3. **Zotero:** In der Web-App unter Einstellungen die Zotero-Nutzer-ID und einen API-Schlüssel mit Schreibrecht eintragen ([zotero.org/settings/keys](https://www.zotero.org/settings/keys)). Das geschieht auf jedem Gerät einmal. Der Schlüssel bleibt im Browser des Geräts und kommt nicht ins Repository.
4. Optional: `RADAR_CONTACT_EMAIL` als Secret (Kontaktadresse für OpenAlex/Crossref), `OPENALEX_API_KEY`, `CINII_APPID`.

## Journals ändern

Alles steht in `config/`:

- `journals.yaml`: Journals mit Bereich, Priorität, Filter und Zotero-Thema
- `filters.yaml`: Stichwörter, Epochenregel und Muster zum Erkennen von Rezensionen
- `settings.yaml`: Rückblick, Katalog und Zotero-Sammlungen

Neue Journals werden beim nächsten Abruf automatisch in OpenAlex gesucht. Die Zuordnung steht danach in `config/resolved.json`. Liefert OpenAlex das falsche Journal, geben Sie im Eintrag eine `issn` an.

## Regeln

- **Posteingang:** alle Japan-Kern-Journals und Geschichts-Journals mit Priorität „hoch“.
- **Fach:** Geschichts-Journals mit Priorität „mittel“, dazu Treffer aus Filter-Journals und aus der fachweiten Themensuche.
- **Rand:** Asien- und Nachbar-Journals, aber nur Artikel mit Japan-Bezug.
- **Epochenregel:** Artikel mit Japan-Bezug, die nur Epochen vor Edo nennen, erscheinen nur, wenn sie zusätzlich eines Ihrer Themen treffen (z. B. Tiergeschichte und Heian-Zeit).

## Entwicklung

```bash
pip install -r requirements.txt pytest
python -m pytest -q
python -m radar.resolve     # Journals zuordnen
python -m radar.fetch       # Abruf
python -m radar.digest --nur-anzeigen
cd docs && python -m http.server   # App lokal unter http://localhost:8000
```

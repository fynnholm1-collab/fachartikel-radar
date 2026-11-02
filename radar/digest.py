"""Wochenmail mit den Highlights der letzten sieben Tage (neue Artikel mit Japanbezug, AHR-Formate).

Aufruf:  python -m radar.digest [--nur-anzeigen]
Benötigte Umgebungsvariablen (als GitHub-Secrets): SMTP_USER, SMTP_PASSWORD, MAIL_TO.
Optional: SMTP_HOST (Standard smtp.gmail.com), SMTP_PORT (465), APP_URL.
"""

from __future__ import annotations

import html
import json
import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data" / "articles.json"
META = ROOT / "docs" / "data" / "meta.json"
APP_URL = "https://fynnholm1-collab.github.io/fachartikel-radar/"
JAPAN = ("Japanische Geschichte", "Japan allgemein")
# Versand dienstags 6 Uhr deutscher Zeit: der Workflow startet um 4 und um 5 Uhr UTC,
# verschickt wird nur in dem Lauf, der in Berlin gerade 6 Uhr ist.
CRON_SOMMER, CRON_WINTER = "0 4 * * 2", "0 5 * * 2"


def week_items(items: list[dict], today: date, skip_days: tuple[str, ...] = ()) -> list[dict]:
    """Neu in den letzten sieben Tagen; Tage eines vollständigen Neuabrufs zählen nicht."""
    since = (today - timedelta(days=7)).isoformat()
    return [a for a in items if a.get("added", "") > since and a.get("added") not in skip_days]


def highlights(items: list[dict]) -> list[dict]:
    return [a for a in items if not a.get("rezension") and (a.get("hervorheben") or a.get("kategorie") in JAPAN)]


def sections(items: list[dict]) -> list[tuple[str, list[dict]]]:
    hl = highlights(items)
    return [
        ("Hervorgehoben (AHR)", [a for a in hl if a.get("hervorheben")]),
        ("Japanische Geschichte", [a for a in hl if not a.get("hervorheben") and a.get("kategorie") == JAPAN[0]]),
        ("Japan allgemein", [a for a in hl if not a.get("hervorheben") and a.get("kategorie") == JAPAN[1]]),
    ]


def render(items: list[dict], app_url: str = APP_URL) -> tuple[str, str]:
    secs = sections(items)
    n = sum(len(s) for _, s in secs)
    shown = {a["id"] for _, s in secs for a in s}
    others = [a for a in items if not a.get("rezension") and a["id"] not in shown]
    reviews = [a for a in items if a.get("rezension")]
    subject = f"Fachartikel-Radar: {n} Highlights der Woche" if n else "Fachartikel-Radar: diese Woche keine Highlights"
    parts = [f"<h2 style='font-family:sans-serif'>{html.escape(subject)}</h2>"]
    for title, arts in secs:
        if not arts:
            continue
        parts.append(f"<h3 style='font-family:sans-serif;margin-top:24px'>{html.escape(title)} ({len(arts)})</h3><ul>")
        for a in arts:
            authors = ", ".join(a.get("authors", [])[:3])
            parts.append(
                "<li style='margin-bottom:10px;font-family:sans-serif'>"
                f"<a href='{html.escape(a.get('url', ''))}'>{html.escape(a['title'])}</a><br>"
                f"<span style='color:#555'>{html.escape(authors)} · <i>{html.escape(a['journal'])}</i></span></li>"
            )
        parts.append("</ul>")
    parts.append(
        f"<p style='font-family:sans-serif;color:#555'>Außerdem {len(others)} weitere neue Artikel "
        f"und {len(reviews)} neue Rezensionen im Radar.</p>"
    )
    if app_url:
        parts.append(f"<p style='font-family:sans-serif'><a href='{html.escape(app_url)}'>Zum Radar</a></p>")
    return subject, "\n".join(parts)


def due(cron: str, now: datetime | None = None) -> bool:
    """Bei geplanten Läufen nur den Lauf nehmen, der in Berlin 6 Uhr entspricht."""
    if not cron:
        return True  # manuell gestartet
    now = now or datetime.now(timezone.utc)
    offset = now.astimezone(ZoneInfo("Europe/Berlin")).utcoffset() or timedelta(0)
    return cron == (CRON_SOMMER if offset >= timedelta(hours=2) else CRON_WINTER)


def main(argv: list[str]) -> int:
    if not due(os.environ.get("CRON", "")):
        print("Nicht 6 Uhr in Berlin; dieser Lauf verschickt nichts.")
        return 0
    items = json.loads(DATA.read_text()) if DATA.exists() else []
    meta = json.loads(META.read_text()) if META.exists() else {}
    week = week_items(items, date.today(), tuple(meta.get("neuabrufe", [])))
    subject, body = render(week, os.environ.get("APP_URL") or APP_URL)
    if "--nur-anzeigen" in argv:
        print(subject)
        print(body)
        return 0
    user, pw, to = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD"), os.environ.get("MAIL_TO")
    if not (user and pw and to):
        print("SMTP_USER, SMTP_PASSWORD oder MAIL_TO fehlt; keine Mail verschickt.")
        return 1
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    msg.set_content("Bitte in einem HTML-fähigen Mailprogramm öffnen.")
    msg.add_alternative(body, subtype="html")
    host, port = os.environ.get("SMTP_HOST", "smtp.gmail.com"), int(os.environ.get("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port) as s:
        s.login(user, pw)
        s.send_message(msg)
    print(f"Mail an {to} verschickt: {subject}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Wochenmail mit den Highlights der letzten sieben Tage.

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
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data" / "articles.json"


def week_items(items: list[dict], today: date) -> list[dict]:
    since = (today - timedelta(days=7)).isoformat()
    return [a for a in items if a.get("added", "") > since]


def sections(items: list[dict]) -> list[tuple[str, list[dict]]]:
    arts = [a for a in items if not a.get("rezension")]
    hl = [a for a in arts if a.get("hervorheben")]
    japan = [a for a in arts if a["bereich"] == "japan" and not a.get("hervorheben")]
    hoch = [a for a in arts if a["bereich"] == "fach" and a["prioritaet"] == "hoch" and not a.get("hervorheben")]
    rand = [a for a in arts if a["bereich"] == "rand" or a["prioritaet"] == "filter"]
    return [
        ("Hervorgehoben", hl),
        ("Japan", japan),
        ("Geschichte, hohe Priorität", hoch),
        ("Treffer aus Rand- und Filter-Journals", rand),
    ]


def render(items: list[dict], app_url: str) -> tuple[str, str]:
    secs = sections(items)
    reviews = [a for a in items if a.get("rezension")]
    mittel = [a for a in items if not a.get("rezension") and a["bereich"] == "fach" and a["prioritaet"] == "mittel"]
    n = sum(len(s) for _, s in secs)
    subject = f"Fachartikel-Radar: {n} Highlights der Woche"
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
        f"<p style='font-family:sans-serif;color:#555'>Außerdem {len(mittel)} weitere Artikel im Reiter „Fach“ "
        f"und {len(reviews)} neue Rezensionen.</p>"
    )
    if app_url:
        parts.append(f"<p style='font-family:sans-serif'><a href='{html.escape(app_url)}'>Zum Radar</a></p>")
    return subject, "\n".join(parts)


def main(argv: list[str]) -> int:
    items = json.loads(DATA.read_text()) if DATA.exists() else []
    subject, body = render(week_items(items, date.today()), os.environ.get("APP_URL", ""))
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

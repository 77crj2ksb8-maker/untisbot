"""Formatierung der Telegram-Nachrichten.

Getrennt vom Diff, damit sich das Aussehen aendern laesst, ohne die
Erkennungslogik anzufassen -- und damit die Ausgabe testbar bleibt.

Verwendet parse_mode=HTML (nicht Markdown): Fach- und Raumnamen enthalten
regelmaessig Zeichen wie "_" oder "(", die Markdown zerlegen wuerde.
"""

from __future__ import annotations

import datetime as dt

from .diff import Change
from .notify import escape

WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
            "Freitag", "Samstag", "Sonntag"]
WEEKDAYS_SHORT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

#: Symbol pro Aenderungstyp. Bewusst sparsam -- die Nachricht soll auf
#: einen Blick lesbar sein, nicht bunt.
ICONS = {
    "cancelled": "❌",
    "uncancelled": "✅",
    "added": "➕",
    "removed": "➖",
    "subject": "📚",
    "teacher": "👤",
    "room": "🚪",
    "time": "🕐",
    "info": "ℹ️",
}


def format_day_header(datum: str) -> str:
    """'2026-08-20' -> 'Donnerstag, 20.08.'"""
    try:
        day = dt.date.fromisoformat(datum)
    except ValueError:
        return datum
    return f"{WEEKDAYS[day.weekday()]}, {day:%d.%m.}"


def relative_day(datum: str, today: dt.date | None = None) -> str:
    """Gibt 'heute' / 'morgen' zurueck, sonst einen leeren String.

    Diese kleine Zusatzinfo entscheidet oft darueber, ob man die Nachricht
    sofort liest oder wegwischt.
    """
    today = today or dt.date.today()
    try:
        day = dt.date.fromisoformat(datum)
    except ValueError:
        return ""
    delta = (day - today).days
    return {0: "heute", 1: "morgen", 2: "übermorgen"}.get(delta, "")


def format_change(change: Change) -> str:
    """Eine einzelne Aenderung als Zeile."""
    icon = ICONS.get(change.kind, "•")
    zeit = escape(change.start)
    titel = escape(change.titel)

    line = f"{icon} <b>{zeit}</b> {titel} — {escape(change.label)}"

    if change.detail:
        line += f"\n    <i>{escape(change.detail)}</i>"

    return line


def format_changes(
    changes: list[Change],
    today: dt.date | None = None,
    header: str = "Stundenplan-Änderungen",
) -> str:
    """Baut die komplette Nachricht, nach Tagen gruppiert."""
    if not changes:
        return ""

    today = today or dt.date.today()
    lines = [f"<b>{escape(header)}</b>"]

    current_day: str | None = None
    for change in changes:
        if change.datum != current_day:
            current_day = change.datum
            relative = relative_day(current_day, today)
            suffix = f" <i>({relative})</i>" if relative else ""
            lines.append("")
            lines.append(f"<b>{escape(format_day_header(current_day))}</b>{suffix}")
        lines.append(format_change(change))

    return "\n".join(lines)


def format_summary(periods: list[dict], today: dt.date | None = None) -> str:
    """Tagesuebersicht -- fuer den spaeteren /heute-Befehl oder als Abendmeldung."""
    if not periods:
        return "<b>Keine Stunden im Plan.</b>"

    today = today or dt.date.today()
    lines: list[str] = []
    current_day: str | None = None

    for period in sorted(periods, key=lambda p: (p["datum"], p["start"])):
        if period["datum"] != current_day:
            current_day = period["datum"]
            relative = relative_day(current_day, today)
            suffix = f" <i>({relative})</i>" if relative else ""
            lines.append("")
            lines.append(f"<b>{escape(format_day_header(current_day))}</b>{suffix}")

        titel = escape(period.get("titel") or "?")
        raum = escape(", ".join(period.get("raum") or []))
        zeit = escape(period.get("start", ""))

        if period.get("status") == "cancelled":
            lines.append(f"❌ <b>{zeit}</b> <s>{titel}</s>")
        else:
            suffix = f" · {raum}" if raum else ""
            lines.append(f"<b>{zeit}</b> {titel}{suffix}")

    return "\n".join(lines).strip()


def format_error(message: str) -> str:
    """Fehlermeldung an den Admin -- bewusst als solche erkennbar."""
    return (
        "<b>⚠️ Stundenplan-Bot: Problem</b>\n\n"
        f"<pre>{escape(message)}</pre>"
    )

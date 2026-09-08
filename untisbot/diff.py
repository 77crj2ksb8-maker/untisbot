"""Aenderungserkennung -- das Herzstueck des Bots.

Bewusst frei von Netzwerk und Dateizugriff: alle Funktionen hier sind reine
Funktionen ueber Listen von Dicts. Genau deshalb sind sie testbar, ohne auf
eine echte Stundenplanaenderung warten zu muessen.

Der schwierige Teil ist nicht, Unterschiede zu finden -- sondern die
Unterschiede zu ignorieren, die keine sind.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


class SuspiciousDataError(RuntimeError):
    """Die neuen Daten sehen nach einer Stoerung aus, nicht nach echten Aenderungen.

    Wird geworfen, statt hunderte Falschmeldungen zu verschicken. Der
    Aufrufer soll daraufhin den gespeicherten Zustand NICHT ueberschreiben.
    """


# --- Aenderungstypen --------------------------------------------------------
#
# Die Reihenfolge bestimmt die Sortierung in der Nachricht: Wichtiges zuerst.

KIND_ORDER = [
    "cancelled",    # Stunde faellt aus
    "uncancelled",  # Ausfall wurde zurueckgenommen
    "added",        # zusaetzliche Stunde
    "removed",      # Stunde ganz aus dem Plan verschwunden
    "subject",      # anderes Fach
    "teacher",      # andere Lehrkraft
    "room",         # anderer Raum
    "time",         # andere Uhrzeit
    "info",         # nur Zusatztext geaendert
]

KIND_LABEL = {
    "cancelled": "entfÃ¤llt",
    "uncancelled": "findet doch statt",
    "added": "neue Stunde",
    "removed": "nicht mehr im Plan",
    "subject": "Fachwechsel",
    "teacher": "Vertretung",
    "room": "Raumwechsel",
    "time": "ZeitÃ¤nderung",
    "info": "Info geÃ¤ndert",
}


@dataclass
class Change:
    """Eine einzelne erkannte Aenderung."""

    kind: str
    datum: str          # YYYY-MM-DD
    start: str          # HH:MM
    ende: str
    titel: str
    detail: str = ""
    period: dict = field(default_factory=dict, repr=False)

    @property
    def sort_key(self) -> tuple:
        rank = KIND_ORDER.index(self.kind) if self.kind in KIND_ORDER else 99
        return (self.datum, self.start, rank, self.titel)

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind, self.kind)


# --- Hilfsfunktionen --------------------------------------------------------


def in_window(period: dict, start: dt.date, end: dt.date) -> bool:
    """Liegt die Stunde im angefragten Zeitfenster?"""
    try:
        day = dt.date.fromisoformat(period["datum"])
    except (KeyError, ValueError):
        return False
    return start <= day <= end


def prune(periods: list[dict], start: dt.date, end: dt.date) -> list[dict]:
    """Beschneidet eine Liste auf das Zeitfenster.

    DER wichtigste Schritt gegen Fehlalarme: Wenn heute 7 Tage abgefragt
    werden, sind die Stunden von gestern morgen nicht mehr dabei. Ohne
    Beschneiden wuerde der Bot sie als "aus dem Plan verschwunden" melden.
    """
    return [p for p in periods if in_window(p, start, end)]


def _match(old: list[dict], new: list[dict]) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Ordnet alte und neue Stunden einander zu.

    Zweistufig, weil keine der beiden Kennungen allein zuverlaessig ist:
      1. ueber die WebUntis-id  -- praezise, aber neu angelegte Vertretungen
         bekommen eine neue id
      2. ueber den key (Datum|Zeit|Fach) -- ueberlebt id-Wechsel

    Rueckgabe: (paare, nur_alt, nur_neu)
    """
    pairs: list[tuple[dict, dict]] = []

    old_by_id = {p["id"]: p for p in old if p.get("id") is not None}
    new_by_id = {p["id"]: p for p in new if p.get("id") is not None}

    matched_old: set[int] = set()
    matched_new: set[int] = set()

    # Stufe 1: id
    for period_id, old_p in old_by_id.items():
        new_p = new_by_id.get(period_id)
        if new_p is not None:
            pairs.append((old_p, new_p))
            matched_old.add(id(old_p))
            matched_new.add(id(new_p))

    # Stufe 2: key, nur fuer die Uebriggebliebenen
    rest_old = [p for p in old if id(p) not in matched_old]
    rest_new = [p for p in new if id(p) not in matched_new]

    rest_new_by_key: dict[str, list[dict]] = {}
    for p in rest_new:
        rest_new_by_key.setdefault(p.get("key", ""), []).append(p)

    for old_p in rest_old:
        candidates = rest_new_by_key.get(old_p.get("key", ""))
        if candidates:
            new_p = candidates.pop(0)
            pairs.append((old_p, new_p))
            matched_old.add(id(old_p))
            matched_new.add(id(new_p))

    only_old = [p for p in old if id(p) not in matched_old]
    only_new = [p for p in new if id(p) not in matched_new]

    return pairs, only_old, only_new


def _fmt_list(values: list[str]) -> str:
    return ", ".join(values) if values else "â"


def _compare_pair(old: dict, new: dict) -> list[Change]:
    """Vergleicht eine zugeordnete Stunde Feld fuer Feld."""
    changes: list[Change] = []

    def make(kind: str, detail: str) -> Change:
        return Change(
            kind=kind,
            datum=new.get("datum", ""),
            start=new.get("start", ""),
            ende=new.get("ende", ""),
            titel=new.get("titel") or "Stunde",
            detail=detail,
            period=new,
        )

    old_status = old.get("status", "regular")
    new_status = new.get("status", "regular")

    # Entfall hat Vorrang -- wenn eine Stunde ausfaellt, interessiert
    # niemanden mehr, dass sich nebenbei der Raum geaendert hat.
    if old_status != "cancelled" and new_status == "cancelled":
        detail = new.get("info") or new.get("text") or ""
        return [make("cancelled", detail)]

    if old_status == "cancelled" and new_status != "cancelled":
        return [make("uncancelled", "Der Ausfall wurde zurÃ¼ckgenommen")]

    if new_status == "cancelled":
        return []  # war vorher schon abgesagt, nichts Neues

    # Inhaltliche Aenderungen
    if old.get("fach") != new.get("fach"):
        changes.append(make(
            "subject",
            f"{_fmt_list(old.get('fach', []))} â {_fmt_list(new.get('fach', []))}",
        ))

    if old.get("lehrer") != new.get("lehrer"):
        changes.append(make(
            "teacher",
            f"{_fmt_list(old.get('lehrer', []))} â {_fmt_list(new.get('lehrer', []))}",
        ))

    if old.get("raum") != new.get("raum"):
        changes.append(make(
            "room",
            f"{_fmt_list(old.get('raum', []))} â {_fmt_list(new.get('raum', []))}",
        ))

    if (old.get("start"), old.get("ende")) != (new.get("start"), new.get("ende")):
        changes.append(make(
            "time",
            f"{old.get('start')}-{old.get('ende')} â {new.get('start')}-{new.get('ende')}",
        ))

    # Freitexte nur melden, wenn sonst nichts passiert ist -- sonst waere
    # jede Vertretung doppelt in der Nachricht.
    if not changes:
        old_info = (old.get("info") or "", old.get("text") or "")
        new_info = (new.get("info") or "", new.get("text") or "")
        if old_info != new_info:
            text = new.get("info") or new.get("text") or "(entfernt)"
            changes.append(make("info", text))

    return changes


def _describe_new(period: dict) -> str:
    """Kurzbeschreibung fuer eine neu aufgetauchte Stunde."""
    parts = []
    if period.get("raum"):
        parts.append(f"Raum {_fmt_list(period['raum'])}")
    if period.get("lehrer"):
        parts.append(_fmt_list(period["lehrer"]))
    if period.get("info"):
        parts.append(period["info"])
    return " Â· ".join(parts)


# --- Plausibilitaetspruefung ------------------------------------------------


def check_plausible(
    old: list[dict],
    new: list[dict],
    max_vanish_ratio: float = 0.7,
) -> None:
    """Prueft, ob die neuen Daten ueberhaupt glaubwuerdig sind.

    Hintergrund: Wenn WebUntis wegen Wartung, Session-Problemen oder einer
    API-Aenderung eine leere oder stark reduzierte Antwort liefert, wuerde
    ein naiver Diff "alles entfaellt" melden. Genau das macht Bots
    unbrauchbar -- deshalb hier eine harte Bremse.
    """
    if not old:
        return  # Erstlauf, nichts zu vergleichen

    if not new:
        raise SuspiciousDataError(
            f"WebUntis lieferte 0 Stunden, gespeichert waren {len(old)}.\n"
            "  Wahrscheinlich Wartung, Ferien oder ein Session-Problem -- "
            "keine echte Stundenplanaenderung.\n"
            "  Der gespeicherte Zustand bleibt unveraendert."
        )

    old_alive = [p for p in old if p.get("status") != "cancelled"]
    if not old_alive:
        return

    _pairs, only_old, _only_new = _match(old, new)
    new_by_key = {p.get("key") for p in new}

    vanished = [
        p for p in only_old
        if p.get("status") != "cancelled" and p.get("key") not in new_by_key
    ]
    ratio = len(vanished) / len(old_alive)

    if ratio > max_vanish_ratio:
        raise SuspiciousDataError(
            f"{len(vanished)} von {len(old_alive)} Stunden ({ratio:.0%}) sind auf "
            "einmal verschwunden.\n"
            "  Das sieht nach einem Datenproblem aus, nicht nach echten Aenderungen.\n"
            "  Der gespeicherte Zustand bleibt unveraendert."
        )


# --- Hauptfunktion ----------------------------------------------------------


def diff_timetables(
    old: list[dict],
    new: list[dict],
    window: tuple[dt.date, dt.date] | None = None,
) -> list[Change]:
    """Vergleicht zwei Stundenplaene und liefert die Aenderungen.

    Wenn ein Zeitfenster angegeben ist, wird der alte Zustand vorher darauf
    beschnitten -- ohne das entstehen bei jedem Tageswechsel Falschmeldungen.
    """
    if window:
        start, end = window
        old = prune(old, start, end)
        new = prune(new, start, end)

    pairs, only_old, only_new = _match(old, new)

    changes: list[Change] = []

    for old_p, new_p in pairs:
        changes.extend(_compare_pair(old_p, new_p))

    for period in only_new:
        # Eine neue Stunde, die sofort als abgesagt eingetragen wird,
        # ist ein Entfall -- nicht "neue Stunde".
        kind = "cancelled" if period.get("status") == "cancelled" else "added"
        changes.append(Change(
            kind=kind,
            datum=period.get("datum", ""),
            start=period.get("start", ""),
            ende=period.get("ende", ""),
            titel=period.get("titel") or "Stunde",
            detail=_describe_new(period),
            period=period,
        ))

    for period in only_old:
        # Bereits abgesagte Stunden, die aus dem Plan genommen werden,
        # sind keine Neuigkeit -- der Ausfall wurde schon gemeldet.
        if period.get("status") == "cancelled":
            continue
        changes.append(Change(
            kind="removed",
            datum=period.get("datum", ""),
            start=period.get("start", ""),
            ende=period.get("ende", ""),
            titel=period.get("titel") or "Stunde",
            detail="",
            period=period,
        ))

    changes.sort(key=lambda c: c.sort_key)
    log.info("Diff: %d Aenderung(en) aus %d alten und %d neuen Stunden",
             len(changes), len(old), len(new))
    return changes

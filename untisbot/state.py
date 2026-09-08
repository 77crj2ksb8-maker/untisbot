"""Speichern und Laden des zuletzt bekannten Stundenplans.

Bewusst eine einfache JSON-Datei: mit einem Editor lesbar, trivial zu
sichern, und bei einem Fehlalarm kann man von Hand nachsehen, was der Bot
vorher gesehen hat. SQLite lohnt sich erst mit mehreren Nutzern.

Wichtigste Eigenschaft: das Schreiben ist **atomar**. Ein Absturz mitten
im Speichern darf die Datei nicht halb beschrieben zuruecklassen -- sonst
meldet der naechste Lauf den halben Stundenplan als "neu".
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

#: Erhoeht sich, wenn sich das Format der gespeicherten Stunden aendert.
#: Bei Abweichung wird der alte Zustand verworfen statt falsch interpretiert.
SCHEMA_VERSION = 1


def load(path: Path | None = None) -> dict:
    """Laedt den gespeicherten Zustand.

    Gibt ein Dict mit 'periods' und Metadaten zurueck. Bei fehlender oder
    kaputter Datei kommt ein leerer Zustand -- der Aufrufer erkennt den
    Erstlauf daran, dass 'periods' leer und 'exists' False ist.
    """
    path = path or config.STATE_FILE

    if not path.exists():
        log.info("Kein gespeicherter Zustand (%s) -- das ist der erste Lauf", path.name)
        return _empty()

    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        # Kaputte Datei nicht loeschen, sondern beiseitelegen: sie ist die
        # einzige Spur, falls spaeter etwas nachvollzogen werden muss.
        backup = path.with_suffix(".broken")
        try:
            path.replace(backup)
            log.warning("Zustandsdatei unlesbar (%s), verschoben nach %s",
                        exc, backup.name)
        except OSError:
            log.warning("Zustandsdatei unlesbar (%s)", exc)
        return _empty()

    if data.get("schema") != SCHEMA_VERSION:
        log.warning("Zustand hat Schema %s, erwartet %s -- wird verworfen",
                    data.get("schema"), SCHEMA_VERSION)
        return _empty()

    periods = data.get("periods") or []
    log.info("Zustand geladen: %d Stunden vom %s",
             len(periods), data.get("gespeichert_am", "?"))

    return {
        "exists": True,
        "schema": SCHEMA_VERSION,
        "periods": periods,
        "gespeichert_am": data.get("gespeichert_am"),
        "fenster": data.get("fenster") or {},
    }


def save(
    periods: list[dict],
    start: dt.date,
    end: dt.date,
    path: Path | None = None,
) -> None:
    """Speichert den Zustand atomar.

    Erst in eine temporaere Datei im selben Verzeichnis schreiben, dann
    per os.replace() umbenennen -- das ist auf allen gaengigen Systemen
    eine atomare Operation.
    """
    path = path or config.STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema": SCHEMA_VERSION,
        "gespeichert_am": dt.datetime.now().isoformat(timespec="seconds"),
        "fenster": {"von": f"{start:%Y-%m-%d}", "bis": f"{end:%Y-%m-%d}"},
        "periods": periods,
    }

    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        # Temporaere Datei nicht liegen lassen.
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise

    log.info("Zustand gespeichert: %d Stunden (%s bis %s)", len(periods), start, end)


def _empty() -> dict:
    return {
        "exists": False,
        "schema": SCHEMA_VERSION,
        "periods": [],
        "gespeichert_am": None,
        "fenster": {},
    }

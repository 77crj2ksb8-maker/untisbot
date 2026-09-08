"""WebUntis-Zugriff.

Die WebUntis-API ist inoffiziell und je nach Schule unterschiedlich
freigeschaltet. Dieses Modul ist deshalb bewusst defensiv gebaut:

  * Jeder Zugriff auf ein Feld hat einen Fallback auf die Rohdaten.
  * Fehlende Berechtigungen fuehren nicht zum Absturz, sondern zu leeren Feldern.
  * Der Abruf probiert mehrere Wege durch (my_timetable -> Klasse).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import logging
from typing import Any, Iterator

import webuntis

from . import config

log = logging.getLogger(__name__)


class UntisError(RuntimeError):
    """Fehler beim Zugriff auf WebUntis."""


# --- Session ----------------------------------------------------------------


@contextlib.contextmanager
def open_session() -> Iterator[webuntis.Session]:
    """Oeffnet eine WebUntis-Session und schliesst sie garantiert wieder.

    Verwendung:
        with open_session() as s:
            perioden = fetch_periods(s, start, end)
    """
    config.require_webuntis()

    session = webuntis.Session(
        server=config.WEBUNTIS_SERVER,
        school=config.WEBUNTIS_SCHOOL,
        username=config.WEBUNTIS_USERNAME,
        password=config.WEBUNTIS_PASSWORD,
        useragent=config.USER_AGENT,
    )

    try:
        session.login()
    except webuntis.errors.BadCredentialsError as exc:
        raise UntisError(
            "Login abgelehnt: Benutzername oder Passwort stimmt nicht.\n"
            "  Pruefe WEBUNTIS_USERNAME und WEBUNTIS_PASSWORD in der .env.\n"
            "  Achtung: Das ist der WebUntis-Login, nicht der Schul-Mail-Login."
        ) from exc
    except webuntis.errors.AuthError as exc:
        raise UntisError(
            f"Anmeldung fehlgeschlagen: {exc}\n"
            "  Haeufigste Ursache: falsches Schulkuerzel (WEBUNTIS_SCHOOL)\n"
            "  oder falscher Server (WEBUNTIS_SERVER)."
        ) from exc
    except Exception as exc:  # Netzwerk, DNS, HTML-Antwort statt JSON ...
        raise UntisError(
            f"Verbindung zu {config.WEBUNTIS_SERVER} fehlgeschlagen: {exc}\n"
            "  Pruefe WEBUNTIS_SERVER (nur der Host, ohne https:// und ohne Pfad)."
        ) from exc

    log.info("Bei WebUntis angemeldet (%s / %s)",
             config.WEBUNTIS_SERVER, config.WEBUNTIS_SCHOOL)

    try:
        yield session
    finally:
        # Logout darf den Lauf nie zum Scheitern bringen.
        with contextlib.suppress(Exception):
            session.logout(suppress_errors=True)
            log.debug("WebUntis-Session geschlossen")


# --- Robuste Feld-Zugriffe --------------------------------------------------
#
# Warum das noetig ist: period.teachers ruft intern session.teachers() auf.
# Viele Schueler-Accounts duerfen die Lehrerliste aber nicht abrufen -- dann
# fliegt eine Exception, obwohl der Name in den Rohdaten der Stunde steht.


def _raw(period: Any) -> dict:
    """Die unveraenderten JSON-Daten der Stunde."""
    return getattr(period, "_data", {}) or {}


def _names_from_raw(period: Any, key: str, original: bool = False) -> list[str]:
    """Liest Namen direkt aus den Rohdaten (te/ro/su/kl).

    original=True liefert die urspruenglichen Werte (orgname), die WebUntis
    bei Vertretungen und Raumwechseln mitliefert.
    """
    entries = _raw(period).get(key) or []
    field = "orgname" if original else "name"
    names = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get(field) or (entry.get("longname") if not original else None)
        if value:
            names.append(str(value))
    return names


def _names(period: Any, attr: str, raw_key: str) -> list[str]:
    """Holt Namen ueber die Library, faellt bei fehlenden Rechten auf Rohdaten zurueck."""
    try:
        objects = getattr(period, attr)
        names = [str(getattr(o, "name", "") or "").strip() for o in objects]
        names = [n for n in names if n]
        if names:
            return names
    except Exception as exc:  # fehlende Berechtigung, leere Liste, KeyError ...
        log.debug("Zugriff auf %s nicht moeglich (%s) -- nutze Rohdaten", attr, exc)
    return _names_from_raw(period, raw_key)


def _original_names(period: Any, attr: str, raw_key: str) -> list[str]:
    """Die urspruenglichen Werte vor einer Vertretung/Verlegung."""
    try:
        objects = getattr(period, attr)
        names = [str(getattr(o, "name", "") or "").strip() for o in objects]
        names = [n for n in names if n]
        if names:
            return names
    except Exception:
        pass
    return _names_from_raw(period, raw_key, original=True)


# --- Normalisierung ---------------------------------------------------------


def period_to_dict(period: Any) -> dict:
    """Wandelt eine WebUntis-Stunde in ein flaches, vergleichbares Dict.

    Das ist die Grundlage fuer die Diff-Logik in Schritt 3: alles ist
    sortiert, JSON-serialisierbar und frei von None-Werten.
    """
    raw = _raw(period)

    start: dt.datetime = period.start
    end: dt.datetime = period.end

    code = getattr(period, "code", None)
    status = code if code in ("cancelled", "irregular") else "regular"

    subjects = sorted(_names(period, "subjects", "su"))
    teachers = sorted(_names(period, "teachers", "te"))
    rooms = sorted(_names(period, "rooms", "ro"))
    klassen = sorted(_names_from_raw(period, "kl"))

    # Kursgruppe ("sg") und Unterrichtsnummer ("lsnumber") sind stabile
    # Anker. Sie retten die Identifikation, wenn "su" leer ist -- was bei
    # Sonderterminen (Klausuren, Exkursionen) regelmaessig vorkommt.
    group = str(raw.get("sg") or "").strip()
    lsnumber = raw.get("lsnumber")

    info_text = str(raw.get("substText") or "").strip()
    lesson_text = str(raw.get("lstext") or "").strip()

    # Anzeigename in absteigender Aussagekraft.
    # Wichtig fuer diese Schule: bei Sonderterminen (Klausuren, Exkursionen)
    # ist "su" leer, aber "lstext" enthaelt z. B. "D-KA" (Deutsch-Klausur).
    label = (
        "+".join(subjects)
        or lesson_text
        or info_text
        or group
        or (f"ls{lsnumber}" if lsnumber else "?")
    )

    data = {
        # Identitaet
        "id": raw.get("id"),
        "lsnumber": lsnumber,
        "key": f"{start:%Y-%m-%d}|{start:%H:%M}|{label}",
        # Zeit
        "datum": f"{start:%Y-%m-%d}",
        "start": f"{start:%H:%M}",
        "ende": f"{end:%H:%M}",
        # Inhalt
        "fach": subjects,
        "lehrer": teachers,
        "raum": rooms,
        "klasse": klassen,
        "gruppe": group,
        "art": str(raw.get("activityType") or "").strip(),
        #: Anzeigename fuer Nachrichten -- nie leer
        "titel": label,
        # Status
        "status": status,
        # Was WebUntis selbst als "vorher" meldet -- Gold wert fuer Schritt 3
        "orig_lehrer": sorted(_original_names(period, "original_teachers", "te")),
        "orig_raum": sorted(_original_names(period, "original_rooms", "ro")),
        "orig_fach": sorted(_original_names(period, "original_subjects", "su")),
        # Freitexte
        "info": info_text,
        "text": lesson_text,
    }
    return data


# --- Abruf ------------------------------------------------------------------


class NoSchoolYearError(UntisError):
    """Das angefragte Zeitfenster liegt in keinem eingetragenen Schuljahr.

    Normalfall in den Sommerferien: Die Schule hat das neue Schuljahr noch
    nicht in WebUntis angelegt. Kein Fehler, sondern ein Zustand -- der Bot
    muss ihn ruhig aussitzen und darf nicht "alles entfaellt" melden.
    """


def schoolyears(session: webuntis.Session) -> list[Any]:
    """Alle in WebUntis eingetragenen Schuljahre (aeltestes zuerst)."""
    try:
        years = list(session.schoolyears())
    except Exception as exc:
        log.debug("Schuljahre nicht abrufbar: %s", exc)
        return []
    return sorted(years, key=lambda y: y.start)


def find_schoolyear(session: webuntis.Session, day: dt.date) -> Any | None:
    """Das Schuljahr, in das ein bestimmtes Datum faellt -- oder None."""
    for year in schoolyears(session):
        if year.start.date() <= day <= year.end.date():
            return year
    return None


def clamp_to_schoolyear(
    session: webuntis.Session,
    start: dt.date,
    end: dt.date,
) -> tuple[dt.date, dt.date]:
    """Beschneidet das Zeitfenster auf ein einzelnes Schuljahr.

    WebUntis lehnt Abfragen ab, die eine Schuljahresgrenze ueberschreiten
    oder ganz ausserhalb liegen. Diese Funktion faengt beides ab.
    """
    years = schoolyears(session)
    if not years:
        return start, end  # keine Info -- unveraendert weiterreichen

    year = find_schoolyear(session, start) or find_schoolyear(session, end)

    if year is None:
        upcoming = [y for y in years if y.start.date() > end]
        last = years[-1]
        if upcoming:
            hint = (
                f"Das naechste Schuljahr ({upcoming[0].name}) beginnt am "
                f"{upcoming[0].start:%d.%m.%Y}."
            )
        else:
            hint = (
                f"Das letzte eingetragene Schuljahr ({last.name}) endete am "
                f"{last.end:%d.%m.%Y}. Das neue ist in WebUntis noch nicht angelegt "
                "-- das passiert bei vielen Schulen erst kurz vor Schulbeginn."
            )
        raise NoSchoolYearError(
            f"Kein Schuljahr deckt den Zeitraum {start:%d.%m.%Y}-{end:%d.%m.%Y} ab.\n"
            f"  {hint}"
        )

    new_start = max(start, year.start.date())
    new_end = min(end, year.end.date())
    if (new_start, new_end) != (start, end):
        log.info("Zeitfenster auf Schuljahr %s beschnitten: %s bis %s",
                 year.name, new_start, new_end)
    return new_start, new_end


def fetch_periods(
    session: webuntis.Session,
    start: dt.date,
    end: dt.date,
) -> tuple[list[Any], str]:
    """Holt den Stundenplan und meldet, welcher Weg funktioniert hat.

    Rueckgabe: (liste_von_perioden, verwendete_methode)

    Probiert der Reihe nach:
      1. my_timetable  -- der persoenliche Plan des Accounts (Schueler/Eltern)
      2. timetable(klasse=...) -- Klassenplan als Fallback
    """
    errors: list[str] = []

    # Vorab: liegt das Fenster ueberhaupt in einem Schuljahr?
    # Spart einen sinnlosen Request und liefert eine verstaendliche Meldung.
    start, end = clamp_to_schoolyear(session, start, end)

    # Weg 1: persoenlicher Plan
    try:
        periods = list(session.my_timetable(start=start, end=end))
        if periods:
            log.info("Abruf ueber my_timetable: %d Stunden", len(periods))
            return periods, "my_timetable"
        errors.append("my_timetable lieferte 0 Stunden")
    except Exception as exc:
        errors.append(f"my_timetable: {exc}")
        log.debug("my_timetable nicht nutzbar: %s", exc)

    # Weg 2: Klassenplan
    klasse_name = config.WEBUNTIS_KLASSE
    if klasse_name:
        try:
            klassen = session.klassen()
            match = next(
                (k for k in klassen if str(k.name).lower() == klasse_name.lower()),
                None,
            )
            if match is None:
                available = ", ".join(sorted(str(k.name) for k in klassen)[:40])
                raise UntisError(
                    f"Klasse {klasse_name!r} nicht gefunden.\n"
                    f"  Verfuegbar: {available}"
                )
            periods = list(session.timetable(klasse=match, start=start, end=end))
            log.info("Abruf ueber Klassenplan %s: %d Stunden", match.name, len(periods))
            return periods, f"klasse:{match.name}"
        except UntisError:
            raise
        except Exception as exc:
            errors.append(f"timetable(klasse={klasse_name}): {exc}")

    raise UntisError(
        "Kein Stundenplan abrufbar. Versuchte Wege:\n  - "
        + "\n  - ".join(errors)
        + "\n\nMoegliche Loesung: WEBUNTIS_KLASSE in der .env setzen "
        "(z. B. WEBUNTIS_KLASSE=10a), falls dein Account keinen "
        "persoenlichen Stundenplan hat."
    )


def fetch_timetable(start: dt.date, end: dt.date) -> list[dict]:
    """Bequemer Komplettabruf: Login, Abruf, Normalisierung, Logout."""
    with open_session() as session:
        periods, _method = fetch_periods(session, start, end)
        result = []
        for period in periods:
            try:
                result.append(period_to_dict(period))
            except Exception as exc:
                # Eine kaputte Stunde darf nie den ganzen Lauf killen.
                log.warning("Stunde uebersprungen (%s): %s", exc, _raw(period))
        return sorted(result, key=lambda p: (p["datum"], p["start"], p["key"]))


def date_window(days: int | None = None) -> tuple[dt.date, dt.date]:
    """Zeitfenster ab heute. Standard: LOOKAHEAD_DAYS aus der Konfiguration."""
    days = days if days is not None else config.LOOKAHEAD_DAYS
    today = dt.date.today()
    return today, today + dt.timedelta(days=days)

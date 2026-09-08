"""Der Gesamtablauf -- das ist das Skript, das spaeter per Cron laeuft.

    python -m untisbot.main

Ablauf:
    1. Stundenplan abrufen
    2. Gespeicherten Vorzustand laden
    3. Plausibilitaet pruefen  (Bremse gegen Massen-Fehlalarme)
    4. Diff bilden
    5. Nachricht senden
    6. Zustand speichern -- erst NACH erfolgreichem Versand

Punkt 6 ist wichtig: Wenn Telegram gerade nicht erreichbar ist, darf die
Aenderung nicht verloren gehen. Lieber beim naechsten Lauf erneut melden.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from . import config, diff, messages, notify, state, untis_client

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_SCHOOLYEAR = 3


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prueft WebUntis auf Stundenplanaenderungen und meldet sie."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Alles ausfuehren, aber nichts senden und nichts speichern")
    parser.add_argument("--days", type=int, default=None,
                        help="Zeitfenster in Tagen (Standard: LOOKAHEAD_DAYS)")
    parser.add_argument("--force-send", action="store_true",
                        help="Auch beim ersten Lauf senden (sonst wird nur gespeichert)")
    parser.add_argument("--summary", action="store_true",
                        help="Statt des Diffs den kompletten Plan senden")
    parser.add_argument("--quiet-hours", default="22-6",
                        help="Zeitraum ohne Ton, Format 'von-bis' (Standard 22-6, "
                             "'aus' zum Abschalten)")
    return parser.parse_args(argv)


def _is_quiet(spec: str, now: dt.datetime) -> bool:
    """Faellt die aktuelle Uhrzeit in die Nachtruhe?"""
    if not spec or spec.lower() in ("aus", "off", "none"):
        return False
    try:
        start_h, end_h = (int(x) for x in spec.split("-", 1))
    except ValueError:
        log.warning("--quiet-hours %r nicht verstanden, ignoriert", spec)
        return False
    hour = now.hour
    if start_h <= end_h:
        return start_h <= hour < end_h
    return hour >= start_h or hour < end_h  # ueber Mitternacht


def run(args: argparse.Namespace) -> int:
    start, end = untis_client.date_window(args.days)
    now = dt.datetime.now()
    silent = _is_quiet(args.quiet_hours, now)

    # --- 1. Abruf ---------------------------------------------------------
    try:
        current = untis_client.fetch_timetable(start, end)
    except untis_client.NoSchoolYearError as exc:
        # Kein Fehler, sondern ein Zustand (Ferien). Ruhig aussitzen.
        log.info("Kein aktives Schuljahr: %s", str(exc).splitlines()[0])
        print(f"Nichts zu tun: {exc}")
        return EXIT_NO_SCHOOLYEAR
    except untis_client.UntisError as exc:
        log.error("Abruf fehlgeschlagen: %s", exc)
        print(f"FEHLER: {exc}", file=sys.stderr)
        return EXIT_ERROR

    log.info("Abgerufen: %d Stunden fuer %s bis %s", len(current), start, end)

    # --- 2. Vorzustand ----------------------------------------------------
    previous = state.load()
    is_first_run = not previous["exists"]

    # --- 3. Plausibilitaet ------------------------------------------------
    try:
        diff.check_plausible(previous["periods"], current)
    except diff.SuspiciousDataError as exc:
        log.warning("Daten unplausibel: %s", exc)
        print(f"ABBRUCH: {exc}", file=sys.stderr)
        return EXIT_ERROR  # Zustand bleibt bewusst unveraendert

    # --- 4. Diff ----------------------------------------------------------
    if args.summary:
        text = messages.format_summary(current, today=now.date())
        changes = []
    else:
        changes = diff.diff_timetables(
            previous["periods"], current, window=(start, end)
        )
        text = messages.format_changes(changes, today=now.date())

    # --- Erstlauf: nur speichern, nicht fluten ------------------------------
    if is_first_run and not args.force_send and not args.summary:
        print(f"Erster Lauf: {len(current)} Stunden gespeichert, nichts gesendet.")
        print("Ab dem naechsten Lauf werden Aenderungen gemeldet.")
        if not args.dry_run:
            state.save(current, start, end)
        return EXIT_OK

    # --- 5. Versand -------------------------------------------------------
    if not text:
        print(f"Keine Aenderungen ({len(current)} Stunden geprueft).")
        if not args.dry_run:
            state.save(current, start, end)
        return EXIT_OK

    if args.dry_run:
        print("--- Nachricht (dry-run, nicht gesendet) ---")
        print(text)
        print("--- Zustand wird NICHT gespeichert ---")
        return EXIT_OK

    try:
        notify.send_message(text, silent=silent)
    except (notify.TelegramError, config.ConfigError) as exc:
        # Zustand NICHT speichern -- sonst ist die Aenderung fuer immer weg.
        log.error("Versand fehlgeschlagen: %s", exc)
        print(f"FEHLER beim Senden: {exc}", file=sys.stderr)
        print("Zustand wurde nicht gespeichert -- naechster Lauf versucht es erneut.",
              file=sys.stderr)
        return EXIT_ERROR

    # --- 6. Zustand sichern -----------------------------------------------
    state.save(current, start, end)

    count = len(changes) if changes else 1
    print(f"{count} Aenderung(en) gemeldet.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config.setup_logging()
    try:
        return run(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # letzte Sicherung -- Cron soll nie stumm sterben
        log.exception("Unerwarteter Fehler")
        print(f"UNERWARTETER FEHLER: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())

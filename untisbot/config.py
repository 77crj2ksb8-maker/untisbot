"""Zentrale Konfiguration.

Alle Zugangsdaten kommen aus der .env-Datei -- nichts wird hier hartcodiert.
Dieses Modul ist die EINZIGE Stelle im Projekt, die os.environ liest.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Projektwurzel = ein Verzeichnis ueber diesem Paket
BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(ENV_PATH)


class ConfigError(RuntimeError):
    """Wird geworfen, wenn eine noetige Einstellung fehlt oder unplausibel ist."""


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or None


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} muss eine ganze Zahl sein, ist aber {raw!r}") from exc


# --- Telegram ---------------------------------------------------------------

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")

#: Liste von Chat-IDs. Mehrere Empfaenger werden per Komma getrennt.
TELEGRAM_CHAT_IDS: list[str] = [
    part.strip() for part in (_get("TELEGRAM_CHAT_ID") or "").split(",") if part.strip()
]


# --- WebUntis (ab Schritt 2 relevant) ---------------------------------------

WEBUNTIS_SERVER = _get("WEBUNTIS_SERVER")
WEBUNTIS_SCHOOL = _get("WEBUNTIS_SCHOOL")
WEBUNTIS_USERNAME = _get("WEBUNTIS_USERNAME")
WEBUNTIS_PASSWORD = _get("WEBUNTIS_PASSWORD")

#: Optional. Nur noetig, wenn der Account keinen persoenlichen Stundenplan hat
#: und stattdessen ein Klassenplan abgerufen werden muss (z. B. "10a").
WEBUNTIS_KLASSE = _get("WEBUNTIS_KLASSE")


# --- Verhalten --------------------------------------------------------------

LOOKAHEAD_DAYS = _get_int("LOOKAHEAD_DAYS", 7)
TIMEZONE = _get("TIMEZONE", "Europe/Berlin")
LOG_LEVEL = (_get("LOG_LEVEL", "INFO") or "INFO").upper()

#: Ein aussagekraeftiger User-Agent ist bei WebUntis Pflicht und hoeflich.
USER_AGENT = "untisbot/0.1 (privates Stundenplan-Tool)"

STATE_FILE = BASE_DIR / "state.json"


# --- Validierung ------------------------------------------------------------

def require_telegram() -> tuple[str, list[str]]:
    """Prueft die Telegram-Einstellungen und gibt (token, chat_ids) zurueck.

    Wirft ConfigError mit einem Hinweis, was zu tun ist -- statt spaeter
    an einer unverstaendlichen Stelle mit "NoneType" zu scheitern.
    """
    if not TELEGRAM_BOT_TOKEN:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN fehlt.\n"
            f"  -> Lege {ENV_PATH.name} an (cp .env.example .env) und trage den\n"
            "     Token von @BotFather ein."
        )
    if ":" not in TELEGRAM_BOT_TOKEN:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN sieht falsch aus -- erwartet wird das Format\n"
            "  123456789:AAH-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        )
    if not TELEGRAM_CHAT_IDS:
        raise ConfigError(
            "TELEGRAM_CHAT_ID fehlt.\n"
            "  -> Schreibe deinem Bot in Telegram einmal /start und fuehre dann aus:\n"
            "     python -m untisbot.get_chat_id"
        )
    return TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_IDS


def require_webuntis() -> None:
    """Prueft die WebUntis-Einstellungen (wird erst ab Schritt 2 gebraucht)."""
    missing = [
        name
        for name, value in (
            ("WEBUNTIS_SERVER", WEBUNTIS_SERVER),
            ("WEBUNTIS_SCHOOL", WEBUNTIS_SCHOOL),
            ("WEBUNTIS_USERNAME", WEBUNTIS_USERNAME),
            ("WEBUNTIS_PASSWORD", WEBUNTIS_PASSWORD),
        )
        if not value
    ]
    if missing:
        raise ConfigError("Fehlende WebUntis-Einstellungen in .env: " + ", ".join(missing))


def setup_logging() -> None:
    """Einheitliches Logging fuer alle Skripte."""
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )
    # Die Netzwerk-Logs von requests/urllib3 sind sehr geschwaetzig.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    # Die webuntis-Bibliothek loggt jede API-Fehlerantwort selbst als ERROR --
    # auch die, die wir bewusst abfangen (fehlende Rechte, Ferienzeitraeume).
    # Wir behandeln diese Faelle sauber, also unterdruecken wir das Rauschen.
    if LOG_LEVEL != "DEBUG":
        logging.getLogger("webuntis").setLevel(logging.CRITICAL)


def mask(secret: str | None) -> str:
    """Kuerzt ein Geheimnis fuer die Log-Ausgabe: 12345678:AAH... -> 1234...xxxx"""
    if not secret:
        return "<nicht gesetzt>"
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"

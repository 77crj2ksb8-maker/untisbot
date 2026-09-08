"""Telegram-Versand.

Bewusst ohne python-telegram-bot: fuer reines Senden reicht ein HTTP-POST.
Das haelt die Abhaengigkeiten klein und das Deployment (Raspberry Pi, Cron)
schmerzfrei. Diese Datei ist die einzige Stelle, die mit Telegram spricht.
"""

from __future__ import annotations

import html
import logging
import time

import requests

from . import config

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"

#: Telegram bricht Nachrichten ueber 4096 Zeichen ab.
MAX_MESSAGE_LENGTH = 4096

#: Kleiner Sicherheitsabstand beim Splitten.
SPLIT_LENGTH = 3900

REQUEST_TIMEOUT = 20


class TelegramError(RuntimeError):
    """Fehler beim Sprechen mit der Telegram-API."""


def _api_url(token: str, method: str) -> str:
    return f"{API_BASE}/bot{token}/{method}"


def _call(token: str, method: str, payload: dict | None = None) -> dict:
    """Ruft eine Telegram-API-Methode auf und gibt das 'result'-Feld zurueck."""
    url = _api_url(token, method)
    try:
        response = requests.post(url, json=payload or {}, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise TelegramError(f"Netzwerkfehler bei {method}: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise TelegramError(
            f"Unerwartete Antwort von Telegram bei {method} "
            f"(HTTP {response.status_code}): {response.text[:200]}"
        ) from exc

    if not data.get("ok"):
        code = data.get("error_code")
        description = data.get("description", "keine Beschreibung")
        raise TelegramError(_explain_error(code, description, method))

    return data.get("result", {})


def _explain_error(code: int | None, description: str, method: str) -> str:
    """Uebersetzt die haeufigsten Telegram-Fehler in verstaendliche Hinweise."""
    hints = {
        401: (
            "Der Token wird abgelehnt. Pruefe TELEGRAM_BOT_TOKEN in .env -- "
            "am haeufigsten ist ein kopierter Leerschritt oder ein widerrufener Token."
        ),
        400: (
            "Telegram lehnt die Anfrage ab. Bei 'chat not found': Du musst dem Bot "
            "zuerst selbst /start schreiben, vorher darf er dir nicht antworten. "
            "Bei 'can't parse entities': Ein HTML-Tag in der Nachricht ist kaputt."
        ),
        403: (
            "Der Bot darf diesem Chat nicht schreiben -- du hast ihn evtl. blockiert "
            "oder aus der Gruppe entfernt."
        ),
        429: "Zu viele Anfragen. Telegram drosselt -- kurz warten und erneut versuchen.",
    }
    hint = hints.get(code or 0, "")
    message = f"Telegram-Fehler bei {method} (code {code}): {description}"
    return f"{message}\n  Hinweis: {hint}" if hint else message


# --- oeffentliche Funktionen ------------------------------------------------


def get_me(token: str | None = None) -> dict:
    """Prueft den Token und gibt die Bot-Infos zurueck (Name, Username, ID)."""
    token = token or config.TELEGRAM_BOT_TOKEN
    if not token:
        raise config.ConfigError("Kein TELEGRAM_BOT_TOKEN gesetzt.")
    return _call(token, "getMe")


def get_updates(token: str | None = None, timeout: int = 0) -> list[dict]:
    """Holt die zuletzt an den Bot gesendeten Nachrichten.

    Wird von get_chat_id.py genutzt, um die Chat-ID zu ermitteln.
    Achtung: Telegram haelt Updates nur ca. 24 Stunden vor.
    """
    token = token or config.TELEGRAM_BOT_TOKEN
    if not token:
        raise config.ConfigError("Kein TELEGRAM_BOT_TOKEN gesetzt.")
    result = _call(token, "getUpdates", {"timeout": timeout})
    return result if isinstance(result, list) else []


def escape(text: str) -> str:
    """Macht beliebigen Text sicher fuer parse_mode=HTML.

    Noetig, weil Lehrer- oder Raumnamen theoretisch <, > oder & enthalten koennen.
    """
    return html.escape(str(text), quote=False)


def split_message(text: str, limit: int = SPLIT_LENGTH) -> list[str]:
    """Teilt lange Nachrichten an Zeilengrenzen auf statt mitten im Wort."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in text.split("\n"):
        # Einzelne ueberlange Zeile: hart schneiden.
        while len(line) > limit:
            if current:
                parts.append("\n".join(current))
                current, current_len = [], 0
            parts.append(line[:limit])
            line = line[limit:]

        if current_len + len(line) + 1 > limit:
            parts.append("\n".join(current))
            current, current_len = [], 0

        current.append(line)
        current_len += len(line) + 1

    if current:
        parts.append("\n".join(current))

    return [p for p in parts if p.strip()]


def send_message(
    text: str,
    chat_ids: list[str] | None = None,
    token: str | None = None,
    silent: bool = False,
    dry_run: bool = False,
) -> int:
    """Schickt eine Nachricht an alle konfigurierten Chats.

    Gibt die Anzahl erfolgreich versendeter Nachrichten zurueck.
    Bei dry_run wird nur geloggt, nichts gesendet.
    """
    if token is None or chat_ids is None:
        cfg_token, cfg_chats = config.require_telegram()
        token = token or cfg_token
        chat_ids = chat_ids or cfg_chats

    chunks = split_message(text)
    sent = 0

    for chat_id in chat_ids:
        for index, chunk in enumerate(chunks):
            if dry_run:
                log.info("[dry-run] an %s (Teil %d/%d):\n%s",
                         chat_id, index + 1, len(chunks), chunk)
                sent += 1
                continue

            _call(
                token,
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_notification": silent,
                    "link_preview_options": {"is_disabled": True},
                },
            )
            sent += 1
            log.debug("Nachricht an %s gesendet (Teil %d/%d)",
                      chat_id, index + 1, len(chunks))

            # Telegram erlaubt ca. 30 Nachrichten/Sekunde -- bei mehreren
            # Teilen kurz durchatmen, damit wir nie in die Drosselung laufen.
            if len(chunks) > 1:
                time.sleep(0.4)

    return sent

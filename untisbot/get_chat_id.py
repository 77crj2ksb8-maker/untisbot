"""Ermittelt die Telegram-Chat-ID fuer den konfigurierten Bot.

    python -m untisbot.get_chat_id

Voraussetzung: Du hast dem Bot in Telegram vorher einmal /start geschrieben
(oder ihn in eine Gruppe eingeladen und dort etwas geschrieben) -- ohne das
liefert Telegram keine Updates.
"""

from __future__ import annotations

import sys

from . import config, notify


def _describe_chat(chat: dict) -> str:
    kind = chat.get("type", "?")
    name = chat.get("title") or chat.get("username") or chat.get("first_name") or "?"
    return f"{chat.get('id')}  ({kind}: {name})"


def main(argv: list[str] | None = None) -> int:
    config.setup_logging()

    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        print("FEHLER: TELEGRAM_BOT_TOKEN fehlt in .env.", file=sys.stderr)
        return 1

    try:
        me = notify.get_me(token)
    except notify.TelegramError as exc:
        print(f"FEHLER: Token wird abgelehnt: {exc}", file=sys.stderr)
        return 1

    print(f"Bot: @{me.get('username')} ({me.get('first_name')})")

    try:
        updates = notify.get_updates(token)
    except notify.TelegramError as exc:
        print(f"FEHLER beim Abrufen der Updates: {exc}", file=sys.stderr)
        return 1

    chats: dict[int, dict] = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post")
        if not message:
            continue
        chat = message.get("chat")
        if chat and "id" in chat:
            chats[chat["id"]] = chat

    if not chats:
        print(
            "\nKeine Chats gefunden.\n"
            "  -> Schreibe dem Bot in Telegram einmal /start (oder etwas "
            "Beliebiges) und fuehre dieses Kommando dann erneut aus.\n"
            "  -> Telegram haelt Updates nur ca. 24 Stunden vor."
        )
        return 1

    print("\nGefundene Chats (TELEGRAM_CHAT_ID in .env eintragen):")
    for chat in chats.values():
        print(f"  {_describe_chat(chat)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

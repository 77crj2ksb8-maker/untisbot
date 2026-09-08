---
description: Walk through connecting Telegram and WebUntis for untisbot, and write .env / GitHub secrets
---

You are helping the user connect the two external apps that `untisbot` needs
before it can run: **Telegram** (to send messages) and **WebUntis** (to read
the timetable). Follow this repo's existing conventions (see
`untisbot/config.py` for the exact variable names it reads).

Work through the following steps interactively, one at a time. Ask before
writing any file or secret, and never print a real token/password back to
the user in full — only a masked form (reuse the `config.mask()` helper's
convention: first 4 and last 4 characters).

## 1. Check current state

- Look for a `.env` file at the repo root. If one exists, read which of the
  required variables are already set (without printing secret values) and
  tell the user what's missing.
- The required variables (see `untisbot/config.py`):
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
  - `WEBUNTIS_SERVER`, `WEBUNTIS_SCHOOL`, `WEBUNTIS_USERNAME`, `WEBUNTIS_PASSWORD`
  - `WEBUNTIS_KLASSE` (optional)
- If no `.env` exists, offer to create one from `.env.example`.

## 2. Connect Telegram

- If `TELEGRAM_BOT_TOKEN` is missing, explain: open Telegram, talk to
  `@BotFather`, run `/newbot`, and copy the token it returns
  (format `123456789:AAH-...`).
- Ask the user to paste the token. Validate it by running:
  `python -m untisbot.get_chat_id` after temporarily writing the token to
  `.env` (or by calling `untisbot.notify.get_me` directly) — this confirms
  the token is accepted before asking for anything else.
- If `TELEGRAM_CHAT_ID` is missing: tell the user to send `/start` (or any
  message) to their new bot in Telegram, then run
  `python -m untisbot.get_chat_id` to list candidate chat IDs. Let them pick
  one (or several, comma-separated for multiple recipients) and write it to
  `.env`.

## 3. Connect WebUntis

- Ask for the four required WebUntis values: server hostname (no
  `https://`, e.g. `achilles.webuntis.com`), school name as WebUntis shows
  it, username, and password. Mention `WEBUNTIS_KLASSE` is only needed if
  the account has no personal timetable and a class plan must be used
  instead.
- After writing them to `.env`, validate the connection by running:
  `python -m untisbot.main --dry-run --summary`
  This exercises the real WebUntis login without sending any Telegram
  message or touching `state.json`. If it fails, surface the error message
  (the codebase's `ConfigError`/`UntisError` messages are written to be
  actionable) and let the user fix the relevant value.

## 4. Write `.env`

- Once both apps are validated, write (or update) `.env` at the repo root
  with all collected values, preserving the structure/comments of
  `.env.example`. Never commit this file — confirm `.gitignore` excludes it.

## 5. Offer GitHub Actions secrets

The scheduled workflow (`.github/workflows/check-timetable.yml`) reads the
same variables from repository secrets, not from `.env`. Ask the user
whether they also want these set as GitHub Actions secrets so the scheduled
run works. If yes, and the `gh` CLI is available and authenticated, offer
to run (confirm each one before executing, since these are pushed to a
shared/remote system):

```
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
gh secret set WEBUNTIS_SERVER
gh secret set WEBUNTIS_SCHOOL
gh secret set WEBUNTIS_USERNAME
gh secret set WEBUNTIS_PASSWORD
gh secret set WEBUNTIS_KLASSE   # only if used
```

If `gh` isn't available, just point the user to the repo's
Settings -> Secrets and variables -> Actions page instead.

## 6. Final check

Run `python -m untisbot.main --dry-run` once more end-to-end and report the
result to the user (first run just saves state and reports "nothing sent
yet" — that's expected and not an error).

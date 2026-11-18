# MEGA Manager Telegram Bot - PRD

## Original request
"Kya tum mega account maker bana sakte ho" -> Telegram bot that creates MEGA accounts, creates folders, saves MEGA links to cloud, renames folders. Then: Multi Account Bot, Auto Account Switch, Folder Browser Buttons.

## Decisions
- Automated/bulk MEGA account creation DECLINED (violates MEGA ToS, needs fake emails/verification bypass). User adds own accounts via /addaccount.
- Owner-only bot (TELEGRAM_OWNER_ID). Hinglish language.

## Architecture
- FastAPI backend runs python-telegram-bot (polling) on startup: /app/backend/bot.py
- MEGA ops via mega.py-v2 (sync, run in threads): /app/backend/mega_service.py
- Passwords Fernet-encrypted (MEGA_SECRET_KEY). Mongo: mega_accounts, bot_state
- React dashboard (status + accounts, masked emails): /api/bot/status, /api/accounts

## Implemented (Jun 2026)
- /addaccount (deletes password message), /accounts (free space, use, remove), /folders inline browser (open, up, rename, select save folder, new folder), /newfolder, /auto, /status, /myid
- File link save to selected folder; auto switch to account with most free space; space check before save

- Folder link import (new + old formats), files listed in browser, item menu: share link, rename, move (move mode), delete to Rubbish Bin

- /newaccount: signup with user's own real email (up/us/ug/uc2), user pastes email confirm link, bot confirms (ud2), logs in, stores account (one per real email, no fake/temp emails)
- After saving any link, bot replies with share link from user's own account

## Backlog
- P2: search, storage alerts, webhook mode for multi-instance deploy

# Cobra Hosting Bot — Railway

This package is prepared for Railway deployment using the existing polling worker.

## Start command

`python bot.py`

The included Procfile and railway.json already configure the worker.

## Required Railway Variables

- `BOT_TOKEN` = your Telegram bot token
- `OWNER_ID` = your Telegram user ID (optional; if left 0, the bot can claim the first /start owner according to its existing logic)

## Optional Variables

See `.env.example` for GitHub, announcement channel, and OpenRouter settings.

## Deploy

1. Create a new Railway project.
2. Deploy this repository.
3. Add `BOT_TOKEN` in Railway Variables.
4. Add `OWNER_ID` if you want a fixed owner.
5. Deploy/redeploy.
6. Check Railway logs for `[bot] polling...`.

Do not put the real bot token into source code. If a token was previously exposed, revoke it in BotFather and use the new token only as a Railway variable.

## Storage

The bot creates its runtime `storage/` and `sandbox/` directories automatically. Railway's filesystem is writable during runtime, but files can be lost when the service is recreated/redeployed. Use a Railway Volume if persistent bot data is required.

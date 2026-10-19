# HostRailway

Telegram bot hosting utilities for Railway.

## Persistent credit ledger

The repository now includes `credit_system.py`, a SQLite-backed ledger for tracking:
- Each user's current credit balance.
- Each user's hosted bot records and lifecycle status.
- Credit grants and deductions, including reason, admin ID, bot ID, timestamp, and balance after each transaction.
- Per-user history and admin summary helpers.

### Railway persistence

SQLite data must be placed on a persistent Railway Volume. Create/mount a volume at `/data` and set this service variable:

```env
HOSTRAILWAY_DB=/data/hostrailway.sqlite3
```

Without a persistent volume (or an external persistent database), the database file may be lost when the service is replaced/redeployed.

### Integration status

`credit_system.py` is the persistence layer; the existing `bot.py` hosting workflow has not yet been connected to it. To activate charging in the actual hosting flow:
1. Call `register_hosted_bot(user_id, bot_id, bot_name, username, source_fingerprint)` when a user's bot is first saved. Updating/restarting an existing bot record does not deduct credits.
2. Call `consume_credit(user_id, amount=1, reason="new bot hosting", bot_id=bot_id)` only at the confirmed new-hosting action, before launching the new bot. Handle `ValueError("insufficient credits")` by stopping the operation.
3. Call `add_credit(user_id, amount, admin_id, reason)` only after checking the acting Telegram/web user is an authorized admin.
4. Put `admin_all_users()` and `admin_user_summary(user_id)` behind server-side admin authentication. Do not expose them to normal users or trust an admin ID supplied by the browser.
5. Connect the host/start/stop/restart/delete actions to `set_bot_status()` so lifecycle changes persist.

Example:

```python
from credit_system import (
    register_hosted_bot, consume_credit, add_credit,
    admin_all_users, admin_user_summary, set_bot_status,
)
```

The ledger uses atomic SQLite transactions and prevents balances from becoming negative. The functions named `admin_*` are data helpers, not an authentication system: the existing bot/web admin handlers must enforce authorization before calling them.

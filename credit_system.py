"""Persistent per-user/per-bot credit ledger for HostRailway.

This module stores credit balances, hosted-bot records, and an append-only
transaction history in SQLite. Set HOSTRAILWAY_DB to a file on a persistent
Railway Volume (for example /data/hostrailway.sqlite3).

Integration note: call consume_credit() at the exact point where a hosting
operation consumes a credit, and call add_credit() when an admin grants credits.
Do not count a bot restart/re-host as a new charge unless it is a new hosting
operation under your product rules.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

DB_PATH = Path(os.environ.get("HOSTRAILWAY_DB", "data/hostrailway.sqlite3"))


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=20, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_credit_db() -> None:
    """Create tables safely; existing balances and history are preserved."""
    with _transaction() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS credit_users (
            user_id TEXT PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS hosted_bots (
            bot_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            bot_name TEXT NOT NULL DEFAULT '',
            source_fingerprint TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'saved',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES credit_users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_hosted_bots_user ON hosted_bots(user_id);
        CREATE TABLE IF NOT EXISTS credit_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('grant', 'consume', 'adjustment')),
            reason TEXT NOT NULL DEFAULT '',
            admin_id TEXT NOT NULL DEFAULT '',
            bot_id TEXT NOT NULL DEFAULT '',
            balance_after INTEGER NOT NULL CHECK(balance_after >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES credit_users(user_id)
        );
        CREATE INDEX IF NOT EXISTS idx_credit_tx_user ON credit_transactions(user_id, id DESC);
        """)


def _ensure_user(db: sqlite3.Connection, user_id: str, username: str = "") -> None:
    uid = str(user_id).strip()
    if not uid:
        raise ValueError("user_id is required")
    db.execute(
        """INSERT INTO credit_users(user_id, username) VALUES (?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
           username=CASE WHEN excluded.username='' THEN credit_users.username
                         ELSE excluded.username END,
           updated_at=CURRENT_TIMESTAMP""",
        (uid, str(username or "")[:100]),
    )


def register_hosted_bot(
    user_id: str, bot_id: str, bot_name: str = "",
    username: str = "", source_fingerprint: str = "",
) -> dict:
    """Save/update a bot record without changing the user's credit balance."""
    init_credit_db()
    uid, bid = str(user_id).strip(), str(bot_id).strip()
    if not bid:
        raise ValueError("bot_id is required")
    with _transaction() as db:
        _ensure_user(db, uid, username)
        db.execute(
            """INSERT INTO hosted_bots(bot_id,user_id,bot_name,source_fingerprint,status)
               VALUES(?,?,?,?, 'saved')
               ON CONFLICT(bot_id) DO UPDATE SET
                 user_id=excluded.user_id,
                 bot_name=excluded.bot_name,
                 source_fingerprint=CASE WHEN excluded.source_fingerprint=''
                      THEN hosted_bots.source_fingerprint ELSE excluded.source_fingerprint END,
                 updated_at=CURRENT_TIMESTAMP""",
            (bid, uid, str(bot_name or "")[:160], str(source_fingerprint or "")[:200]),
        )
        row = db.execute("SELECT * FROM hosted_bots WHERE bot_id=?", (bid,)).fetchone()
        return dict(row)


def add_credit(user_id: str, amount: int, admin_id: str, reason: str = "", username: str = "") -> int:
    """Grant positive credits. Returns the new balance; admin authorization is caller's job."""
    if isinstance(amount, bool) or int(amount) <= 0:
        raise ValueError("amount must be a positive integer")
    amount = int(amount)
    init_credit_db()
    with _transaction() as db:
        _ensure_user(db, str(user_id), username)
        db.execute(
            "UPDATE credit_users SET balance=balance+?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (amount, str(user_id)),
        )
        balance = int(db.execute("SELECT balance FROM credit_users WHERE user_id=?",
                                  (str(user_id),)).fetchone()["balance"])
        db.execute(
            """INSERT INTO credit_transactions(user_id,amount,kind,reason,admin_id,balance_after)
               VALUES(?,?,'grant',?,?,?)""",
            (str(user_id), amount, str(reason or "")[:300], str(admin_id), balance),
        )
        return balance


def consume_credit(user_id: str, amount: int = 1, reason: str = "hosting", bot_id: str = "") -> int:
    """Atomically deduct credits or fail without allowing a negative balance."""
    if isinstance(amount, bool) or int(amount) <= 0:
        raise ValueError("amount must be a positive integer")
    amount = int(amount)
    init_credit_db()
    with _transaction() as db:
        row = db.execute("SELECT balance FROM credit_users WHERE user_id=?",
                         (str(user_id),)).fetchone()
        if row is None:
            raise ValueError("user has no credit account")
        balance = int(row["balance"])
        if balance < amount:
            raise ValueError("insufficient credits")
        balance -= amount
        db.execute(
            "UPDATE credit_users SET balance=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?",
            (balance, str(user_id)),
        )
        db.execute(
            """INSERT INTO credit_transactions(user_id,amount,kind,reason,bot_id,balance_after)
               VALUES(?,?,'consume',?,?,?)""",
            (str(user_id), -amount, str(reason or "")[:300], str(bot_id or ""), balance),
        )
        return balance


def set_bot_status(bot_id: str, status: str) -> None:
    """Persist lifecycle state such as saved/running/stopped/error."""
    allowed = {"saved", "running", "stopped", "error", "pending"}
    if status not in allowed:
        raise ValueError("invalid bot status")
    init_credit_db()
    with _transaction() as db:
        cur = db.execute(
            "UPDATE hosted_bots SET status=?, updated_at=CURRENT_TIMESTAMP WHERE bot_id=?",
            (status, str(bot_id)),
        )
        if cur.rowcount == 0:
            raise ValueError("bot not found")


def admin_user_summary(user_id: str) -> dict:
    """Return balance, hosted bots, and history for an authenticated admin UI only."""
    init_credit_db()
    with _connect() as db:
        user = db.execute("SELECT * FROM credit_users WHERE user_id=?", (str(user_id),)).fetchone()
        if user is None:
            return {"user": None, "bots": [], "transactions": []}
        bots = db.execute(
            "SELECT * FROM hosted_bots WHERE user_id=? ORDER BY created_at DESC", (str(user_id),)
        ).fetchall()
        tx = db.execute(
            """SELECT * FROM credit_transactions WHERE user_id=?
               ORDER BY id DESC LIMIT 100""", (str(user_id),)
        ).fetchall()
        return {"user": dict(user), "bots": [dict(x) for x in bots],
                "transactions": [dict(x) for x in tx]}


def admin_all_users() -> list[dict]:
    """Return all users and balances for an authenticated admin UI only."""
    init_credit_db()
    with _connect() as db:
        rows = db.execute("""
            SELECT u.user_id, u.username, u.balance, u.updated_at,
                   COUNT(b.bot_id) AS hosted_bot_count
            FROM credit_users u LEFT JOIN hosted_bots b ON b.user_id=u.user_id
            GROUP BY u.user_id ORDER BY u.updated_at DESC
        """).fetchall()
        return [dict(row) for row in rows]


init_credit_db()

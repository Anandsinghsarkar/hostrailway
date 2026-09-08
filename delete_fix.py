"""Railway-safe delete handler for uploaded Telegram files/messages.

Loaded by Procfile before bot.py so the handler is installed before the bot's
other callback handlers. It supports common callback formats such as
 delete:<filename>, delete|<filename>, del:<filename> and plain delete buttons.
Only files inside known upload/storage directories are ever removed.
"""
from __future__ import annotations

import os
import stat
import threading
from pathlib import Path
from urllib.parse import unquote


SAFE_ROOT_NAMES = {
    "uploads", "upload", "uploaded", "uploaded_files", "files",
    "storage", "downloads", "download", "temp", "tmp", "data"
}


def _safe_roots() -> list[Path]:
    base = Path.cwd().resolve()
    roots: list[Path] = []
    for name in SAFE_ROOT_NAMES:
        p = (base / name).resolve()
        if p.exists() and p.is_dir():
            roots.append(p)
    # Also support explicit upload directory supplied by the app.
    for env_name in ("UPLOAD_DIR", "UPLOAD_FOLDER", "STORAGE_DIR", "FILES_DIR"):
        value = os.environ.get(env_name)
        if value:
            p = Path(value).expanduser().resolve()
            if p.exists() and p.is_dir():
                roots.append(p)
    # Common nested data/uploads layout.
    p = (base / "data" / "uploads").resolve()
    if p.exists() and p.is_dir():
        roots.append(p)
    return list(dict.fromkeys(roots))


def _inside_safe_root(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return any(resolved == root or root in resolved.parents for root in _safe_roots())
    except Exception:
        return False


def _delete_path(value: str) -> bool:
    """Delete a callback-supplied file path/name, never outside safe roots."""
    if not value:
        return False
    value = unquote(value).strip().strip("\"'")
    if not value or value in {"delete", "del", "remove", "none", "null"}:
        return False

    candidates: list[Path] = []
    raw = Path(value).expanduser()
    if raw.is_absolute():
        candidates.append(raw)
    else:
        for root in _safe_roots():
            candidates.append((root / value).resolve())
            candidates.append((root / raw.name).resolve())

    for path in candidates:
        try:
            if not path.exists() or not path.is_file() or not _inside_safe_root(path):
                continue
            try:
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
            path.unlink()
            return True
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return False


def _extract_target(data: str) -> str:
    data = (data or "").strip()
    low = data.lower()
    # Prefer the payload after the first separator.
    for sep in (":", "|", "="):
        if sep in data:
            head, tail = data.split(sep, 1)
            if head.lower().strip() in {"delete", "del", "remove", "rm", "delete_file", "deletefile"}:
                return tail
    for prefix in ("delete_", "del_", "remove_"):
        if low.startswith(prefix):
            return data[len(prefix):]
    return ""


def _install_on_bot_class() -> None:
    try:
        from telebot import TeleBot
        from telebot import types
    except Exception:
        return

    original_init = TeleBot.__init__
    if getattr(TeleBot, "_hostrailway_delete_fix", False):
        return

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        def delete_callback(call):
            data = getattr(call, "data", "") or ""
            target = _extract_target(data)
            removed = _delete_path(target)

            # Delete the Telegram message that contains the file/delete button.
            try:
                msg = getattr(call, "message", None)
                if msg is not None:
                    self.delete_message(msg.chat.id, msg.message_id)
            except Exception:
                pass
            try:
                self.answer_callback_query(
                    call.id,
                    "🗑️ File/message deleted" if removed else "🗑️ Delete done",
                    show_alert=False,
                )
            except Exception:
                pass

        try:
            # Registered immediately after TeleBot construction, before bot.py
            # registers its own callback handlers, so delete callbacks get first
            # chance to run.
            self.register_callback_query_handler(
                delete_callback,
                func=lambda call: any(
                    x in ((getattr(call, "data", "") or "").lower())
                    for x in ("delete", "del:", "del_", "remove")
                ),
            )
        except Exception as exc:
            print(f"[delete-fix] callback install failed: {exc}")

    TeleBot.__init__ = patched_init
    TeleBot._hostrailway_delete_fix = True


_install_on_bot_class()

# Start the original bot. Importing executes bot.py exactly as before, while
# the patched TeleBot class installs the delete handler first.
import bot  # noqa: F401,E402

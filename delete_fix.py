"""Railway-safe delete handler for uploaded Telegram files/messages.

This launcher patches TeleBot before executing bot.py, so existing bot logic
stays intact while delete callbacks get a reliable first-pass handler.
"""
from __future__ import annotations

import os
import runpy
import stat
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
    for env_name in ("UPLOAD_DIR", "UPLOAD_FOLDER", "STORAGE_DIR", "FILES_DIR"):
        value = os.environ.get(env_name)
        if value:
            p = Path(value).expanduser().resolve()
            if p.exists() and p.is_dir():
                roots.append(p)
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
    """Delete only a callback-supplied file inside an allowed storage root."""
    if not value:
        return False
    value = unquote(value).strip().strip("\"'")
    if not value or value.lower() in {"delete", "del", "remove", "none", "null"}:
        return False

    raw = Path(value).expanduser()
    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw.resolve())
    else:
        for root in _safe_roots():
            candidates.append((root / value).resolve())
            # Also support callbacks that send only the stored filename.
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
    for sep in (":", "|", "="):
        if sep in data:
            head, tail = data.split(sep, 1)
            if head.lower().strip() in {
                "delete", "del", "remove", "rm", "delete_file", "deletefile"
            }:
                return tail
    for prefix in ("delete_", "del_", "remove_"):
        if low.startswith(prefix):
            return data[len(prefix):]
    return ""


def _install_on_bot_class() -> None:
    try:
        from telebot import TeleBot
    except Exception as exc:
        print(f"[delete-fix] telebot import failed: {exc}")
        return

    if getattr(TeleBot, "_hostrailway_delete_fix", False):
        return

    original_init = TeleBot.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)

        def delete_callback(call):
            data = getattr(call, "data", "") or ""
            removed = _delete_path(_extract_target(data))

            # Remove the Telegram message containing the uploaded file/button.
            try:
                msg = getattr(call, "message", None)
                if msg is not None:
                    self.delete_message(msg.chat.id, msg.message_id)
            except Exception:
                pass

            try:
                self.answer_callback_query(
                    call.id,
                    "🗑️ File deleted" if removed else "🗑️ Delete done",
                    show_alert=False,
                )
            except Exception:
                pass

        try:
            # Installed immediately after construction, before bot.py adds its
            # own callback handlers, giving this handler priority.
            self.register_callback_query_handler(
                delete_callback,
                func=lambda call: any(
                    token in ((getattr(call, "data", "") or "").lower())
                    for token in ("delete", "del:", "del_", "remove")
                ),
            )
        except Exception as exc:
            print(f"[delete-fix] callback install failed: {exc}")

    TeleBot.__init__ = patched_init
    TeleBot._hostrailway_delete_fix = True


_install_on_bot_class()

# Execute bot.py exactly as if Railway had launched `python bot.py`.
runpy.run_path(str(Path(__file__).with_name("bot.py")), run_name="__main__")

"""Local history store: the last complete snapshot, written atomically at mode 0600."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any

from .client import ApiError, Client
from .paths import write_private
from .report import sort_key


def load_store(path: Path) -> dict[str, Any] | None:
    """The stored snapshot, or None if it is missing, malformed or not marked complete."""
    try:
        blob = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return blob if isinstance(blob, dict) and blob.get("complete") else None


def save_store(blob: dict[str, Any], path: Path) -> Path:
    """Write under an exclusive lock so concurrent syncs cannot discard each other's delta."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    deadline = time.time() + 30
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 120:  # abandoned lock
                    lock.unlink()
                    continue
            except OSError:
                continue
            if time.time() > deadline:
                raise ApiError(f"Store lock held by another process: {lock}")
            time.sleep(0.2)
    try:
        return write_private(path, json.dumps(blob, indent=2, ensure_ascii=False))
    finally:
        lock.unlink(missing_ok=True)


def sync(client: Client, path: Path, *, full: bool = False) -> dict[str, Any]:
    """Pull what is new since the stored cursor (everything if `full` or no store) and save."""
    blob = client.snapshot(None if full else load_store(path))
    blob["last_synced_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save_store(blob, path)
    return blob


def age_days(blob: dict[str, Any]) -> float | None:
    try:
        when = dt.datetime.fromisoformat(str(blob["last_synced_at"]))
    except (KeyError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return round((dt.datetime.now(dt.timezone.utc) - when).total_seconds() / 86400, 2)


def rows(blob: dict[str, Any], user_id: str | None = None) -> list[dict]:
    """Stored records, oldest first. With `user_id`, only that profile (never falls back to all)."""
    histories = blob.get("histories") or {}
    if user_id is not None:
        histories = {user_id: histories[user_id]} if user_id in histories else {}
    return sorted((r for h in histories.values() for r in h.get("measurements") or []), key=sort_key)

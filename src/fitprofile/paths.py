"""XDG-style locations and private atomic file writes."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _dir(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback) / "fitprofile"


def default_env_file() -> Path:
    return _dir("XDG_CONFIG_HOME", ".config") / "credentials.env"


def default_store() -> Path:
    return _dir("XDG_DATA_HOME", ".local/share") / "store.json"


def default_token_cache() -> Path:
    return _dir("XDG_CACHE_HOME", ".cache") / "token.json"


def write_private(path: str | Path, text: str) -> Path:
    """Atomically write `text` to `path`, readable only by the current user."""
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".fitprofile-", dir=target.parent)  # mode 0600
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return target

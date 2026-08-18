"""Filesystem locations for Paygo's local state.

Local-first (README "Core principles" 6): everything lives on disk under a
per-user home directory, no cloud. The location is overridable via ``PAYGO_HOME``
so tests get an isolated ledger and users can relocate state if needed.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "PAYGO_HOME"
DEFAULT_HOME = Path.home() / ".paygo"


def home_dir() -> Path:
    """Return the Paygo home directory (``$PAYGO_HOME`` or ``~/.paygo``)."""
    override = os.environ.get(ENV_HOME)
    return Path(override).expanduser() if override else DEFAULT_HOME


def db_path() -> Path:
    """Return the path to the SQLite ledger file."""
    return home_dir() / "ledger.db"

"""Filesystem locations and local config for Paygo.

Local-first: everything lives under ``$PAYGO_HOME`` or ``~/.paygo``. Secrets
never go in ``config.toml`` — Coinbase credentials stay in the environment
(``CDP_API_KEY_ID``, ``CDP_API_KEY_SECRET``, ``CDP_WALLET_SECRET``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

ENV_HOME = "PAYGO_HOME"
DEFAULT_HOME = Path.home() / ".paygo"

WALLET_DEMO = "demo"
WALLET_COINBASE = "coinbase"
NETWORK_BASE_SEPOLIA = "base-sepolia"
NETWORK_BASE = "base"


def home_dir() -> Path:
    """Return the Paygo home directory (``$PAYGO_HOME`` or ``~/.paygo``)."""
    override = os.environ.get(ENV_HOME)
    return Path(override).expanduser() if override else DEFAULT_HOME


def db_path() -> Path:
    """Return the path to the SQLite ledger file."""
    return home_dir() / "ledger.db"


def config_path() -> Path:
    return home_dir() / "config.toml"


@dataclass
class Config:
    """The on-disk setup. Safe to print; contains no secrets."""

    wallet_kind: str = WALLET_DEMO
    network: str = NETWORK_BASE_SEPOLIA
    account_name: str = "paygo"
    address: str = ""


def load() -> Config:
    """Load ``config.toml`` if it exists, otherwise return demo defaults."""
    path = config_path()
    if not path.is_file():
        return Config()
    data = tomllib.loads(path.read_text())
    wallet = data.get("wallet") or {}
    return Config(
        wallet_kind=wallet.get("kind", WALLET_DEMO),
        network=wallet.get("network", NETWORK_BASE_SEPOLIA),
        account_name=wallet.get("account_name", "paygo"),
        address=wallet.get("address", "") or "",
    )


def save(cfg: Config) -> Path:
    """Write ``config.toml``. Idempotent; never writes secrets."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Paygo local config. Secrets are NEVER stored here.\n"
        "# Coinbase credentials live in the environment:\n"
        "#   CDP_API_KEY_ID, CDP_API_KEY_SECRET, CDP_WALLET_SECRET\n"
        "\n"
        "[wallet]\n"
        f"kind = {cfg.wallet_kind!r}\n"
        f"network = {cfg.network!r}\n"
        f"account_name = {cfg.account_name!r}\n"
        f"address = {cfg.address!r}\n"
    )
    return path

"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from paygo.budget import BudgetEngine
from paygo.sessions import SessionManager


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Path to an isolated, throwaway ledger per test."""
    return tmp_path / "ledger.db"


@pytest.fixture()
def engine(db_path: Path) -> BudgetEngine:
    """A BudgetEngine backed by an isolated, throwaway ledger per test."""
    return BudgetEngine(db_path)


@pytest.fixture()
def sessions(db_path: Path) -> SessionManager:
    """A SessionManager sharing the same isolated ledger as ``engine``."""
    return SessionManager(db_path)

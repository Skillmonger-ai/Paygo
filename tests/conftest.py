"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from paygo.budget import BudgetEngine


@pytest.fixture()
def engine(tmp_path: Path) -> BudgetEngine:
    """A BudgetEngine backed by an isolated, throwaway ledger per test."""
    return BudgetEngine(tmp_path / "ledger.db")

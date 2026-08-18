"""Unit tests for the budget kernel state machine."""

from __future__ import annotations

import pytest

from paygo.budget import RUN_REVOKED, BudgetEngine
from paygo.errors import (
    BudgetExceeded,
    InvalidAmount,
    InvalidTransition,
    ReservationNotFound,
    RunNotActive,
    RunNotFound,
)

DOLLAR = 1_000_000


def test_create_run_initial_snapshot(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", 5 * DOLLAR)
    snap = engine.snapshot(run)
    assert snap.authorized == 5 * DOLLAR
    assert snap.settled == 0
    assert snap.reserved == 0
    assert snap.available == 5 * DOLLAR
    assert snap.status == "ACTIVE"


def test_create_run_rejects_non_positive(engine: BudgetEngine) -> None:
    with pytest.raises(InvalidAmount):
        engine.create_run("agent", 0)


def test_reserve_holds_budget(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.reserve(run, 300_000)
    snap = engine.snapshot(run)
    assert snap.reserved == 300_000
    assert snap.available == 700_000


def test_settle_frees_unused_reservation(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 300_000)  # reserve the max quote
    engine.settle(res, 80_000)  # actual cost is lower
    snap = engine.snapshot(run)
    assert snap.settled == 80_000
    assert snap.reserved == 0  # the 220_000 unused is released implicitly
    assert snap.available == DOLLAR - 80_000


def test_release_returns_budget(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 300_000)
    engine.release(res)
    snap = engine.snapshot(run)
    assert snap.reserved == 0
    assert snap.available == DOLLAR


def test_reserve_beyond_remaining_denied(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.reserve(run, 900_000)
    with pytest.raises(BudgetExceeded):
        engine.reserve(run, 200_000)
    # Invariant preserved: the failed reserve left no residue.
    snap = engine.snapshot(run)
    assert snap.settled + snap.reserved <= snap.authorized
    assert snap.reserved == 900_000


def test_settle_above_reserved_fails_closed(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 100_000)
    with pytest.raises(InvalidAmount):
        engine.settle(res, 100_001)


def test_double_settle_rejected(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 100_000)
    engine.settle(res, 100_000)
    with pytest.raises(InvalidTransition):
        engine.settle(res, 100_000)


def test_release_after_settle_rejected(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 100_000)
    engine.settle(res, 50_000)
    with pytest.raises(InvalidTransition):
        engine.release(res)


def test_topup_raises_ceiling(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.reserve(run, DOLLAR)  # fully reserved, nothing available
    assert engine.snapshot(run).available == 0
    engine.topup(run, 500_000)
    assert engine.snapshot(run).available == 500_000


def test_revoke_denies_further_spend(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    snap = engine.revoke(run)
    assert snap.status == RUN_REVOKED
    with pytest.raises(RunNotActive):
        engine.reserve(run, 1)


def test_topup_on_revoked_run_rejected(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.revoke(run)
    with pytest.raises(RunNotActive):
        engine.topup(run, DOLLAR)


def test_unknown_ids(engine: BudgetEngine) -> None:
    with pytest.raises(RunNotFound):
        engine.reserve("pg_missing", 1)
    with pytest.raises(ReservationNotFound):
        engine.settle("res_missing", 1)

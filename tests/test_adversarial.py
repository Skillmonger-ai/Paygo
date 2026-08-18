"""Adversarial cases the kernel must refuse (fail closed).

These mirror the child-is-hostile threat model (README "Security model"): the
kernel is the enforcement boundary, so it must reject every attempt to spend
beyond, retry into a double charge, or transition state illegally.
"""

from __future__ import annotations

import pytest

from paygo.budget import BudgetEngine
from paygo.errors import BudgetExceeded, InvalidAmount, InvalidTransition, RunNotActive

DOLLAR = 1_000_000


def test_price_larger_than_remaining_is_denied(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", 50_000)  # $0.05 budget
    with pytest.raises(BudgetExceeded):
        engine.reserve(run, 60_000)  # a $0.06 quote must be denied up front


def test_changed_price_on_retry_cannot_exceed_reservation(engine: BudgetEngine) -> None:
    # Reserve at the quoted max; if the merchant later reports a higher actual
    # cost, settlement must refuse rather than silently overcharge the budget.
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 100_000)
    with pytest.raises(InvalidAmount):
        engine.settle(res, 150_000)


def test_spend_after_stop_is_denied(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.revoke(run)  # `paygo stop`
    with pytest.raises(RunNotActive):
        engine.reserve(run, 1)


def test_settlement_is_idempotent_against_replay(engine: BudgetEngine) -> None:
    # A retried settlement (network replay) must not become a second charge.
    run = engine.create_run("agent", DOLLAR)
    res = engine.reserve(run, 100_000)
    engine.settle(res, 100_000)
    with pytest.raises(InvalidTransition):
        engine.settle(res, 100_000)
    # Only one settlement is recorded, so spend did not double.
    assert engine.snapshot(run).settled == 100_000
    assert len(engine.list_transactions(run)) == 1


def test_budget_cannot_be_exceeded_across_mixed_operations(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", 300_000)
    r1 = engine.reserve(run, 100_000)
    engine.settle(r1, 100_000)
    r2 = engine.reserve(run, 100_000)
    engine.settle(r2, 100_000)
    engine.reserve(run, 100_000)  # exactly reaches the ceiling
    with pytest.raises(BudgetExceeded):
        engine.reserve(run, 1)
    snap = engine.snapshot(run)
    assert snap.settled + snap.reserved <= snap.authorized

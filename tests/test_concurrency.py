"""Concurrency proof: the ceiling holds under simultaneous contention.

This is the Milestone-1 definition of done (IMPLEMENTATION_PLAN.md, Milestones):

    100 concurrent requests cannot exceed the authorized ceiling.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from paygo.budget import BudgetEngine
from paygo.errors import BudgetExceeded

DOLLAR = 1_000_000


def test_100_concurrent_reservations_cannot_exceed_ceiling(engine: BudgetEngine) -> None:
    # $1.00 ceiling, 100 threads each trying to reserve $0.02 => demand $2.00.
    # Exactly 50 can fit; the rest must be denied, and never overshoot.
    run = engine.create_run("agent", DOLLAR)
    per = 20_000

    def attempt(_: int) -> bool:
        try:
            engine.reserve(run, per)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(max_workers=32) as pool:
        results = [f.result() for f in as_completed(pool.submit(attempt, i) for i in range(100))]

    successes = sum(results)
    snap = engine.snapshot(run)

    # The core invariant must hold no matter how threads interleaved.
    assert snap.settled + snap.reserved <= snap.authorized
    # And it must be *tight*: exactly the ceiling was reserved, no more, no less.
    assert successes == 50
    assert snap.reserved == DOLLAR
    assert snap.available == 0


def test_exact_exhaustion(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.reserve(run, DOLLAR)  # consume the whole ceiling
    try:
        engine.reserve(run, 1)  # one microdollar more must fail
        raised = False
    except BudgetExceeded:
        raised = True
    assert raised
    assert engine.snapshot(run).available == 0


def test_one_cent_remaining(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)
    engine.reserve(run, DOLLAR - 10_000)  # leave exactly $0.01
    assert engine.snapshot(run).available == 10_000
    engine.reserve(run, 10_000)  # take the last cent
    assert engine.snapshot(run).available == 0


def test_concurrent_topups_are_not_lost(engine: BudgetEngine) -> None:
    run = engine.create_run("agent", DOLLAR)

    def topup(_: int) -> None:
        engine.topup(run, 10_000)  # +$0.01 each

    with ThreadPoolExecutor(max_workers=16) as pool:
        for f in as_completed(pool.submit(topup, i) for i in range(50)):
            f.result()

    # 50 atomic +$0.01 increments must all land: $1.00 + $0.50 = $1.50.
    assert engine.snapshot(run).authorized == DOLLAR + 50 * 10_000

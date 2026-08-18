"""Demonstrate the Milestone-1 budget kernel end to end.

This stands in for the real ``paygo exec`` wrapper (Milestone 2): instead of
launching a child process, it drives :class:`~paygo.budget.BudgetEngine`
directly to show the product promise — *an agent can spend, but cannot exceed
its ceiling* — against the local ledger.

Run it with an isolated ledger so `paygo` CLI commands can then inspect it::

    PAYGO_HOME=/tmp/paygo-demo uv run python examples/budget_demo.py
    PAYGO_HOME=/tmp/paygo-demo uv run paygo status
"""

from __future__ import annotations

from paygo import config
from paygo.budget import BudgetEngine
from paygo.errors import BudgetExceeded
from paygo.money import format_dollars, parse_dollars

# Priced fake "services" a pretend agent might buy. No real money involved.
SPEND_PLAN = [
    ("inference", "0.30"),
    ("exa/search", "0.10"),
    ("inference", "0.40"),
    ("inference", "0.50"),  # this one should push us over a $1.00 budget
]


def main() -> None:
    engine = BudgetEngine(config.db_path())
    run = engine.create_run("examples/budget_demo.py", parse_dollars("1.00"))
    print(f"PAYGO\nRun        {run}\nBudget     {format_dollars(parse_dollars('1.00'))}\n")

    for service, price in SPEND_PLAN:
        micros = parse_dollars(price)
        try:
            # Real flow: reserve the max quote, execute, settle the actual cost.
            reservation = engine.reserve(run, micros, provider="demo", request_hash=service)
        except BudgetExceeded:
            print(f"agent → {service:<14} DENIED (would exceed budget)")
            continue
        engine.settle(reservation, micros, service=service)
        print(f"agent → {service:<14} -{format_dollars(micros)}")

    snap = engine.snapshot(run)
    print(
        f"\nSpent       {format_dollars(snap.settled)}"
        f"\nRemaining   {format_dollars(snap.available)}"
    )

    # The user (not the agent) tops up to let the task continue.
    print("\nUser tops up +$0.50 …")
    engine.topup(run, parse_dollars("0.50"))
    last_service, last_price = SPEND_PLAN[-1]
    reservation = engine.reserve(run, parse_dollars(last_price), provider="demo")
    engine.settle(reservation, parse_dollars(last_price), service=last_service)
    print(f"agent → {last_service:<14} -{format_dollars(parse_dollars(last_price))}")

    snap = engine.snapshot(run)
    print(
        f"\nAuthorized  {format_dollars(snap.authorized)}"
        f"\nSpent       {format_dollars(snap.settled)}"
        f"\nRemaining   {format_dollars(snap.available)}"
        f"\n\nInspect with:  PAYGO_HOME={config.home_dir()} paygo inspect {run}"
    )


if __name__ == "__main__":
    main()

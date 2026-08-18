"""Paygo command-line interface (Milestone 1 surface).

Kept boring on purpose (PROJECT_PLAN.md rule 13). This milestone implements the
read/admin commands that operate directly on the local ledger:

    paygo init
    paygo status [RUN_ID]
    paygo history
    paygo inspect RUN_ID
    paygo topup RUN_ID AMOUNT
    paygo stop RUN_ID

The spend-side commands documented in the README (``paygo exec`` / ``paygo
doctor``) require the process wrapper and are staged for Milestone 2. They are
registered here as explicit, fail-closed placeholders so the CLI never pretends
to enforce a budget it cannot yet enforce.
"""

from __future__ import annotations

import typer

from paygo import __version__, config
from paygo.budget import BudgetEngine, RunSnapshot
from paygo.errors import PaygoError
from paygo.money import format_dollars, parse_dollars

app = typer.Typer(
    add_completion=False,
    help="Give software an allowance: a hard dollar budget around a process.",
    no_args_is_help=True,
)


def _engine() -> BudgetEngine:
    """Construct an engine bound to the configured ledger path."""
    return BudgetEngine(config.db_path())


def _print_snapshot(snap: RunSnapshot) -> None:
    """Render a run snapshot in the README's `paygo status` layout."""
    typer.echo(f"PAYGO — {snap.id}")
    typer.echo("")
    typer.echo(f"{'Authorized':<20}{format_dollars(snap.authorized)}")
    typer.echo(f"{'Settled':<20}{format_dollars(snap.settled)}")
    typer.echo(f"{'Reserved':<20}{format_dollars(snap.reserved)}")
    typer.echo(f"{'Available':<20}{format_dollars(snap.available)}")
    typer.echo(f"{'Status':<20}{snap.status}")


@app.command()
def init() -> None:
    """Initialize the local Paygo home directory and SQLite ledger."""
    # Constructing the engine creates ~/.paygo and the ledger schema idempotently.
    _engine()
    typer.echo(f"Initialized Paygo ledger at {config.db_path()}")


@app.command()
def status(run_id: str = typer.Argument(None, help="Run id; omit to list all.")) -> None:
    """Show budget state for one run, or a summary of all runs."""
    engine = _engine()
    try:
        if run_id:
            _print_snapshot(engine.snapshot(run_id))
            return
        runs = engine.list_runs()
    except PaygoError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if not runs:
        typer.echo("No runs yet. Create spending under a budget with `paygo exec`.")
        return
    # Compact one-line-per-run summary when no specific run is requested.
    for snap in runs:
        typer.echo(
            f"{snap.id:<12} {snap.status:<10} "
            f"spent {format_dollars(snap.settled)} / {format_dollars(snap.authorized)}  "
            f"available {format_dollars(snap.available)}"
        )


@app.command()
def history() -> None:
    """List every run recorded in the ledger, newest first."""
    for snap in _engine().list_runs():
        typer.echo(
            f"{snap.created_at}  {snap.id:<12} {snap.status:<10} "
            f"{format_dollars(snap.settled)} / {format_dollars(snap.authorized)}  "
            f"{snap.command}"
        )


@app.command()
def inspect(run_id: str = typer.Argument(..., help="Run id to inspect.")) -> None:
    """Show the itemized transaction ledger for a run."""
    engine = _engine()
    try:
        snap = engine.snapshot(run_id)
        txns = engine.list_transactions(run_id)
    except PaygoError as exc:
        raise typer.BadParameter(str(exc)) from exc

    _print_snapshot(snap)
    typer.echo("")
    if not txns:
        typer.echo("No transactions yet.")
        return
    for txn in txns:
        label = txn["service"] or txn["provider"]
        # Timestamps are stored as ISO-8601; show just the time portion.
        when = txn["created_at"][11:19]
        typer.echo(
            f"{when}  {label:<20} -{format_dollars(txn['amount_microdollars'])}"
        )


@app.command()
def topup(
    run_id: str = typer.Argument(..., help="Run id to top up."),
    amount: str = typer.Argument(..., help="Dollar amount to add, e.g. 5 or 0.10."),
) -> None:
    """Raise a run's authorization ceiling (user-side administrative action)."""
    engine = _engine()
    try:
        snap = engine.topup(run_id, parse_dollars(amount))
    except PaygoError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Topped up {run_id} by {format_dollars(parse_dollars(amount))}.")
    _print_snapshot(snap)


@app.command()
def stop(run_id: str = typer.Argument(..., help="Run id to stop/revoke.")) -> None:
    """Revoke a run so no further paid operations are authorized."""
    engine = _engine()
    try:
        snap = engine.revoke(run_id)
    except PaygoError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Run {run_id} is now {snap.status}. The wallet remains yours.")


# --- Milestone 2 placeholders (process wrapper) --------------------------
# These are the README's headline commands. They are intentionally not yet
# functional; they fail closed rather than launch a child with a budget we
# cannot yet enforce end-to-end.
_M2_MESSAGE = (
    "`paygo {name}` requires the process wrapper (Milestone 2) and is not "
    "available in this build. Milestone 1 ships the budget kernel and ledger "
    "commands (init, status, history, inspect, topup, stop)."
)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def exec() -> None:  # noqa: A001 - matches the documented `paygo exec` command
    """Run a command under a hard budget (Milestone 2 — not yet implemented)."""
    typer.echo(_M2_MESSAGE.format(name="exec"), err=True)
    raise typer.Exit(code=2)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def doctor() -> None:
    """Report budget-bypass risks before launch (Milestone 2 — not yet implemented)."""
    typer.echo(_M2_MESSAGE.format(name="doctor"), err=True)
    raise typer.Exit(code=2)


@app.command()
def version() -> None:
    """Print the Paygo version."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()

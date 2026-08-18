"""Paygo command-line interface.

    paygo init
    paygo exec -b N [--strict] -- <command>
    paygo doctor -- <command>
    paygo status [RUN_ID]
    paygo history
    paygo inspect RUN_ID
    paygo topup RUN_ID AMOUNT
    paygo stop RUN_ID
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess

import typer

from paygo import __version__, config
from paygo.budget import RUN_COMPLETED, RUN_FAILED, BudgetEngine, RunSnapshot
from paygo.credentials import PROVIDER_ENV_VARS, WALLET_ENV_VARS, present
from paygo.demo import create_demo_merchant
from paygo.errors import PaygoError
from paygo.money import format_dollars, parse_dollars
from paygo.runtime import (
    ENV_DEMO_MERCHANT,
    LocalRuntime,
    build_child_environment,
)
from paygo.service import create_app
from paygo.sessions import SessionManager
from paygo.wallet import FakeWallet
from paygo.x402 import X402Buyer

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


# --- Milestone 2: process wrapper ----------------------------------------


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def exec(  # noqa: A001 - matches the documented `paygo exec` command
    ctx: typer.Context,
    budget: str = typer.Option(..., "-b", "--budget", help="Ceiling in dollars, e.g. 5 or 0.50."),
    strict: bool = typer.Option(
        False, "--strict", help="Scrub known provider/wallet credentials from the child."
    ),
) -> None:
    """Run a command under a hard budget.

    Paygo creates a run, mints a run-scoped session token, starts a localhost
    service the child can query, launches the child with that token injected,
    forwards its stdio, and revokes the token when it exits. The child can read
    its balance but cannot administer the run (no top-up/stop endpoint).
    """
    command = list(ctx.args)  # everything after `--` (and unknown options)
    if not command:
        raise typer.BadParameter("No command given. Usage: paygo exec -b 5 -- <command>")

    try:
        authorized = parse_dollars(budget)
    except PaygoError as exc:
        raise typer.BadParameter(str(exc)) from exc

    engine = _engine()
    sessions = SessionManager(config.db_path())
    run_id = engine.create_run(" ".join(command), authorized)
    token = sessions.mint(run_id)

    # Demo merchant is a separate origin sharing an HMAC secret with FakeWallet.
    # The secret never enters the child environment.
    demo_secret = secrets.token_hex(16)
    wallet = FakeWallet(demo_secret)
    buyer = X402Buyer(engine, wallet)
    merchant_runtime = LocalRuntime(create_demo_merchant(demo_secret))
    paygo_runtime = LocalRuntime(create_app(engine, sessions, buyer))
    merchant_url = merchant_runtime.start()
    base_url = paygo_runtime.start()

    typer.echo("PAYGO")
    typer.echo(f"Run        {run_id}")
    typer.echo(f"Budget     {format_dollars(authorized)}")
    if strict:
        typer.echo("Mode       strict (provider/wallet credentials scrubbed)")
    typer.echo("")

    child_env = build_child_environment(
        os.environ.copy(),
        run_id=run_id,
        base_url=base_url,
        token=token,
        strict=strict,
        extra={ENV_DEMO_MERCHANT: merchant_url},
    )

    exit_code = 1
    try:
        proc = subprocess.Popen(command, env=child_env)
    except OSError as exc:
        paygo_runtime.stop()
        merchant_runtime.stop()
        sessions.revoke_run(run_id)
        wallet.revoke_session(run_id)
        engine.finalize(run_id, RUN_FAILED)
        raise typer.BadParameter(f"Could not launch {command[0]!r}: {exc}") from exc

    def _forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    prev_int = signal.signal(signal.SIGINT, _forward)
    prev_term = signal.signal(signal.SIGTERM, _forward)
    try:
        exit_code = proc.wait()
    finally:
        signal.signal(signal.SIGINT, prev_int)
        signal.signal(signal.SIGTERM, prev_term)
        sessions.revoke_run(run_id)
        wallet.revoke_session(run_id)
        snap = engine.finalize(run_id, RUN_COMPLETED if exit_code == 0 else RUN_FAILED)
        paygo_runtime.stop()
        merchant_runtime.stop()

    typer.echo("")
    typer.echo(f"Spent       {format_dollars(snap.settled)}")
    typer.echo(f"Remaining   {format_dollars(snap.available)}")
    raise typer.Exit(code=exit_code)


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def doctor(ctx: typer.Context) -> None:
    """Report budget-bypass risks before launching a command.

    Trust requires visibility (SYSTEM_DESIGN.md, "doctor"). This inspects what can
    be checked before launch and prints an honest verdict; it never claims strict
    enforcement when a bypass path is known.
    """
    command = " ".join(ctx.args) if ctx.args else "(none)"
    present_provider = present(PROVIDER_ENV_VARS)
    wallet_configured = bool(present(WALLET_ENV_VARS))

    typer.echo("Paygo doctor")
    typer.echo("")
    typer.echo(f"{'Command':<24}{command}")
    typer.echo(f"{'Wallet':<24}{'configured' if wallet_configured else 'demo (fake HMAC wallet)'}")
    typer.echo(f"{'Paid path (x402)':<24}demo merchant (fake 402, no real money)")
    if present_provider:
        typer.echo(f"{'Existing provider keys':<24}{', '.join(present_provider)}")
        typer.echo(f"{'Budget guarantee':<24}PARTIAL")
        typer.echo("")
        typer.echo(
            "The child may be able to spend outside Paygo using the credentials "
            "above.\nUse --strict to scrub them, or remove them from the environment."
        )
    else:
        typer.echo(f"{'Existing provider keys':<24}none detected")
        typer.echo(f"{'Budget guarantee':<24}HARD (no known bypass credentials)")


@app.command()
def version() -> None:
    """Print the Paygo version."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()

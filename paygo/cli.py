"""Paygo command-line interface (Milestone 1 surface).

Kept boring on purpose (IMPLEMENTATION_PLAN.md, coding rules). This implements the
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

import os
import signal
import subprocess

import typer

from paygo import __version__, config
from paygo.budget import RUN_COMPLETED, RUN_FAILED, BudgetEngine, RunSnapshot
from paygo.errors import PaygoError
from paygo.money import format_dollars, parse_dollars
from paygo.runtime import LocalRuntime
from paygo.service import create_app
from paygo.sessions import SessionManager

# Provider credentials a child could use to spend *outside* Paygo. `doctor`
# reports them; `--strict` scrubs them from the child environment.
_KNOWN_PROVIDER_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
]
# Wallet/admin credentials that must never reach the child under strict mode.
_KNOWN_WALLET_KEYS = [
    "CDP_API_KEY_NAME",
    "CDP_API_KEY_PRIVATE_KEY",
    "COINBASE_API_KEY",
    "COINBASE_API_SECRET",
    "WALLET_PRIVATE_KEY",
]

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

    runtime = LocalRuntime(create_app(engine, sessions))
    base_url = runtime.start()

    typer.echo("PAYGO")
    typer.echo(f"Run        {run_id}")
    typer.echo(f"Budget     {format_dollars(authorized)}")
    if strict:
        typer.echo("Mode       strict (provider/wallet credentials scrubbed)")
    typer.echo("")

    # The child receives only narrow, run-scoped config — never wallet or admin
    # credentials (SYSTEM_DESIGN.md, "Security & threat model").
    child_env = os.environ.copy()
    if strict:
        for key in (*_KNOWN_PROVIDER_KEYS, *_KNOWN_WALLET_KEYS):
            child_env.pop(key, None)
    child_env["PAYGO_RUN_ID"] = run_id
    child_env["PAYGO_BASE_URL"] = base_url
    child_env["PAYGO_SESSION_TOKEN"] = token

    exit_code = 1
    try:
        proc = subprocess.Popen(command, env=child_env)
    except OSError as exc:
        # Launch failed (not found, not executable, empty argv, …). Clean up the
        # run and credentials before surfacing a clear error.
        runtime.stop()
        sessions.revoke_run(run_id)
        engine.finalize(run_id, RUN_FAILED)
        raise typer.BadParameter(f"Could not launch {command[0]!r}: {exc}") from exc

    # Be a thin wrapper: forward Ctrl-C / termination to the child, then clean up
    # after it exits.
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
        # Always revoke credentials and stop the runtime, even on error.
        sessions.revoke_run(run_id)
        snap = engine.finalize(run_id, RUN_COMPLETED if exit_code == 0 else RUN_FAILED)
        runtime.stop()

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
    present_provider = [k for k in _KNOWN_PROVIDER_KEYS if os.environ.get(k)]
    wallet_configured = any(os.environ.get(k) for k in _KNOWN_WALLET_KEYS)

    typer.echo("Paygo doctor")
    typer.echo("")
    typer.echo(f"{'Command':<24}{command}")
    typer.echo(f"{'Wallet':<24}{'configured' if wallet_configured else 'not configured'}")
    # The paid path (x402/inference) is not wired up until later milestones; say
    # so plainly rather than imply a capability that does not exist yet.
    typer.echo(f"{'Paid path (x402)':<24}not yet available (planned)")
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

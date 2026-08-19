"""Paygo command-line interface.

paygo init
paygo demo
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
import sys

import typer

from paygo import __version__, config
from paygo.budget import RUN_COMPLETED, RUN_FAILED, BudgetEngine, RunSnapshot
from paygo.coinbase import (
    CoinbaseWallet,
    coinbase_extra_installed,
    missing_cdp_credentials,
)
from paygo.credentials import (
    CDP_PORTAL_API_KEYS,
    CDP_PORTAL_WALLET_SECRET,
    CDP_REQUIRED_ENV_VARS,
    PROVIDER_ENV_VARS,
    present,
)
from paygo.demo import create_demo_merchant
from paygo.errors import PaygoError, PaymentFailed, UnsupportedPayment
from paygo.harness import home_exists, identify, on_path
from paygo.money import format_dollars, parse_dollars
from paygo.runtime import (
    ENV_DEMO_MERCHANT,
    LocalRuntime,
    build_child_environment,
)
from paygo.service import create_app
from paygo.sessions import SessionManager
from paygo.wallet import FakeWallet, RoutingWallet
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
def init(
    wallet: str | None = typer.Option(
        None,
        "--wallet",
        help="demo (no account, instant) or coinbase (USDC on Base via CDP). "
        "Omit to keep the current wallet, or demo on first run.",
    ),
    network: str | None = typer.Option(
        None,
        "--network",
        help="base-sepolia (testnet, faucet) or base (mainnet). Omit to keep the current network.",
    ),
    faucet: bool = typer.Option(
        False, "--faucet", help="After Coinbase setup, request testnet USDC."
    ),
) -> None:
    """Set up Paygo on this machine. Safe to re-run.

    First run defaults to the demo wallet: no accounts, no keys, no USDC.
    ``--wallet coinbase`` is a one-time opt-in that reads CDP credentials from
    the environment and never writes them to disk. Re-running without flags
    keeps the current wallet.
    """
    existing = config.load() if config.config_path().is_file() else config.Config()
    kind = wallet if wallet is not None else existing.wallet_kind
    net = network if network is not None else existing.network

    if kind not in {config.WALLET_DEMO, config.WALLET_COINBASE}:
        raise typer.BadParameter("--wallet must be 'demo' or 'coinbase'.")
    if net not in {config.NETWORK_BASE_SEPOLIA, config.NETWORK_BASE}:
        raise typer.BadParameter("--network must be 'base-sepolia' or 'base'.")

    _engine()  # creates the ledger schema
    cfg = config.Config(
        wallet_kind=kind,
        network=net,
        account_name=existing.account_name or "paygo",
        address=existing.address if kind == config.WALLET_COINBASE else "",
    )

    typer.echo("PAYGO setup")
    typer.echo("")
    typer.echo(f"{'Ledger':<22}{config.db_path()}")

    if kind == config.WALLET_DEMO:
        if faucet:
            typer.echo("Note: --faucet applies to Coinbase testnet; ignored on demo.")
        config.save(cfg)
        typer.echo(f"{'Wallet':<22}demo (no real money)")
        typer.echo(f"{'Config':<22}{config.config_path()}")
        typer.echo("")
        typer.echo("Ready. No accounts, no keys, no USDC required.")
        typer.echo("")
        typer.echo("  paygo demo")
        typer.echo("")
        typer.echo("When you want real USDC on Base:")
        typer.echo("  paygo init --wallet coinbase --faucet")
        return

    # --- Coinbase path ---------------------------------------------------
    # Persist intent first so doctor knows the user wants Coinbase even if
    # credentials or the optional extra are still missing.
    config.save(cfg)
    missing = missing_cdp_credentials()
    extra_ok = coinbase_extra_installed()
    if missing or not extra_ok:
        typer.echo(f"{'Wallet':<22}coinbase (setup incomplete)")
        typer.echo("")
        if missing:
            typer.echo("Create these once in the Coinbase CDP portal (free):")
            typer.echo(f"  API key + secret     {CDP_PORTAL_API_KEYS}")
            typer.echo(f"  Wallet secret        {CDP_PORTAL_WALLET_SECRET}")
            typer.echo("")
            typer.echo("Then export — Paygo never writes these to disk:")
            for name in CDP_REQUIRED_ENV_VARS:
                mark = "✓ " if name not in missing else "  "
                typer.echo(f"  {mark}export {name}=...")
            typer.echo("")
        if not extra_ok:
            typer.echo("Install the optional extra (account + faucet + signing):")
            typer.echo("  uv tool install --force 'paygo[coinbase]'")
            typer.echo("  # from a clone: uv tool install --force '.[coinbase]'")
            typer.echo("")
        typer.echo("Re-run:")
        typer.echo("  paygo init --wallet coinbase --faucet")
        raise typer.Exit(code=2)

    cdp_wallet = CoinbaseWallet(account_name=cfg.account_name, network=net, address=cfg.address)
    try:
        address = cdp_wallet.address()
    except PaymentFailed as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc

    cfg.address = address
    config.save(cfg)
    typer.echo(f"{'Wallet':<22}coinbase / CDP")
    typer.echo(f"{'Network':<22}{net}")
    typer.echo(f"{'Address':<22}{address}")
    typer.echo(f"{'Config':<22}{config.config_path()}")

    if faucet:
        if net != config.NETWORK_BASE_SEPOLIA:
            typer.echo("Faucet skipped (only available on base-sepolia).")
        else:
            try:
                tx = cdp_wallet.request_faucet()
                typer.echo(f"{'Faucet':<22}requested testnet USDC ({tx})")
                typer.echo("Wait ~30s for confirmation, then:  paygo doctor")
            except (PaymentFailed, UnsupportedPayment) as exc:
                typer.echo(f"{'Faucet':<22}failed — {exc}")

    try:
        usdc = cdp_wallet.balance()
        typer.echo(f"{'USDC':<22}{format_dollars(usdc)}")
        if usdc == 0 and not faucet:
            typer.echo("")
            typer.echo("Wallet is empty. Fund it with testnet USDC:")
            typer.echo(f"  send USDC on Base Sepolia to {address}")
            typer.echo("  or re-run:  paygo init --wallet coinbase --faucet")
    except PaymentFailed as exc:
        typer.echo(f"{'USDC':<22}(could not fetch: {exc})")

    typer.echo("")
    typer.echo("The child process never sees these credentials.")
    typer.echo("Demo spend still works (fake merchant); Base quotes use this wallet.")
    typer.echo("")
    typer.echo("  paygo doctor")
    typer.echo("  paygo demo")


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
        typer.echo(f"{when}  {label:<20} -{format_dollars(txn['amount_microdollars'])}")


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


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def exec(  # noqa: A001 - matches the documented `paygo exec` command
    ctx: typer.Context,
    budget: str = typer.Option(..., "-b", "--budget", help="Ceiling in dollars, e.g. 5 or 0.50."),
    strict: bool = typer.Option(
        False, "--strict", help="Scrub known provider credentials from the child."
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
    _run_under_budget(command, budget, strict=strict)


@app.command()
def demo(
    budget: str = typer.Option(
        "0.25",
        "-b",
        "--budget",
        help="Ceiling in dollars. Default $0.25 covers two $0.10 searches.",
    ),
) -> None:
    """Try Paygo: buy fake search until the budget runs out.

    No git clone, no extra packages. Uses the Python that installed ``paygo``
    so ``httpx`` is already there.
    """
    _run_under_budget([sys.executable, "-m", "paygo.demo_agent"], budget, strict=False)


def _run_under_budget(command: list[str], budget: str, *, strict: bool) -> None:
    """Shared launch path for ``paygo exec`` and ``paygo demo``."""
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
    wallet = _runtime_wallet(demo_secret)
    buyer = X402Buyer(engine, wallet)
    merchant_runtime = LocalRuntime(create_demo_merchant(demo_secret))
    paygo_runtime = LocalRuntime(create_app(engine, sessions, buyer))
    merchant_url = merchant_runtime.start()
    base_url = paygo_runtime.start()

    typer.echo("PAYGO")
    typer.echo(f"Run        {run_id}")
    typer.echo(f"Budget     {format_dollars(authorized)}")
    if strict:
        typer.echo("Mode       strict (provider credentials scrubbed)")
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


def _runtime_wallet(demo_secret: str) -> RoutingWallet:
    """Demo merchant always works; Coinbase is used only for Base quotes."""
    demo = FakeWallet(demo_secret)
    cfg = config.load()
    if cfg.wallet_kind != config.WALLET_COINBASE:
        return RoutingWallet(demo)
    if missing_cdp_credentials():
        return RoutingWallet(demo)
    return RoutingWallet(
        demo,
        CoinbaseWallet(
            account_name=cfg.account_name,
            network=cfg.network,
            address=cfg.address,
        ),
    )


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def doctor(ctx: typer.Context) -> None:
    """Check whether Paygo is ready, and report budget-bypass risks.

    Run with no arguments after ``paygo init`` to see the setup checklist.
    Pass a command (``paygo doctor -- codex``) to also inspect that process.
    """
    command = " ".join(ctx.args) if ctx.args else None
    cfg = config.load()
    present_provider = present(PROVIDER_ENV_VARS)

    typer.echo("Paygo doctor")
    typer.echo("")

    ledger_ok = config.db_path().is_file()
    ledger = config.db_path()
    if ledger_ok:
        typer.echo(f"{'Ledger':<24}✓ {ledger}")
    else:
        typer.echo(f"{'Ledger':<24}✗  run paygo init")

    if cfg.wallet_kind == config.WALLET_DEMO:
        typer.echo(f"{'Wallet':<24}✓ demo (no real money)")
        typer.echo(f"{'Paid path':<24}✓ demo merchant (fake 402)")
        typer.echo(f"{'USDC / Base':<24}— not configured (optional)")
    else:
        missing = missing_cdp_credentials()
        extra_ok = coinbase_extra_installed()
        if missing or not extra_ok:
            typer.echo(f"{'Wallet':<24}⚠ coinbase (setup incomplete)")
            typer.echo(f"{'Paid path':<24}⚠ demo merchant only until setup is complete")
            if missing:
                typer.echo(f"{'CDP credentials':<24}✗ {', '.join(missing)}")
            if not extra_ok:
                typer.echo(f"{'Coinbase extra':<24}✗ uv tool install --force 'paygo[coinbase]'")
            typer.echo(f"{'Next':<24}export CDP_* if needed, then: paygo init --wallet coinbase")
        else:
            addr = cfg.address or "(run paygo init --wallet coinbase to fetch address)"
            typer.echo(f"{'Wallet':<24}✓ coinbase / CDP")
            typer.echo(f"{'Network':<24}{cfg.network}")
            typer.echo(f"{'Address':<24}{addr}")
            if cfg.address:
                try:
                    usdc = CoinbaseWallet(
                        account_name=cfg.account_name,
                        network=cfg.network,
                        address=cfg.address,
                    ).balance()
                    label = f"✓ {format_dollars(usdc)}"
                    if usdc == 0:
                        label += " — fund or: paygo init --wallet coinbase --faucet"
                    typer.echo(f"{'USDC':<24}{label}")
                except PaymentFailed as exc:
                    typer.echo(f"{'USDC':<24}⚠ {exc}")
            typer.echo(f"{'Paid path':<24}✓ demo merchant + Base x402 via CDP")

    harness = identify(command) if command else None
    leftover_home = bool(harness and home_exists(harness))
    partial = bool(present_provider or leftover_home or (harness and harness.nested))

    if command:
        typer.echo(f"{'Command':<24}{command}")
        if harness:
            found = on_path(harness)
            typer.echo(f"{'Harness':<24}{harness.product}")
            if found:
                typer.echo(f"{'On PATH':<24}✓ {found}")
            else:
                typer.echo(f"{'On PATH':<24}✗ {harness.binary} not found")
            typer.echo(f"{'Run it':<24}{harness.attach}")
            typer.echo(f"{'How it spends':<24}{harness.spend}")
            if leftover_home:
                typer.echo(f"{'Harness home':<24}⚠ ~/{harness.home} exists (login may bypass)")
            if harness.nested:
                typer.echo(f"{'Nested CLIs':<24}⚠ may spawn Codex/Claude with their own login")
        else:
            typer.echo(f"{'Harness':<24}generic process (Paygo wraps whatever you exec)")

    if present_provider:
        typer.echo(f"{'Existing provider keys':<24}{', '.join(present_provider)}")
    else:
        typer.echo(f"{'Existing provider keys':<24}✓ none detected")

    if partial:
        typer.echo(f"{'Budget guarantee':<24}PARTIAL")
        typer.echo("")
        if present_provider:
            typer.echo("The child may spend outside Paygo using the environment credentials above.")
            typer.echo("Use --strict to scrub them, or remove them from the environment.")
        if leftover_home:
            typer.echo(
                "A harness login directory exists. Subscription/OAuth spend is not "
                "Paygo-mediated. Sign out of that harness or use --strict plus API-key mode."
            )
        if harness and harness.nested:
            typer.echo(
                "This harness can spawn nested CLIs. Their own logins are a separate spend path."
            )
    else:
        typer.echo(f"{'Budget guarantee':<24}HARD (no known bypass credentials)")

    if ledger_ok:
        typer.echo("")
        typer.echo("Ready:")
        if harness:
            typer.echo(f"  {harness.attach}")
        else:
            typer.echo("  paygo demo")


@app.command()
def version() -> None:
    """Print the Paygo version."""
    typer.echo(__version__)


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()

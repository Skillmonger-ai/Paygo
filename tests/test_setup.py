"""Setup UX: init, config.toml, doctor checklist. No secrets on disk."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from paygo import config
from paygo.coinbase import (
    CoinbaseWallet,
    coinbase_extra_installed,
    missing_cdp_credentials,
    usdc_from_balances,
)
from paygo.credentials import CDP_REQUIRED_ENV_VARS, WALLET_ENV_VARS, scrub
from paygo.errors import PaymentFailed, UnsupportedPayment
from paygo.runtime import ENV_RUN_ID, build_child_environment
from paygo.wallet import FakeWallet, RoutingWallet
from paygo.x402 import DEMO_NETWORK, PaymentRequirements

PAYGO_BIN = Path(sys.executable).parent / "paygo"
needs_cli = pytest.mark.skipif(not PAYGO_BIN.exists(), reason="paygo console script not installed")
REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def home(tmp_path: Path, monkeypatch) -> Path:
    """Isolated ``$PAYGO_HOME`` for both the CLI subprocess and in-process config."""
    monkeypatch.setenv("PAYGO_HOME", str(tmp_path))
    for name in CDP_REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _cli(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PAYGO_BIN), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        timeout=60,
    )


@needs_cli
def test_init_demo_writes_config_and_ledger(home: Path) -> None:
    result = _cli("init")
    assert result.returncode == 0, result.stderr
    assert "Ready" in result.stdout
    assert (home / "ledger.db").is_file()
    cfg = config.load()
    assert cfg.wallet_kind == "demo"
    text = (home / "config.toml").read_text()
    assert "kind = 'demo'" in text
    # Comments may mention env-var *names*, but never assignment of a secret.
    assert "sk-" not in text
    assert "SECRET = '" not in text
    for name in CDP_REQUIRED_ENV_VARS:
        assert f"{name}=" not in text


@needs_cli
def test_init_is_idempotent(home: Path) -> None:
    first = _cli("init")
    second = _cli("init")
    assert first.returncode == 0 and second.returncode == 0
    assert config.load().wallet_kind == "demo"


@needs_cli
def test_init_without_flags_keeps_coinbase_intent(home: Path) -> None:
    """Bare `paygo init` must not clobber a Coinbase opt-in back to demo."""
    first = _cli("init", "--wallet", "coinbase")
    assert first.returncode == 2
    second = _cli("init")
    assert second.returncode == 2, second.stdout
    assert config.load().wallet_kind == "coinbase"


@needs_cli
def test_init_wallet_demo_switches_back(home: Path) -> None:
    _cli("init", "--wallet", "coinbase")
    result = _cli("init", "--wallet", "demo")
    assert result.returncode == 0
    cfg = config.load()
    assert cfg.wallet_kind == "demo"
    assert cfg.address == ""


@needs_cli
def test_init_coinbase_without_creds_explains_setup(home: Path) -> None:
    result = _cli("init", "--wallet", "coinbase")
    assert result.returncode == 2
    assert "portal.cdp.coinbase.com" in result.stdout
    assert "CDP_API_KEY_ID" in result.stdout
    assert "never writes these to disk" in result.stdout
    assert "paygo[coinbase]" in result.stdout
    assert config.load().wallet_kind == "coinbase"


@needs_cli
def test_init_coinbase_with_creds_but_no_extra_still_saves_intent(home: Path) -> None:
    """Credentials without the extra must not skip writing kind=coinbase."""
    if coinbase_extra_installed():
        pytest.skip("cdp-sdk is installed in this environment")
    result = _cli(
        "init",
        "--wallet",
        "coinbase",
        extra_env={
            "CDP_API_KEY_ID": "id",
            "CDP_API_KEY_SECRET": "sec",
            "CDP_WALLET_SECRET": "wal",
        },
    )
    assert result.returncode == 2
    assert "paygo[coinbase]" in result.stdout
    assert "portal.cdp.coinbase.com" not in result.stdout  # creds were present
    assert config.load().wallet_kind == "coinbase"
    assert "super-secret" not in (home / "config.toml").read_text()


@needs_cli
def test_doctor_without_init_tells_you_to_init(home: Path) -> None:
    result = _cli("doctor")
    assert result.returncode == 0, result.stderr
    assert "run paygo init" in result.stdout


@needs_cli
def test_doctor_demo_is_ready(home: Path) -> None:
    assert _cli("init").returncode == 0
    result = _cli("doctor")
    assert result.returncode == 0, result.stderr
    assert "demo" in result.stdout
    assert "HARD" in result.stdout
    assert "paygo demo" in result.stdout


@needs_cli
def test_doctor_coinbase_incomplete_is_honest(home: Path) -> None:
    _cli("init", "--wallet", "coinbase")
    result = _cli("doctor")
    assert result.returncode == 0
    assert "coinbase" in result.stdout
    assert "demo merchant only" in result.stdout
    assert "HARD" in result.stdout  # no provider keys → Paygo-mediated spend is still hard


@needs_cli
def test_doctor_partial_when_provider_key_present(home: Path) -> None:
    _cli("init")
    result = _cli("doctor", extra_env={"OPENAI_API_KEY": "sk-test"})
    assert "PARTIAL" in result.stdout
    assert "OPENAI_API_KEY" in result.stdout


@needs_cli
def test_config_never_round_trips_env_secrets(home: Path) -> None:
    result = _cli("init", extra_env={"CDP_API_KEY_SECRET": "super-secret-value"})
    assert result.returncode == 0
    assert "super-secret-value" not in (home / "config.toml").read_text()


@needs_cli
def test_exec_still_works_after_coinbase_intent(home: Path) -> None:
    """Coinbase opt-in without creds must not break the demo spend path."""
    assert _cli("init", "--wallet", "coinbase").returncode == 2
    result = _cli("demo")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "DENIED" in result.stdout
    assert "Spent       $0.20" in result.stdout


def test_strict_scrubs_current_cdp_names() -> None:
    env = {"CDP_API_KEY_ID": "id", "CDP_WALLET_SECRET": "sec", "PATH": "/bin"}
    cleaned = scrub(env)
    assert "CDP_API_KEY_ID" not in cleaned
    assert "CDP_WALLET_SECRET" not in cleaned
    assert cleaned["PATH"] == "/bin"
    assert set(CDP_REQUIRED_ENV_VARS) <= set(WALLET_ENV_VARS)


def test_child_never_sees_wallet_secrets_even_without_strict() -> None:
    parent = {
        "PATH": "/bin",
        "CDP_API_KEY_ID": "id",
        "CDP_API_KEY_SECRET": "secret",
        "CDP_WALLET_SECRET": "wallet",
        "OPENAI_API_KEY": "sk-keep-in-standard",
    }
    child = build_child_environment(
        parent, run_id="pg_test", base_url="http://127.0.0.1:9", token="tok"
    )
    assert child[ENV_RUN_ID] == "pg_test"
    assert "CDP_API_KEY_SECRET" not in child
    assert "CDP_WALLET_SECRET" not in child
    assert child["OPENAI_API_KEY"] == "sk-keep-in-standard"
    strict = build_child_environment(
        parent,
        run_id="pg_test",
        base_url="http://127.0.0.1:9",
        token="tok",
        strict=True,
    )
    assert "OPENAI_API_KEY" not in strict


def test_missing_cdp_credentials_lists_all_three(monkeypatch) -> None:
    for name in CDP_REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert missing_cdp_credentials() == list(CDP_REQUIRED_ENV_VARS)


def test_coinbase_without_extra_explains_install() -> None:
    if coinbase_extra_installed():
        pytest.skip("cdp-sdk is installed in this environment")
    with pytest.raises(PaymentFailed) as exc:
        CoinbaseWallet().address()
    assert "paygo[coinbase]" in str(exc.value)


def test_faucet_refused_on_mainnet() -> None:
    with pytest.raises(UnsupportedPayment):
        CoinbaseWallet(network="base", address="0xabc").request_faucet()


def test_usdc_from_cdp_shaped_balances() -> None:
    page = SimpleNamespace(
        balances=[
            SimpleNamespace(
                token=SimpleNamespace(symbol="USDC"),
                amount=SimpleNamespace(amount="250000", decimals=6),
            ),
            SimpleNamespace(
                token=SimpleNamespace(symbol="ETH"),
                amount=SimpleNamespace(amount="1", decimals=18),
            ),
        ]
    )
    assert usdc_from_balances(page) == 250_000
    assert (
        usdc_from_balances(
            {"balances": [{"token": {"symbol": "usdc"}, "amount": {"amount": "10", "decimals": 6}}]}
        )
        == 10
    )


def test_routing_wallet_keeps_demo_working_without_coinbase() -> None:
    wallet = RoutingWallet(FakeWallet("secret"))
    quote = PaymentRequirements(
        scheme="exact",
        network=DEMO_NETWORK,
        amount=100_000,
        asset="USD",
        pay_to="paygo:demo:merchant",
        resource="http://merchant/search",
    )
    payload = wallet.authorize_x402(quote, "req-1")
    assert payload["payload"]["scheme"] == "paygo-demo"


def test_routing_wallet_explains_coinbase_setup_for_base_quotes() -> None:
    wallet = RoutingWallet(FakeWallet("secret"))
    quote = PaymentRequirements(
        scheme="exact",
        network="eip155:84532",
        amount=10_000,
        asset="USDC",
        pay_to="0xabc",
        resource="https://api.example/paid",
    )
    with pytest.raises(UnsupportedPayment) as exc:
        wallet.authorize_x402(quote, "req-1")
    assert "paygo init --wallet coinbase" in str(exc.value)

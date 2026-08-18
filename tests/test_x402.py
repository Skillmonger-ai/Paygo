"""x402 buyer + fake merchant: the M3 paid path, no real money."""

from __future__ import annotations

import secrets

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from paygo.budget import BudgetEngine
from paygo.demo import DEFAULT_PRICE_MICRODOLLARS, create_demo_merchant
from paygo.errors import BudgetExceeded, PaymentFailed, UnsupportedPayment
from paygo.runtime import LocalRuntime
from paygo.wallet import FakeWallet
from paygo.x402 import DEMO_NETWORK, PAYMENT_SIGNATURE, X402Buyer, encode_header

DOLLAR = 1_000_000


def _merchant(secret: str | None = None) -> tuple[str, LocalRuntime, str]:
    secret = secret or secrets.token_hex(16)
    runtime = LocalRuntime(create_demo_merchant(secret))
    return runtime.start(), runtime, secret


def test_buy_settles_exact_price(engine: BudgetEngine) -> None:
    url, runtime, secret = _merchant()
    try:
        run = engine.create_run("agent", DOLLAR)
        result = X402Buyer(engine, FakeWallet(secret)).buy(
            run, f"{url}/search", method="POST", json_body={"q": "hello"}
        )
        assert result.status_code == 200
        assert result.settled_microdollars == DEFAULT_PRICE_MICRODOLLARS
        assert result.body["query"] == "hello"
        snap = engine.snapshot(run)
        assert snap.settled == DEFAULT_PRICE_MICRODOLLARS
        assert snap.reserved == 0
        assert snap.settled + snap.reserved <= snap.authorized
    finally:
        runtime.stop()


def test_second_buy_aggregates(engine: BudgetEngine) -> None:
    url, runtime, secret = _merchant()
    try:
        run = engine.create_run("agent", DOLLAR)
        buyer = X402Buyer(engine, FakeWallet(secret))
        buyer.buy(run, f"{url}/search", method="POST", json_body={"q": "a"})
        buyer.buy(run, f"{url}/search", method="POST", json_body={"q": "b"})
        assert engine.snapshot(run).settled == 2 * DEFAULT_PRICE_MICRODOLLARS
    finally:
        runtime.stop()


def test_exhaustion_denies_before_payment(engine: BudgetEngine) -> None:
    url, runtime, secret = _merchant()
    try:
        run = engine.create_run("agent", 150_000)
        buyer = X402Buyer(engine, FakeWallet(secret))
        buyer.buy(run, f"{url}/search", method="POST", json_body={"q": "1"})
        with pytest.raises(BudgetExceeded) as exc:
            buyer.buy(run, f"{url}/search", method="POST", json_body={"q": "2"})
        assert exc.value.remaining == 50_000
        snap = engine.snapshot(run)
        assert snap.settled == DEFAULT_PRICE_MICRODOLLARS
        assert snap.reserved == 0
        assert snap.settled + snap.reserved <= snap.authorized
    finally:
        runtime.stop()


def test_retry_same_request_id_does_not_double_charge(engine: BudgetEngine) -> None:
    url, runtime, secret = _merchant()
    try:
        run = engine.create_run("agent", DOLLAR)
        buyer = X402Buyer(engine, FakeWallet(secret))
        first = buyer.buy(
            run, f"{url}/search", method="POST", json_body={"q": "idem"}, request_id="req-1"
        )
        second = buyer.buy(
            run, f"{url}/search", method="POST", json_body={"q": "idem"}, request_id="req-1"
        )
        assert first.replayed is False
        assert second.replayed is True
        assert engine.snapshot(run).settled == DEFAULT_PRICE_MICRODOLLARS
        assert len(engine.list_transactions(run)) == 1
    finally:
        runtime.stop()


def test_wallet_poorer_than_quote_releases_hold(engine: BudgetEngine) -> None:
    url, runtime, secret = _merchant()
    try:
        run = engine.create_run("agent", DOLLAR)
        with pytest.raises(UnsupportedPayment):
            X402Buyer(engine, FakeWallet(secret, balance_microdollars=1)).buy(
                run, f"{url}/search", method="POST", json_body={"q": "x"}
            )
        snap = engine.snapshot(run)
        assert snap.reserved == 0
        assert snap.settled == 0
    finally:
        runtime.stop()


def test_forged_payment_is_rejected_by_merchant() -> None:
    url, runtime, _secret = _merchant()
    try:
        probe = httpx.post(f"{url}/search", json={"q": "x"})
        assert probe.status_code == 402
        forged = encode_header({
            "x402Version": 2,
            "accepted": {"amount": str(DEFAULT_PRICE_MICRODOLLARS), "network": DEMO_NETWORK},
            "payload": {"scheme": "paygo-demo", "mac": "deadbeef", "request_id": "x"},
        })
        paid = httpx.post(
            f"{url}/search", json={"q": "x"}, headers={PAYMENT_SIGNATURE: forged}
        )
        assert paid.status_code == 402
    finally:
        runtime.stop()


def test_malformed_402_fails_closed(engine: BudgetEngine) -> None:
    app = FastAPI()

    @app.get("/weird")
    def weird():
        return JSONResponse({"nope": True}, status_code=402)

    runtime = LocalRuntime(app)
    url = runtime.start()
    try:
        run = engine.create_run("agent", DOLLAR)
        with pytest.raises(PaymentFailed):
            X402Buyer(engine, FakeWallet(secrets.token_hex(16))).buy(run, f"{url}/weird")
        assert engine.snapshot(run).reserved == 0
    finally:
        runtime.stop()

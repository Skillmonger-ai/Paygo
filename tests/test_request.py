"""POST /v1/paygo/request through the token-gated service."""

from __future__ import annotations

import secrets

from fastapi.testclient import TestClient

from paygo.budget import BudgetEngine
from paygo.demo import DEFAULT_PRICE_MICRODOLLARS, create_demo_merchant
from paygo.runtime import LocalRuntime
from paygo.service import create_app
from paygo.sessions import SessionManager
from paygo.wallet import FakeWallet
from paygo.x402 import X402Buyer

DOLLAR = 1_000_000


def test_request_requires_token(engine: BudgetEngine, sessions: SessionManager) -> None:
    secret = secrets.token_hex(16)
    merchant = LocalRuntime(create_demo_merchant(secret))
    merchant_url = merchant.start()
    try:
        buyer = X402Buyer(engine, FakeWallet(secret))
        run = engine.create_run("agent", DOLLAR)
        token = sessions.mint(run)
        client = TestClient(create_app(engine, sessions, buyer))
        payload = {
            "url": f"{merchant_url}/search",
            "method": "POST",
            "json": {"q": "x"},
        }
        assert client.post("/v1/paygo/request", json=payload).status_code == 401
        ok = client.post(
            "/v1/paygo/request",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["ok"] is True
        assert body["settled_microdollars"] == DEFAULT_PRICE_MICRODOLLARS
    finally:
        merchant.stop()


def test_request_budget_exceeded_is_403(engine: BudgetEngine, sessions: SessionManager) -> None:
    secret = secrets.token_hex(16)
    merchant = LocalRuntime(create_demo_merchant(secret))
    merchant_url = merchant.start()
    try:
        run = engine.create_run("agent", DEFAULT_PRICE_MICRODOLLARS)
        token = sessions.mint(run)
        client = TestClient(
            create_app(engine, sessions, X402Buyer(engine, FakeWallet(secret)))
        )
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"url": f"{merchant_url}/search", "method": "POST", "json": {"q": "1"}}
        assert client.post("/v1/paygo/request", headers=headers, json=payload).status_code == 200
        denied = client.post("/v1/paygo/request", headers=headers, json=payload)
        assert denied.status_code == 403
        assert denied.json()["detail"]["error"] == "budget_exceeded"
    finally:
        merchant.stop()


def test_request_without_buyer_is_501(engine: BudgetEngine, sessions: SessionManager) -> None:
    run = engine.create_run("agent", DOLLAR)
    token = sessions.mint(run)
    resp = TestClient(create_app(engine, sessions)).post(
        "/v1/paygo/request",
        headers={"Authorization": f"Bearer {token}"},
        json={"url": "http://example", "method": "GET"},
    )
    assert resp.status_code == 501

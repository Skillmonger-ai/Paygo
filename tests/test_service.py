"""Local service: token-gated, run-scoped, read-only."""

from __future__ import annotations

from fastapi.testclient import TestClient

from paygo.budget import BudgetEngine
from paygo.service import create_app
from paygo.sessions import SessionManager

DOLLAR = 1_000_000


def _client(engine: BudgetEngine, sessions: SessionManager) -> TestClient:
    return TestClient(create_app(engine, sessions))


def test_health_needs_no_auth(engine: BudgetEngine, sessions: SessionManager) -> None:
    resp = _client(engine, sessions).get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_balance_requires_valid_token(engine: BudgetEngine, sessions: SessionManager) -> None:
    run = engine.create_run("agent", 5 * DOLLAR)
    token = sessions.mint(run)
    client = _client(engine, sessions)

    assert client.get("/v1/paygo/balance").status_code == 401
    assert client.get(
        "/v1/paygo/balance", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401

    ok = client.get("/v1/paygo/balance", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    body = ok.json()
    assert body["run_id"] == run
    assert body["authorized_microdollars"] == 5 * DOLLAR
    assert body["available"] == "$5.00"


def test_revoked_token_is_rejected(engine: BudgetEngine, sessions: SessionManager) -> None:
    run = engine.create_run("agent", DOLLAR)
    token = sessions.mint(run)
    client = _client(engine, sessions)
    assert client.get(
        "/v1/paygo/balance", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200
    sessions.revoke_run(run)
    assert client.get(
        "/v1/paygo/balance", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 401


def test_transactions_reflect_settlements(
    engine: BudgetEngine, sessions: SessionManager
) -> None:
    run = engine.create_run("agent", DOLLAR)
    token = sessions.mint(run)
    res = engine.reserve(run, 100_000, provider="demo")
    engine.settle(res, 80_000, service="inference")

    resp = _client(engine, sessions).get(
        "/v1/paygo/transactions", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    txns = resp.json()["transactions"]
    assert len(txns) == 1
    assert txns[0]["service"] == "inference"
    assert txns[0]["amount"] == "$0.08"


def test_child_cannot_administer(engine: BudgetEngine, sessions: SessionManager) -> None:
    # There is no top-up/stop endpoint on the child-facing service.
    run = engine.create_run("agent", DOLLAR)
    token = sessions.mint(run)
    resp = _client(engine, sessions).post(
        "/v1/paygo/topup", headers={"Authorization": f"Bearer {token}"}, json={"amount": 5}
    )
    assert resp.status_code == 404

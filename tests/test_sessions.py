"""Session token mint / verify / revoke."""

from __future__ import annotations

from paygo.budget import BudgetEngine
from paygo.sessions import SessionManager

DOLLAR = 1_000_000


def test_mint_then_verify(engine: BudgetEngine, sessions: SessionManager) -> None:
    run = engine.create_run("agent", DOLLAR)
    token = sessions.mint(run)
    assert sessions.verify(token) == run


def test_unknown_and_empty_tokens_do_not_verify(
    engine: BudgetEngine, sessions: SessionManager
) -> None:
    engine.create_run("agent", DOLLAR)
    assert sessions.verify("nonsense") is None
    assert sessions.verify("") is None


def test_revoke_invalidates_token(engine: BudgetEngine, sessions: SessionManager) -> None:
    run = engine.create_run("agent", DOLLAR)
    token = sessions.mint(run)
    assert sessions.verify(token) == run
    assert sessions.revoke_run(run) == 1
    assert sessions.verify(token) is None


def test_tokens_are_run_scoped(engine: BudgetEngine, sessions: SessionManager) -> None:
    run_a = engine.create_run("a", DOLLAR)
    run_b = engine.create_run("b", DOLLAR)
    token_a = sessions.mint(run_a)
    token_b = sessions.mint(run_b)
    assert token_a != token_b
    assert sessions.verify(token_a) == run_a
    assert sessions.verify(token_b) == run_b
    # Revoking one run does not affect the other.
    sessions.revoke_run(run_a)
    assert sessions.verify(token_a) is None
    assert sessions.verify(token_b) == run_b

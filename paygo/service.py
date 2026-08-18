"""Local HTTP service the child process talks to.

Bound to 127.0.0.1 only (SYSTEM_DESIGN.md, "Security & threat model"). Every
child-facing endpoint requires the run-scoped bearer token, and a token only
grants access to *its own* run. This Milestone-2 service is read-only:

    GET  /health                     liveness (no auth)
    GET  /v1/paygo/balance           this run's budget snapshot
    GET  /v1/paygo/transactions      this run's settled transactions

The paid path (``POST /v1/paygo/request``) and the OpenAI-compatible inference
proxy arrive in later milestones; deliberately absent here so the child cannot
yet move money it shouldn't.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI, Header, HTTPException

from paygo.budget import BudgetEngine
from paygo.money import format_dollars
from paygo.sessions import SessionManager


def create_app(engine: BudgetEngine, sessions: SessionManager) -> FastAPI:
    """Build the FastAPI app wired to a specific engine + session manager."""
    app = FastAPI(title="Paygo local runtime", docs_url=None, redoc_url=None)

    def current_run(authorization: str | None = Header(default=None)) -> str:
        """Resolve the caller's run id from its bearer token, or 401.

        Failures are intentionally indistinguishable (missing, malformed,
        unknown, revoked all yield the same 401) so the child learns nothing
        about why a token was rejected.
        """
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        run_id = sessions.verify(token)
        if run_id is None:
            raise HTTPException(status_code=401, detail="Invalid or revoked session token.")
        return run_id

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/paygo/balance")
    def balance(run_id: str = Depends(current_run)) -> dict:
        snap = engine.snapshot(run_id)
        # Return raw microdollars (authoritative) plus formatted dollars (display)
        # so agents can do exact math and humans can read the logs.
        return {
            "run_id": snap.id,
            "status": snap.status,
            "authorized_microdollars": snap.authorized,
            "settled_microdollars": snap.settled,
            "reserved_microdollars": snap.reserved,
            "available_microdollars": snap.available,
            "authorized": format_dollars(snap.authorized),
            "settled": format_dollars(snap.settled),
            "reserved": format_dollars(snap.reserved),
            "available": format_dollars(snap.available),
        }

    @app.get("/v1/paygo/transactions")
    def transactions(run_id: str = Depends(current_run)) -> dict:
        txns = engine.list_transactions(run_id)
        return {
            "run_id": run_id,
            "transactions": [
                {
                    "created_at": t["created_at"],
                    "provider": t["provider"],
                    "service": t["service"],
                    "kind": t["kind"],
                    "amount_microdollars": t["amount_microdollars"],
                    "amount": format_dollars(t["amount_microdollars"]),
                }
                for t in txns
            ],
        }

    return app

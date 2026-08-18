"""Local HTTP service the child process talks to.

Bound to 127.0.0.1 only (SYSTEM_DESIGN.md, "Security & threat model"). Every
child-facing endpoint requires the run-scoped bearer token, and a token only
grants access to *its own* run.

    GET  /health                     liveness (no auth)
    GET  /v1/paygo/balance           this run's budget snapshot
    GET  /v1/paygo/transactions      this run's settled transactions
    POST /v1/paygo/request           the paid-request primitive (M3)

There is no top-up, stop, or wallet-admin endpoint. The OpenAI-compatible
inference proxy arrives in Milestone 5 as another front door onto the same
buyer.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from paygo.budget import BudgetEngine
from paygo.errors import BudgetExceeded, PaymentFailed, UnsupportedPayment
from paygo.money import format_dollars
from paygo.sessions import SessionManager
from paygo.x402 import X402Buyer


class PaidRequest(BaseModel):
    """Body of ``POST /v1/paygo/request``.

    The token — not this body — binds the run. ``url`` is the merchant resource
    the buyer should fetch; for the M3 demo that's ``$PAYGO_DEMO_MERCHANT_URL/search``.
    """

    url: str
    method: str = Field(default="GET")
    json_body: dict[str, Any] | None = Field(default=None, alias="json")
    request_id: str | None = None

    model_config = {"populate_by_name": True}


def create_app(
    engine: BudgetEngine,
    sessions: SessionManager,
    buyer: X402Buyer | None = None,
) -> FastAPI:
    """Build the FastAPI app wired to a specific engine, session manager, and buyer."""
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

    @app.post("/v1/paygo/request")
    def paid_request(
        payload: PaidRequest, run_id: str = Depends(current_run)
    ) -> dict:
        if buyer is None:
            raise HTTPException(status_code=501, detail="Paid path is not configured.")
        method = payload.method.upper()
        if method not in {"GET", "POST"}:
            raise HTTPException(status_code=400, detail=f"Unsupported method {payload.method!r}.")
        try:
            result = buyer.buy(
                run_id,
                payload.url,
                method=method,
                json_body=payload.json_body,
                request_id=payload.request_id,
            )
        except BudgetExceeded as exc:
            # 403, not 402: 402 is the merchant saying "pay me". This is Paygo
            # refusing to authorize the spend.
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "budget_exceeded",
                    "requested_microdollars": exc.requested,
                    "remaining_microdollars": exc.remaining,
                    "requested": format_dollars(exc.requested),
                    "remaining": format_dollars(exc.remaining),
                },
            ) from exc
        except UnsupportedPayment as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PaymentFailed as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return result.envelope()

    return app

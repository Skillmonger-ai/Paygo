"""Built-in fake x402 merchant.

A *separate origin* from the Paygo service (SYSTEM_DESIGN.md §3a). Paygo is the
buyer; this process is a resource that answers 402 until a valid demo-scheme
``PAYMENT-SIGNATURE`` is attached. The MAC key is shared with ``FakeWallet``
and never injected into the child.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException, Request

from paygo.wallet import demo_mac
from paygo.x402 import (
    DEMO_ASSET,
    DEMO_NETWORK,
    PAYMENT_REQUIRED,
    PAYMENT_SIGNATURE,
    PaymentRequirements,
    decode_header,
    encode_header,
)

# $0.10 per call — a $0.25 budget buys twice and is denied on the third.
DEFAULT_PRICE_MICRODOLLARS = 100_000


def create_demo_merchant(
    secret: str, price_microdollars: int = DEFAULT_PRICE_MICRODOLLARS
) -> FastAPI:
    """Build a tiny FastAPI app that sells ``POST /search`` for a fixed price."""
    app = FastAPI(title="Paygo demo merchant", docs_url=None, redoc_url=None)

    def _challenge(resource_url: str) -> HTTPException:
        quote = PaymentRequirements(
            scheme="exact",
            network=DEMO_NETWORK,
            amount=price_microdollars,
            asset=DEMO_ASSET,
            pay_to="paygo:demo:merchant",
            resource=resource_url,
        )
        body = {
            "x402Version": 2,
            "error": f"{PAYMENT_SIGNATURE} header is required",
            "resource": {"url": resource_url, "description": "Fake paid search"},
            "accepts": [quote.to_wire()],
        }
        return HTTPException(
            status_code=402,
            detail=body,
            headers={PAYMENT_REQUIRED: encode_header(body)},
        )

    def _verify(signature_header: str | None, resource_url: str) -> None:
        if not signature_header:
            raise _challenge(resource_url)
        try:
            payload = decode_header(signature_header)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Malformed PAYMENT-SIGNATURE.") from exc
        accepted = payload.get("accepted") or {}
        inner = payload.get("payload") or {}
        try:
            amount = int(accepted.get("amount"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Payment amount missing.") from exc
        request_id = inner.get("request_id") or ""
        mac = inner.get("mac") or ""
        expected = demo_mac(secret, amount, resource_url, request_id)
        # Bind the MAC to this resource and the quoted price so a signature for
        # a cheaper call (or a different path) cannot be replayed here.
        if amount != price_microdollars or not hmac_equal(mac, expected):
            raise HTTPException(status_code=402, detail="Payment verification failed.")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/search")
    async def search(
        request: Request,
        payment_signature: str | None = Header(default=None, alias=PAYMENT_SIGNATURE),
    ) -> dict:
        # Reconstruct the URL the buyer probed so the MAC resource binding matches.
        resource_url = str(request.url)
        _verify(payment_signature, resource_url)
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        query = body.get("q") or body.get("query") or "unknown"
        return {
            "query": query,
            "results": [
                {
                    "title": f"Demo result for {query!r}",
                    "snippet": "This is fake paid search. No real money moved.",
                }
            ],
        }

    return app


def hmac_equal(left: str, right: str) -> bool:
    """Constant-time string compare for MAC hex digests."""
    import hmac as hmac_mod

    return hmac_mod.compare_digest(left.encode(), right.encode())

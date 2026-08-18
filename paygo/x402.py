"""Minimal x402 HTTP buyer.

Spec knowledge lives here and nowhere else (SYSTEM_DESIGN.md §3b): header names,
base64 JSON, ``accepts[]`` selection, the probe-then-retry dance. The kernel
never hears of 402. A later real-chain wallet is a new ``authorize_x402``
implementation, not a rewrite of this file.

Wire format follows x402 HTTP transport v2 closely enough that Milestone 4 is a
merchant/wallet swap:

- 402 + ``PAYMENT-REQUIRED`` (base64 JSON, fallback to JSON body)
- retry with ``PAYMENT-SIGNATURE`` (base64 JSON)
- ``x402Version: 2``, ``accepts[]`` with ``scheme`` / ``network`` / ``amount``
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from paygo.budget import RES_RESERVED, RES_SETTLED, BudgetEngine
from paygo.errors import PaymentFailed, UnsupportedPayment
from paygo.money import format_dollars

# Demo-only CAIP-ish network id. The fake wallet pays this; the Coinbase adapter
# will pay eip155:8453 (Base) and refuse this. Fail closed on anything else.
DEMO_NETWORK = "paygo:demo"
DEMO_ASSET = "USD"

PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"


class Wallet(Protocol):
    """The slice of a wallet the buyer actually calls."""

    def authorize_x402(self, requirements: PaymentRequirements, request_id: str) -> dict: ...


@dataclass(frozen=True)
class PaymentRequirements:
    """One entry from a 402 ``accepts[]`` list, amounts in microdollars."""

    scheme: str
    network: str
    amount: int  # integer microdollars (atomic units of the demo USD asset)
    asset: str
    pay_to: str
    resource: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "network": self.network,
            "amount": str(self.amount),
            "asset": self.asset,
            "payTo": self.pay_to,
            "maxTimeoutSeconds": 60,
        }


@dataclass(frozen=True)
class BuyResult:
    """What ``POST /v1/paygo/request`` returns to the child, unwrapped."""

    status_code: int
    body: Any
    settled_microdollars: int
    replayed: bool = False

    def envelope(self) -> dict[str, Any]:
        return {
            "ok": 200 <= self.status_code < 300,
            "settled_microdollars": self.settled_microdollars,
            "settled": format_dollars(self.settled_microdollars),
            "replayed": self.replayed,
            "status_code": self.status_code,
            "response": self.body,
        }


def encode_header(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_header(value: str) -> dict[str, Any]:
    try:
        return json.loads(base64.b64decode(value))
    except (ValueError, json.JSONDecodeError) as exc:
        raise PaymentFailed("Malformed PAYMENT-REQUIRED header.") from exc


def _request_hash(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def parse_payment_required(response: httpx.Response, resource_url: str) -> dict[str, Any]:
    """Read a 402's PaymentRequired object from the header, then the body."""
    header = response.headers.get(PAYMENT_REQUIRED)
    if header:
        return decode_header(header)
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise PaymentFailed("402 response had no PAYMENT-REQUIRED header or JSON body.") from exc
    if isinstance(body, dict) and "accepts" not in body and isinstance(body.get("detail"), dict):
        body = body["detail"]
    if not isinstance(body, dict) or "accepts" not in body:
        raise PaymentFailed("402 JSON body is not a PaymentRequired object.")
    # Fill in resource URL if the merchant omitted it.
    body.setdefault("resource", {"url": resource_url})
    return body


def select_requirement(payment_required: dict[str, Any], resource_url: str) -> PaymentRequirements:
    """Pick the first exact-scheme quote we understand; fail closed otherwise."""
    accepts = payment_required.get("accepts") or []
    resource = (payment_required.get("resource") or {}).get("url") or resource_url
    for raw in accepts:
        scheme = raw.get("scheme")
        network = raw.get("network")
        asset = raw.get("asset")
        # v2 uses `amount`; some drafts used `maxAmountRequired`. Accept both.
        amount_str = raw.get("amount") or raw.get("maxAmountRequired")
        if scheme != "exact":
            continue
        if not amount_str:
            continue
        try:
            amount = int(amount_str)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        return PaymentRequirements(
            scheme=scheme,
            network=network or "",
            amount=amount,
            asset=asset or "",
            pay_to=raw.get("payTo") or "",
            resource=resource,
        )
    raise UnsupportedPayment("No exact-scheme quote with a usable amount in accepts[].")


class X402Buyer:
    """Runs quote → reserve → authorize → retry → settle against one engine."""

    def __init__(
        self,
        engine: BudgetEngine,
        wallet: Wallet,
        client: httpx.Client | None = None,
    ) -> None:
        self._engine = engine
        self._wallet = wallet
        # The caller owns the client if they passed one (tests); otherwise we
        # open a short-lived client per buy so we don't leak sockets.
        self._client = client

    def buy(
        self,
        run_id: str,
        url: str,
        *,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> BuyResult:
        request_id = request_id or secrets.token_hex(16)
        request_hash = _request_hash(request_id)
        existing = self._engine.find_reservation_by_hash(run_id, request_hash)
        if existing and existing["status"] == RES_SETTLED:
            # Sequential retry after success: do not charge again.
            return BuyResult(
                status_code=200,
                body={"replayed": True, "request_id": request_id},
                settled_microdollars=int(existing["settled_microdollars"] or 0),
                replayed=True,
            )

        reservation_id = (
            existing["id"]
            if existing and existing["status"] == RES_RESERVED
            else None
        )

        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=15.0)
        try:
            probe = client.request(method, url, json=json_body)
            if probe.status_code == 200:
                return BuyResult(
                    status_code=200,
                    body=_as_body(probe),
                    settled_microdollars=0,
                )
            if probe.status_code != 402:
                raise PaymentFailed(
                    f"Merchant returned HTTP {probe.status_code} (expected 402 or 200)."
                )

            required = parse_payment_required(probe, url)
            quote = select_requirement(required, url)

            if reservation_id is None:
                reservation_id = self._engine.reserve(
                    run_id,
                    quote.amount,
                    provider="x402",
                    request_hash=request_hash,
                )

            try:
                payload = self._wallet.authorize_x402(quote, request_id)
                paid = client.request(
                    method,
                    url,
                    json=json_body,
                    headers={PAYMENT_SIGNATURE: encode_header(payload)},
                )
                if paid.status_code != 200:
                    raise PaymentFailed(
                        f"Merchant rejected payment (HTTP {paid.status_code})."
                    )
                self._engine.settle(
                    reservation_id,
                    quote.amount,
                    service=_service_label(url),
                    payment_id=request_id,
                )
                return BuyResult(
                    status_code=200,
                    body=_as_body(paid),
                    settled_microdollars=quote.amount,
                )
            except Exception:
                # Anything after reserve — wallet refusal, merchant 4xx, transport
                # error — must free the hold so the ceiling is not leaked.
                try:
                    self._engine.release(reservation_id)
                except Exception:
                    pass
                raise
        finally:
            if owns_client:
                client.close()


def _as_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except json.JSONDecodeError:
        return response.text


def _service_label(url: str) -> str:
    # Inspect/ledger UX uses this as the per-line label (e.g. "search").
    try:
        path = httpx.URL(url).path.rstrip("/")
    except Exception:
        return url
    return path.rsplit("/", 1)[-1] or url

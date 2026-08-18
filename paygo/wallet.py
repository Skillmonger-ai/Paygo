"""Wallet adapters.

The budget kernel never talks to a chain. Anything that produces a
``PAYMENT-SIGNATURE`` implements the small surface below. Milestone 3 ships
only the HMAC fake used by the demo merchant; the Coinbase/CDP adapter arrives
in Milestone 4 as a second implementation of the same methods (at which point
this file becomes a package).
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol

from paygo.errors import UnsupportedPayment
from paygo.x402 import DEMO_NETWORK, PaymentRequirements


class WalletAdapter(Protocol):
    """Narrow signing/authorization surface used by :class:`paygo.x402.X402Buyer`."""

    def address(self) -> str: ...
    def balance(self) -> int: ...
    def authorize_x402(self, requirements: PaymentRequirements, request_id: str) -> dict: ...
    def revoke_session(self, run_id: str) -> None: ...


def demo_mac(secret: str, amount: int, resource: str, request_id: str) -> str:
    """Compute the demo-scheme MAC. Shared by the fake wallet and fake merchant."""
    payload = f"{amount}:{resource}:{request_id}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


class FakeWallet:
    """In-process wallet that MACs demo-scheme quotes.

    The MAC key never leaves the Paygo process, so a hostile child that calls
    the merchant directly cannot produce a valid ``PAYMENT-SIGNATURE``. Balance
    is intentionally huge: in the demo, the *budget* is the constraint, not
    the wallet. (A separate test covers a wallet that's poorer than the quote.)
    """

    def __init__(self, secret: str, *, balance_microdollars: int = 10**15) -> None:
        self._secret = secret
        self._balance = balance_microdollars

    def address(self) -> str:
        return "paygo:demo:wallet"

    def balance(self) -> int:
        return self._balance

    def authorize_x402(self, requirements: PaymentRequirements, request_id: str) -> dict:
        if requirements.network != DEMO_NETWORK:
            raise UnsupportedPayment(
                f"FakeWallet cannot pay network {requirements.network!r}."
            )
        if requirements.amount > self._balance:
            raise UnsupportedPayment("Wallet balance is lower than the quoted amount.")
        mac = demo_mac(self._secret, requirements.amount, requirements.resource, request_id)
        return {
            "x402Version": 2,
            "accepted": requirements.to_wire(),
            "payload": {
                "scheme": "paygo-demo",
                "mac": mac,
                "request_id": request_id,
                "from": self.address(),
            },
        }

    def revoke_session(self, run_id: str) -> None:
        # The fake holds no per-run provider credentials to tear down.
        return None

"""Coinbase/CDP wallet adapter.

Optional: requires the ``paygo[coinbase]`` extra (``cdp-sdk`` + ``x402``).
Secrets are read from the environment, never from ``config.toml``. The private
key never leaves Coinbase's TEE; Paygo only ever sees an address and signed
x402 payloads.

Provisioning (``paygo init --wallet coinbase``) is the usability path:
create-or-fetch a named account, print the address, optionally faucet testnet
USDC. Live ``authorize_x402`` for Base quotes is implemented when the extra is
installed; treating that path as proven waits on a live merchant (M4).
"""

from __future__ import annotations

import asyncio
from typing import Any

from paygo.credentials import CDP_REQUIRED_ENV_VARS, present
from paygo.errors import PaymentFailed, UnsupportedPayment
from paygo.x402 import PaymentRequirements

# CAIP-2 network ids the Coinbase adapter will sign for.
BASE_MAINNET = "eip155:8453"
BASE_SEPOLIA = "eip155:84532"

# USDC has 6 decimals; Paygo accounts in microdollars — 1:1 with atomic USDC.
USDC_DECIMALS = 6

_CDP_NETWORK = {
    "base": "base",
    "base-sepolia": "base-sepolia",
    BASE_MAINNET: "base",
    BASE_SEPOLIA: "base-sepolia",
}


def missing_cdp_credentials(env: dict[str, str] | None = None) -> list[str]:
    """Return the CDP env vars that are unset — the init/doctor checklist."""
    have = set(present(CDP_REQUIRED_ENV_VARS, env))
    return [name for name in CDP_REQUIRED_ENV_VARS if name not in have]


def coinbase_extra_installed() -> bool:
    """True when ``cdp-sdk`` is importable (the ``paygo[coinbase]`` extra)."""
    try:
        import cdp  # noqa: F401
    except ImportError:
        return False
    return True


def _require_cdp() -> None:
    if coinbase_extra_installed():
        return
    raise PaymentFailed(
        "Coinbase support is an optional extra. Install it with:\n"
        "  uv tool install --force 'paygo[coinbase]'\n"
        "  # from a clone: uv tool install --force '.[coinbase]'"
    )


class CoinbaseWallet:
    """CDP-managed wallet. No private key is stored in Paygo."""

    def __init__(
        self,
        *,
        account_name: str = "paygo",
        network: str = "base-sepolia",
        address: str = "",
    ) -> None:
        self.account_name = account_name
        self.network = network
        self._address = address

    def address(self) -> str:
        if self._address:
            return self._address
        self._address = asyncio.run(self._ensure_account())
        return self._address

    def balance(self) -> int:
        """USDC balance in microdollars (USDC has 6 decimals, so 1:1)."""
        return asyncio.run(self._usdc_balance())

    def request_faucet(self) -> str:
        """Claim testnet USDC on Base Sepolia. Returns a tx hash/id string."""
        if _CDP_NETWORK.get(self.network, self.network) != "base-sepolia":
            raise UnsupportedPayment("Faucet is only available on base-sepolia.")
        return asyncio.run(self._faucet())

    def authorize_x402(self, requirements: PaymentRequirements, request_id: str) -> dict:
        if requirements.network not in {BASE_MAINNET, BASE_SEPOLIA, "base", "base-sepolia"}:
            raise UnsupportedPayment(f"CoinbaseWallet cannot pay network {requirements.network!r}.")
        return asyncio.run(self._sign(requirements, request_id))

    def revoke_session(self, run_id: str) -> None:
        return None

    # --- async CDP calls -------------------------------------------------

    async def _ensure_account(self) -> str:
        _require_cdp()
        from cdp import CdpClient

        try:
            async with CdpClient() as cdp:
                account = await cdp.evm.get_or_create_account(name=self.account_name)
                return account.address
        except PaymentFailed:
            raise
        except Exception as exc:
            raise PaymentFailed(f"Could not create or load CDP account: {exc}") from exc

    async def _usdc_balance(self) -> int:
        _require_cdp()
        from cdp import CdpClient

        address = self._address or await self._ensure_account()
        self._address = address
        network = _CDP_NETWORK.get(self.network, self.network)
        async with CdpClient() as cdp:
            try:
                page = await cdp.evm.list_token_balances(address=address, network=network)
            except Exception as exc:
                raise PaymentFailed(f"Could not fetch USDC balance: {exc}") from exc
        return usdc_from_balances(page)

    async def _faucet(self) -> str:
        _require_cdp()
        from cdp import CdpClient

        address = self._address or await self._ensure_account()
        self._address = address
        async with CdpClient() as cdp:
            try:
                tx = await cdp.evm.request_faucet(
                    address=address, network="base-sepolia", token="usdc"
                )
            except Exception as exc:
                raise PaymentFailed(f"Faucet request failed: {exc}") from exc
            return str(tx)

    async def _sign(self, requirements: PaymentRequirements, request_id: str) -> dict[str, Any]:
        # request_id is part of WalletAdapter; the x402 client generates its
        # own nonce inside the EIP-3009 authorization.
        del request_id
        _require_cdp()
        try:
            from cdp import CdpClient
            from cdp.evm_local_account import EvmLocalAccount
            from x402 import x402Client
            from x402.mechanisms.evm import EthAccountSigner
            from x402.mechanisms.evm.exact import ExactEvmScheme
        except ImportError as exc:
            raise PaymentFailed(
                "Live x402 signing needs the Coinbase extra:\n"
                "  uv tool install --force 'paygo[coinbase]'"
            ) from exc

        caip = (
            requirements.network
            if requirements.network.startswith("eip155:")
            else (BASE_SEPOLIA if self.network == "base-sepolia" else BASE_MAINNET)
        )
        try:
            async with CdpClient() as cdp:
                account = await cdp.evm.get_or_create_account(name=self.account_name)
                signer = EthAccountSigner(EvmLocalAccount(account))
                client = x402Client()
                client.register(caip, ExactEvmScheme(signer))
                # The x402 client wants the 402 PaymentRequired object. We pass
                # the wire form our buyer already parsed; current 2.x releases
                # accept dicts and raise TypeError if they do not.
                payment_required = {
                    "x402Version": 2,
                    "resource": {"url": requirements.resource},
                    "accepts": [requirements.to_wire()],
                }
                payload = await client.create_payment_payload(payment_required)
        except PaymentFailed:
            raise
        except Exception as exc:
            raise PaymentFailed(f"Could not sign x402 payload: {exc}") from exc
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if isinstance(payload, dict):
            return payload
        return dict(payload)


def usdc_from_balances(page: Any) -> int:
    """Sum USDC balances from a CDP ``list_token_balances`` response.

    CDP returns ``{token: {symbol}, amount: {amount, decimals}}``. Atomic USDC
    (6 decimals) maps 1:1 onto Paygo microdollars.
    """
    items = getattr(page, "balances", None)
    if items is None and isinstance(page, dict):
        items = page.get("balances") or page.get("data") or []
    if items is None:
        items = getattr(page, "data", None) or []

    total = 0
    for item in items or []:
        symbol = _nested(item, "token", "symbol") or _nested(item, "symbol") or ""
        if str(symbol).upper() not in {"USDC", "USDC.E"}:
            continue
        raw = _nested(item, "amount", "amount")
        decimals = _nested(item, "amount", "decimals")
        if raw is None:
            raw = _nested(item, "amount") or _nested(item, "value") or 0
        try:
            atomic = int(raw)
            scale = int(decimals) if decimals is not None else USDC_DECIMALS
        except (TypeError, ValueError):
            continue
        total += _to_microdollars(atomic, scale)
    return total


def _to_microdollars(atomic: int, decimals: int) -> int:
    """Convert a token's atomic units into microdollars (6 decimal USD)."""
    if decimals == USDC_DECIMALS:
        return atomic
    if decimals > USDC_DECIMALS:
        return atomic // (10 ** (decimals - USDC_DECIMALS))
    return atomic * (10 ** (USDC_DECIMALS - decimals))


def _nested(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            cur = getattr(cur, key, None)
    return cur

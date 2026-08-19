"""Known credential taxonomy — one file to update when providers churn.

Wallet/admin credentials are **always** stripped from the child (the README
promise: the process never receives wallet signing material). Provider keys
are reported by ``paygo doctor`` and stripped only under ``paygo exec --strict``.
Harness-specific auth knowledge belongs here (or later in ``harness/``), never
in the budget kernel.
"""

from __future__ import annotations

import os

# Inference/cloud keys a child could use to spend *outside* Paygo.
# Names come from the harnesses in HARNESSES.md. Update this tuple when they churn.
PROVIDER_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "DEEPSEEK_API_KEY",
    "NOUS_API_KEY",
)

# Wallet/admin credentials that must never reach the child under strict mode.
# Current CDP names come first; older aliases stay so they get scrubbed too.
WALLET_ENV_VARS: tuple[str, ...] = (
    "CDP_API_KEY_ID",
    "CDP_API_KEY_SECRET",
    "CDP_WALLET_SECRET",
    "CDP_API_KEY_NAME",
    "CDP_API_KEY_PRIVATE_KEY",
    "COINBASE_API_KEY",
    "COINBASE_API_SECRET",
    "WALLET_PRIVATE_KEY",
)

# The three values `paygo init --wallet coinbase` actually needs.
CDP_REQUIRED_ENV_VARS: tuple[str, ...] = (
    "CDP_API_KEY_ID",
    "CDP_API_KEY_SECRET",
    "CDP_WALLET_SECRET",
)

CDP_PORTAL_API_KEYS = "https://portal.cdp.coinbase.com/access/api"
CDP_PORTAL_WALLET_SECRET = "https://portal.cdp.coinbase.com/wallets/non-custodial/security"


def present(names: tuple[str, ...], env: dict[str, str] | None = None) -> list[str]:
    """Return the names in ``names`` that are set (and non-empty) in ``env``."""
    source = env if env is not None else os.environ
    return [name for name in names if source.get(name)]


def scrub(env: dict[str, str], names: tuple[str, ...] | None = None) -> dict[str, str]:
    """Return a copy of ``env`` with the given names removed.

    Defaults to wallet + provider keys. The runtime always scrubs
    ``WALLET_ENV_VARS``; ``--strict`` additionally scrubs ``PROVIDER_ENV_VARS``.
    """
    drop = set(names if names is not None else (*PROVIDER_ENV_VARS, *WALLET_ENV_VARS))
    return {k: v for k, v in env.items() if k not in drop}

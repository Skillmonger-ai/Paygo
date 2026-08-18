"""Known credential taxonomy — one file to update when providers churn.

`paygo doctor` reports these; `paygo exec --strict` scrubs them from the child
environment. Harness-specific auth knowledge belongs here (or later in
`harness/`), never in the budget kernel.
"""

from __future__ import annotations

import os

# Inference/cloud keys a child could use to spend *outside* Paygo.
PROVIDER_ENV_VARS: tuple[str, ...] = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "MISTRAL_API_KEY",
)

# Wallet/admin credentials that must never reach the child under strict mode.
WALLET_ENV_VARS: tuple[str, ...] = (
    "CDP_API_KEY_NAME",
    "CDP_API_KEY_PRIVATE_KEY",
    "COINBASE_API_KEY",
    "COINBASE_API_SECRET",
    "WALLET_PRIVATE_KEY",
)


def present(names: tuple[str, ...], env: dict[str, str] | None = None) -> list[str]:
    """Return the names in ``names`` that are set (and non-empty) in ``env``."""
    source = env if env is not None else os.environ
    return [name for name in names if source.get(name)]


def scrub(env: dict[str, str], names: tuple[str, ...] | None = None) -> dict[str, str]:
    """Return a copy of ``env`` with the given names removed.

    Defaults to scrubbing both provider and wallet keys (what ``--strict`` does).
    """
    drop = set(names if names is not None else (*PROVIDER_ENV_VARS, *WALLET_ENV_VARS))
    return {k: v for k, v in env.items() if k not in drop}

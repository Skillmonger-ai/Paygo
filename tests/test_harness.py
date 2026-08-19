"""Harness identity: wrap the CLI the user already has, don't become it."""

from __future__ import annotations

from paygo.credentials import PROVIDER_ENV_VARS, scrub
from paygo.harness import identify


def test_identify_known_binaries() -> None:
    assert identify("codex").binary == "codex"
    assert identify("/usr/local/bin/claude").binary == "claude"
    assert identify("pi -c").binary == "pi"
    assert identify("hermes").nested is True
    assert identify("openclaw gateway").nested is True
    assert identify("codex.exe").binary == "codex"
    assert identify("python my_agent.py") is None
    assert identify("") is None


def test_strict_scrubs_harness_gateway_tokens() -> None:
    env = {
        "ANTHROPIC_AUTH_TOKEN": "gateway",
        "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
        "CODEX_API_KEY": "ck",
        "XAI_API_KEY": "x",
        "PATH": "/bin",
    }
    cleaned = scrub(env, PROVIDER_ENV_VARS)
    assert "ANTHROPIC_AUTH_TOKEN" not in cleaned
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in cleaned
    assert "CODEX_API_KEY" not in cleaned
    assert cleaned["PATH"] == "/bin"

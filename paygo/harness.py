"""Known agent harnesses — identity and honest doctor copy.

Paygo does not configure Codex, Claude, Pi, OpenClaw, or Hermes. It wraps the
binary the user already runs. This file is the lookup table ``paygo doctor --``
uses so leftover subscription logins are reported instead of implied away.
See ``HARNESSES.md`` for the operational map.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Harness:
    """One PATH CLI Paygo is willing to wrap without learning its planner."""

    binary: str
    product: str
    home: str  # directory under the user home, e.g. ".codex"
    # How that process spends money when Paygo is *not* mediating.
    spend: str
    # How Paygo attaches today (process wrap). Later doors are named, not built.
    attach: str
    # True when this process commonly spawns Codex/Claude with their own auth.
    nested: bool = False


HARNESSES: tuple[Harness, ...] = (
    Harness(
        binary="codex",
        product="OpenAI Codex",
        home=".codex",
        spend="ChatGPT OAuth in ~/.codex and/or OPENAI_API_KEY",
        attach="paygo exec -b 5 -- codex",
    ),
    Harness(
        binary="claude",
        product="Claude Code",
        home=".claude",
        spend="Claude OAuth in ~/.claude and/or ANTHROPIC_API_KEY",
        attach="paygo exec -b 5 -- claude",
    ),
    Harness(
        binary="pi",
        product="Pi",
        home=".pi",
        spend="provider keys / /login credentials under ~/.pi/agent",
        attach="paygo exec -b 5 -- pi",
    ),
    Harness(
        binary="hermes",
        product="Hermes",
        home=".hermes",
        spend="~/.hermes/.env plus any nested Codex/Claude login",
        attach="paygo exec -b 5 -- hermes",
        nested=True,
    ),
    Harness(
        binary="openclaw",
        product="OpenClaw",
        home=".openclaw",
        spend="gateway keys in ~/.openclaw; nested Codex/Claude runtimes",
        attach="paygo exec -b 5 -- openclaw",
        nested=True,
    ),
)

_BY_BINARY = {h.binary: h for h in HARNESSES}


def identify(command: str) -> Harness | None:
    """Return the harness for the first token of ``command``, if known."""
    if not command.strip():
        return None
    name = Path(command.split()[0]).name
    # Windows users may pass codex.cmd / claude.exe; strip a final suffix.
    stem = name[:-4] if name.lower().endswith(".exe") else name
    stem = stem[:-4] if stem.lower().endswith(".cmd") else stem
    return _BY_BINARY.get(stem)


def on_path(harness: Harness) -> str | None:
    """Absolute path of the binary if it is on PATH."""
    found = shutil.which(harness.binary)
    return found


def home_exists(harness: Harness) -> bool:
    """True when the harness has already been set up on this machine."""
    return (Path.home() / harness.home).exists()

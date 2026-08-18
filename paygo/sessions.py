"""Run-scoped session tokens.

The child process is assumed hostile (SYSTEM_DESIGN.md, "Security & threat
model"). It never receives wallet keys or admin tokens — only a short-lived,
run-scoped bearer token that authorizes read access to *its own* run's balance
and transactions through the local service.

Only a hash of each token is persisted, so a leaked ledger cannot be replayed to
impersonate a child. Revocation is per-run and immediate: once a run ends, every
token minted for it stops verifying.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from pathlib import Path

from paygo import ledger


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash(token: str) -> str:
    # SHA-256 is sufficient here: tokens are 256 bits of CSPRNG output, so there
    # is nothing to brute-force and no need for a slow password hash.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionManager:
    """Mints, verifies, and revokes run-scoped bearer tokens."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        ledger.init_db(db_path)

    def mint(self, run_id: str) -> str:
        """Create a new token for a run and return the plaintext exactly once.

        The plaintext is never stored; the caller must inject it into the child
        environment immediately. Subsequent lookups match on the stored hash.
        """
        token = secrets.token_urlsafe(32)
        conn = ledger.connect(self._db_path)
        try:
            conn.execute(
                "INSERT INTO sessions (id, run_id, token_hash, created_at, "
                "revoked_at) VALUES (?, ?, ?, ?, NULL)",
                (f"ses_{secrets.token_hex(6)}", run_id, _hash(token), _now()),
            )
        finally:
            conn.close()
        return token

    def verify(self, token: str) -> str | None:
        """Return the run id for a valid, non-revoked token, else ``None``.

        Returning ``None`` (rather than raising) lets the HTTP layer map every
        failure — unknown, malformed, or revoked — to a single opaque 401 without
        leaking which case occurred.
        """
        if not token:
            return None
        conn = ledger.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT run_id FROM sessions WHERE token_hash = ? "
                "AND revoked_at IS NULL",
                (_hash(token),),
            ).fetchone()
        finally:
            conn.close()
        return row["run_id"] if row else None

    def revoke_run(self, run_id: str) -> int:
        """Revoke every session for a run; returns how many were revoked."""
        conn = ledger.connect(self._db_path)
        try:
            cur = conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE run_id = ? "
                "AND revoked_at IS NULL",
                (_now(), run_id),
            )
            return cur.rowcount
        finally:
            conn.close()

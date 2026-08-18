"""The budget kernel: atomic reserve / settle / release / topup / revoke.

This is the most important module in the project. It enforces, under
concurrency, the single invariant that defines Paygo::

    settled + active_reserved <= authorized

Every mutation that touches that inequality runs inside a ``BEGIN IMMEDIATE``
SQLite transaction so the write lock is held for the whole read-modify-write.
That serializes concurrent reservers and makes it impossible for two of them to
both "see" the same remaining budget and both succeed.
"""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from paygo import ledger
from paygo.errors import (
    BudgetExceeded,
    InvalidAmount,
    InvalidTransition,
    ReservationNotFound,
    RunNotActive,
    RunNotFound,
)

# Run lifecycle statuses (see SYSTEM_DESIGN.md, "Data model").
RUN_ACTIVE = "ACTIVE"
RUN_EXHAUSTED = "EXHAUSTED"
RUN_REVOKED = "REVOKED"
RUN_COMPLETED = "COMPLETED"
RUN_FAILED = "FAILED"

# Reservation statuses.
RES_RESERVED = "RESERVED"
RES_SETTLED = "SETTLED"
RES_RELEASED = "RELEASED"
RES_FAILED = "FAILED"

# A run must be ACTIVE to accept new spend.
_SPENDABLE_STATUSES = {RUN_ACTIVE}


def _now() -> str:
    """UTC ISO-8601 timestamp; UTC keeps the ledger timezone-unambiguous."""
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str, nbytes: int = 4) -> str:
    return f"{prefix}_{secrets.token_hex(nbytes)}"


@dataclass(frozen=True)
class RunSnapshot:
    """A point-in-time view of a run's budget accounting."""

    id: str
    command: str
    status: str
    authorized: int
    settled: int
    reserved: int  # sum of currently-active (RESERVED) reservations
    created_at: str
    ended_at: str | None

    @property
    def available(self) -> int:
        """Microdollars still authorizable: authorized - settled - reserved."""
        return self.authorized - self.settled - self.reserved


class BudgetEngine:
    """Owns the ledger and performs all authoritative budget mutations."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        # Ensure the schema exists so the engine is usable immediately; this is
        # idempotent (all DDL is IF NOT EXISTS).
        ledger.init_db(db_path)

    @contextmanager
    def _immediate_txn(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside a ``BEGIN IMMEDIATE`` transaction.

        The write lock is acquired up front (IMMEDIATE) rather than lazily, which
        is what serializes concurrent reservations. Commits on success, rolls
        back on any exception, and always closes the per-operation connection.
        """
        conn = ledger.connect(self._db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    # --- run lifecycle ---------------------------------------------------

    def create_run(self, command: str, authorized_microdollars: int) -> str:
        """Create an ACTIVE run with the given authorization ceiling."""
        if authorized_microdollars <= 0:
            raise InvalidAmount("Authorized budget must be positive.")
        run_id = _new_id("pg", 3)
        with self._immediate_txn() as conn:
            conn.execute(
                "INSERT INTO runs (id, created_at, ended_at, command, "
                "authorized_microdollars, status) VALUES (?, ?, NULL, ?, ?, ?)",
                (run_id, _now(), command, authorized_microdollars, RUN_ACTIVE),
            )
        return run_id

    def topup(self, run_id: str, amount_microdollars: int) -> RunSnapshot:
        """Raise a run's authorization ceiling atomically.

        This is a user-side administrative action only (README "Top-ups"); the
        child run token must never reach this path. It merely changes the ceiling
        and does not move funds.
        """
        if amount_microdollars <= 0:
            raise InvalidAmount("Top-up amount must be positive.")
        with self._immediate_txn() as conn:
            run = self._load_run(conn, run_id)
            if run["status"] not in _SPENDABLE_STATUSES:
                raise RunNotActive(
                    f"Run {run_id} is {run['status']}, cannot top up."
                )
            conn.execute(
                "UPDATE runs SET authorized_microdollars = "
                "authorized_microdollars + ? WHERE id = ?",
                (amount_microdollars, run_id),
            )
            return self._snapshot(conn, run_id)

    def finalize(self, run_id: str, status: str) -> RunSnapshot:
        """Close out a still-ACTIVE run when its process exits.

        Only transitions from ACTIVE, so it never clobbers a run the user already
        stopped (REVOKED). ``status`` should be COMPLETED (clean child exit) or
        FAILED (non-zero exit / signal).
        """
        if status not in (RUN_COMPLETED, RUN_FAILED):
            raise InvalidTransition(f"Cannot finalize to status {status!r}.")
        with self._immediate_txn() as conn:
            run = self._load_run(conn, run_id)
            if run["status"] == RUN_ACTIVE:
                conn.execute(
                    "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
                    (status, _now(), run_id),
                )
            return self._snapshot(conn, run_id)

    def revoke(self, run_id: str) -> RunSnapshot:
        """Mark a run REVOKED so no further reservations are authorized."""
        with self._immediate_txn() as conn:
            run = self._load_run(conn, run_id)
            # Idempotent-ish: revoking an already-terminal run is a no-op error
            # only if it was never spendable; revoking ACTIVE ends it.
            if run["status"] in _SPENDABLE_STATUSES:
                conn.execute(
                    "UPDATE runs SET status = ?, ended_at = ? WHERE id = ?",
                    (RUN_REVOKED, _now(), run_id),
                )
            return self._snapshot(conn, run_id)

    # --- spend lifecycle -------------------------------------------------

    def reserve(
        self,
        run_id: str,
        max_cost_microdollars: int,
        provider: str = "unknown",
        request_hash: str | None = None,
    ) -> str:
        """Reserve up to ``max_cost`` against a run, or raise ``BudgetExceeded``.

        This is the enforcement point. The whole check-then-insert runs under the
        IMMEDIATE write lock, so ``remaining`` cannot be stale by the time we act
        on it.
        """
        if max_cost_microdollars <= 0:
            raise InvalidAmount("Reservation amount must be positive.")

        with self._immediate_txn() as conn:
            run = self._load_run(conn, run_id)
            if run["status"] not in _SPENDABLE_STATUSES:
                raise RunNotActive(f"Run {run_id} is {run['status']}.")

            settled, active_reserved = self._totals(conn, run_id)
            remaining = run["authorized_microdollars"] - settled - active_reserved
            if max_cost_microdollars > remaining:
                # Denied before any payment is authorized (fail closed).
                raise BudgetExceeded(
                    f"Requested {max_cost_microdollars} exceeds remaining {remaining}.",
                    requested=max_cost_microdollars,
                    remaining=remaining,
                )

            reservation_id = _new_id("res", 6)
            conn.execute(
                "INSERT INTO reservations (id, run_id, provider, request_hash, "
                "reserved_microdollars, settled_microdollars, status, created_at, "
                "settled_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, NULL)",
                (
                    reservation_id,
                    run_id,
                    provider,
                    request_hash,
                    max_cost_microdollars,
                    RES_RESERVED,
                    _now(),
                ),
            )
            return reservation_id

    def settle(
        self,
        reservation_id: str,
        actual_cost_microdollars: int,
        service: str | None = None,
        external_id: str | None = None,
        payment_id: str | None = None,
    ) -> None:
        """Settle a reservation at its actual cost and record a transaction.

        The unused portion (reserved - actual) is freed implicitly: once the
        reservation flips RESERVED -> SETTLED it no longer counts toward
        ``active_reserved``, and only ``actual_cost`` counts toward ``settled``.
        We refuse to settle above the reserved max (fail closed), since a charge
        larger than the authorized reservation would breach the invariant.
        """
        if actual_cost_microdollars < 0:
            raise InvalidAmount("Settlement cost cannot be negative.")

        with self._immediate_txn() as conn:
            res = self._load_reservation(conn, reservation_id)
            if res["status"] != RES_RESERVED:
                raise InvalidTransition(
                    f"Reservation {reservation_id} is {res['status']}, "
                    "cannot settle."
                )
            if actual_cost_microdollars > res["reserved_microdollars"]:
                raise InvalidAmount(
                    "Settlement exceeds the reserved amount; refusing to settle."
                )

            conn.execute(
                "UPDATE reservations SET settled_microdollars = ?, status = ?, "
                "settled_at = ? WHERE id = ?",
                (actual_cost_microdollars, RES_SETTLED, _now(), reservation_id),
            )
            conn.execute(
                "INSERT INTO transactions (id, run_id, reservation_id, provider, "
                "service, kind, amount_microdollars, currency, external_id, "
                "payment_id, metadata_json, created_at) VALUES "
                "(?, ?, ?, ?, ?, 'settlement', ?, 'USD', ?, ?, NULL, ?)",
                (
                    _new_id("txn", 6),
                    res["run_id"],
                    reservation_id,
                    res["provider"],
                    service,
                    actual_cost_microdollars,
                    external_id,
                    payment_id,
                    _now(),
                ),
            )

    def release(self, reservation_id: str) -> None:
        """Release an unspent reservation, freeing its held budget."""
        with self._immediate_txn() as conn:
            res = self._load_reservation(conn, reservation_id)
            if res["status"] != RES_RESERVED:
                raise InvalidTransition(
                    f"Reservation {reservation_id} is {res['status']}, "
                    "cannot release."
                )
            conn.execute(
                "UPDATE reservations SET status = ? WHERE id = ?",
                (RES_RELEASED, reservation_id),
            )

    # --- reads -----------------------------------------------------------

    def snapshot(self, run_id: str) -> RunSnapshot:
        """Return the current accounting snapshot for a run."""
        conn = ledger.connect(self._db_path)
        try:
            return self._snapshot(conn, run_id)
        finally:
            conn.close()

    def list_runs(self) -> list[RunSnapshot]:
        """Return snapshots for every run, newest first."""
        conn = ledger.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT id FROM runs ORDER BY created_at DESC"
            ).fetchall()
            return [self._snapshot(conn, row["id"]) for row in rows]
        finally:
            conn.close()

    def list_transactions(self, run_id: str) -> list[dict]:
        """Return settled transactions for a run, oldest first."""
        conn = ledger.connect(self._db_path)
        try:
            # Confirm the run exists so callers get RunNotFound, not empty output.
            self._load_run(conn, run_id)
            rows = conn.execute(
                "SELECT * FROM transactions WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def find_reservation_by_hash(self, run_id: str, request_hash: str) -> dict | None:
        """Return the newest reservation for a client ``request_id`` hash, if any.

        Used by the x402 buyer for sequential idempotency: a retried request
        with the same id must not become a second charge.
        """
        conn = ledger.connect(self._db_path)
        try:
            row = conn.execute(
                "SELECT * FROM reservations WHERE run_id = ? AND request_hash = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id, request_hash),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # --- internals -------------------------------------------------------

    def _load_run(self, conn, run_id: str):
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFound(f"No such run: {run_id}")
        return row

    def _load_reservation(self, conn, reservation_id: str):
        row = conn.execute(
            "SELECT * FROM reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
        if row is None:
            raise ReservationNotFound(f"No such reservation: {reservation_id}")
        return row

    def _totals(self, conn, run_id: str) -> tuple[int, int]:
        """Return (settled, active_reserved) microdollar totals for a run."""
        settled = conn.execute(
            "SELECT COALESCE(SUM(settled_microdollars), 0) AS s FROM reservations "
            "WHERE run_id = ? AND status = ?",
            (run_id, RES_SETTLED),
        ).fetchone()["s"]
        reserved = conn.execute(
            "SELECT COALESCE(SUM(reserved_microdollars), 0) AS r FROM reservations "
            "WHERE run_id = ? AND status = ?",
            (run_id, RES_RESERVED),
        ).fetchone()["r"]
        return int(settled), int(reserved)

    def _snapshot(self, conn, run_id: str) -> RunSnapshot:
        run = self._load_run(conn, run_id)
        settled, reserved = self._totals(conn, run_id)
        return RunSnapshot(
            id=run["id"],
            command=run["command"],
            status=run["status"],
            authorized=run["authorized_microdollars"],
            settled=settled,
            reserved=reserved,
            created_at=run["created_at"],
            ended_at=run["ended_at"],
        )

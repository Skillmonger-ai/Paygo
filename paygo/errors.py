"""Typed errors for the budget kernel.

Distinct exception types let the CLI and (later) the HTTP layer map failures to
clear exit codes / status codes, and let tests assert on precise failure modes
rather than matching error strings.
"""

from __future__ import annotations


class PaygoError(Exception):
    """Base class for all Paygo domain errors."""


class BudgetExceeded(PaygoError):
    """A reservation was denied because it would breach the run ceiling.

    Raised by :meth:`paygo.budget.BudgetEngine.reserve` when
    ``requested > authorized - settled - active_reserved``. This is the primary
    enforcement point of the whole product, so it is its own type. ``requested``
    and ``remaining`` are microdollars so the HTTP layer can tell an agent
    exactly why it was denied (and how large the hold would have been).
    """

    def __init__(
        self, message: str, *, requested: int = 0, remaining: int = 0
    ) -> None:
        super().__init__(message)
        self.requested = requested
        self.remaining = remaining


class RunNotFound(PaygoError):
    """The referenced run id does not exist in the ledger."""


class RunNotActive(PaygoError):
    """An operation requires an ACTIVE run but the run is in a terminal state."""


class ReservationNotFound(PaygoError):
    """The referenced reservation id does not exist."""


class InvalidTransition(PaygoError):
    """A reservation/run was asked to move between incompatible states.

    For example: settling an already-released reservation, or releasing one that
    has already settled. Guarding these keeps the ledger's accounting sound.
    """


class InvalidAmount(PaygoError):
    """A monetary amount was zero, negative, or otherwise unusable."""


class UnsupportedPayment(PaygoError):
    """The 402 quote used a scheme, network, or asset Paygo will not pay."""


class PaymentFailed(PaygoError):
    """The merchant probe, payment retry, or wallet authorization failed."""

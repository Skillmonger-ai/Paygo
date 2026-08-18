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
    enforcement point of the whole product, so it is its own type.
    """


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

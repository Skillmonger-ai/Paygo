"""Paygo: give software an allowance.

Paygo puts a hard dollar budget around an autonomous process. This package
currently implements Milestone 1 from ``PROJECT_PLAN.md``: the budget kernel
(integer-microdollar accounting, a SQLite ledger, and atomic reserve/settle/
release/topup/revoke operations) plus the read/admin CLI surface.

The central invariant, enforced under concurrency, is::

    settled + active_reserved <= authorized
"""

__all__ = ["__version__"]

__version__ = "0.0.1"

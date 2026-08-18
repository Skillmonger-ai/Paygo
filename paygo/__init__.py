"""Paygo: give software an allowance.

Paygo puts a hard dollar budget around an autonomous process. This package
implements the budget kernel (integer-microdollar accounting, a SQLite ledger,
and atomic reserve/settle/release/topup/revoke operations) plus the CLI and the
run-scoped process wrapper. See ``SYSTEM_DESIGN.md`` and
``IMPLEMENTATION_PLAN.md`` for architecture and roadmap.

The central invariant, enforced under concurrency, is::

    settled + active_reserved <= authorized
"""

__all__ = ["__version__"]

__version__ = "0.0.1"

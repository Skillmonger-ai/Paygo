"""Money as integer microdollars.

Authoritative accounting must never use floats (PROJECT_PLAN.md, rule 6):
binary floating point cannot represent most decimal cents exactly, so repeated
addition drifts. Paygo therefore stores and computes every amount as an integer
number of *microdollars*::

    $1.00 == 1_000_000 microdollars

Parsing accepts human input (e.g. ``"5"``, ``"0.05"``) via :class:`Decimal` for
exact base-10 rounding, then converts to ``int`` once at the boundary.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from paygo.errors import InvalidAmount

MICRODOLLARS_PER_DOLLAR = 1_000_000


def parse_dollars(value: str | int | float | Decimal) -> int:
    """Convert a user-supplied dollar amount to integer microdollars.

    Floats are accepted for CLI convenience but routed through ``Decimal(str(x))``
    so we round the *decimal* the user meant, not its float approximation.
    Rejects non-positive or unparseable amounts with :class:`InvalidAmount`.
    """
    try:
        # str() first: Decimal(0.1) keeps the float error, Decimal("0.1") does not.
        dollars = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise InvalidAmount(f"Not a valid amount: {value!r}") from exc

    if dollars <= 0:
        raise InvalidAmount(f"Amount must be positive: {value!r}")

    micros = (dollars * MICRODOLLARS_PER_DOLLAR).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return int(micros)


def format_dollars(microdollars: int) -> str:
    """Render integer microdollars as a ``$X.XX`` string for display.

    Uses Decimal division so the two-decimal presentation is exact.
    """
    dollars = (Decimal(microdollars) / MICRODOLLARS_PER_DOLLAR).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return f"${dollars}"

"""Money parsing/formatting: exactness and rejection of bad input."""

from __future__ import annotations

import pytest

from paygo.errors import InvalidAmount
from paygo.money import format_dollars, parse_dollars


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("5", 5_000_000),
        ("0.05", 50_000),
        ("0.10", 100_000),
        (1, 1_000_000),
        (0.1, 100_000),  # float is routed through Decimal(str(x)) => exact
        ("1.005", 1_005_000),
    ],
)
def test_parse_dollars(value, expected) -> None:
    assert parse_dollars(value) == expected


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "", "-0.01"])
def test_parse_dollars_rejects(bad) -> None:
    with pytest.raises(InvalidAmount):
        parse_dollars(bad)


@pytest.mark.parametrize(
    ("micros", "expected"),
    [(5_000_000, "$5.00"), (50_000, "$0.05"), (0, "$0.00"), (4_510_000, "$4.51")],
)
def test_format_dollars(micros, expected) -> None:
    assert format_dollars(micros) == expected

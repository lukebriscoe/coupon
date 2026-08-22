from __future__ import annotations

import pytest

from football.models import to_fractional


@pytest.mark.parametrize("decimal,expected", [
    (2.00, "EVS"),
    (1.50, "1/2"),
    (1.51, "1/2"),      # snapped — never "51/100"
    (1.53, "8/15"),
    (1.78, "4/5"),
    (2.50, "6/4"),
    (3.00, "2/1"),
    (3.03, "2/1"),
    (5.00, "4/1"),
    (6.37, "11/2"),
    (11.00, "10/1"),
    (34.00, "33/1"),
])
def test_decimal_to_fractional(decimal, expected):
    assert to_fractional(decimal) == expected


@pytest.mark.parametrize("decimal", [1.0, 0.5, 0.0, -3.0])
def test_impossible_prices_render_as_a_dash(decimal):
    assert to_fractional(decimal) == "—"


def test_odds_on_prices_read_as_fractions_below_evens():
    """A 1.20 shot is 1/5, not 5/1 — getting this backwards would be dangerous."""
    numerator, denominator = to_fractional(1.20).split("/")
    assert int(numerator) < int(denominator)


def test_odds_against_prices_read_as_fractions_above_evens():
    numerator, denominator = to_fractional(4.00).split("/")
    assert int(numerator) > int(denominator)


def test_ladder_is_monotonic():
    """Longer decimal odds must never render as a shorter fraction."""
    from football.models import _ODDS_LADDER

    values = [n / d for n, d in _ODDS_LADDER]
    assert values == sorted(values)

"""Margin removal.

A bookmaker's three prices on a match imply probabilities summing to more than
100%. That excess is the overround — Sky Bet's margin. Comparing raw implied
probabilities across fixtures is meaningless because each match is juiced by a
different amount, so everything downstream works on de-vigged probabilities.
"""
from __future__ import annotations

import math

from football.models import (
    MARKET_DOUBLE_CHANCE,
    MARKET_H2H,
    SOURCE_DERIVED,
    Fixture,
    Selection,
)


def implied(odds: float) -> float:
    """Raw implied probability of a decimal price, margin included."""
    return 1.0 / odds


def overround(prices: dict[str, float]) -> float:
    """Total implied probability. 1.05 means a 5% margin."""
    return sum(implied(o) for o in prices.values())


def proportional(prices: dict[str, float]) -> dict[str, float]:
    """Scale every implied probability down by the same factor.

    Simple and stable. It slightly over-rates longshots, because bookmakers
    load more margin onto them than onto favourites.
    """
    total = overround(prices)
    if total <= 0:
        return {k: 0.0 for k in prices}
    return {k: implied(o) / total for k, o in prices.items()}


def shin(prices: dict[str, float], tolerance: float = 1e-9,
         max_iterations: int = 100) -> dict[str, float]:
    """Shin's method — removes margin assuming some bettors are better informed.

    Takes more margin off longshots than favourites, which matches how books
    actually price. Solves for the insider-trading proportion z by bisection.
    """
    total = overround(prices)
    if total <= 1.0 or len(prices) < 2:
        return proportional(prices)

    pi = {k: implied(o) for k, o in prices.items()}

    def probabilities(z: float) -> dict[str, float]:
        if z <= 0:
            return {k: v / total for k, v in pi.items()}
        return {
            k: (math.sqrt(z * z + 4.0 * (1.0 - z) * (v * v) / total) - z)
               / (2.0 * (1.0 - z))
            for k, v in pi.items()
        }

    low, high = 0.0, 0.99
    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        if sum(probabilities(mid).values()) > 1.0:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break

    result = probabilities((low + high) / 2.0)
    # Guard against float drift so the probabilities always sum to exactly 1.
    scale = sum(result.values())
    return {k: v / scale for k, v in result.items()} if scale else result


METHODS = {"proportional": proportional, "shin": shin}


def fair_probabilities(prices: dict[str, float],
                       method: str = "proportional") -> dict[str, float]:
    """De-vig a set of prices with the configured method."""
    if not prices:
        return {}
    fn = METHODS.get(method)
    if fn is None:
        raise ValueError(
            f"Unknown de-vig method {method!r}. Choose from {sorted(METHODS)}."
        )
    return fn(prices)


def derived_double_chance(fixture: Fixture) -> dict[str, float]:
    """Estimate Double Chance prices when the market isn't in the API response.

    The fair probability of "Home or Draw" is just p(home) + p(draw). Converting
    that straight to a price would give a margin-free quote Sky Bet would never
    offer, so the fixture's own overround is applied back on top. These are
    estimates, and every Selection built from them is flagged as derived.
    """
    probs = fixture.fair_probabilities
    if not probs:
        return {}

    home, away, draw = fixture.home_team, fixture.away_team, fixture.draw_key
    if not all(k in probs for k in (home, away, draw)):
        return {}

    margin = overround(fixture.h2h_prices) or 1.0
    combos = {
        f"{home} or {draw}": probs[home] + probs[draw],
        f"{away} or {draw}": probs[away] + probs[draw],
        f"{home} or {away}": probs[home] + probs[away],
    }
    return {
        label: round(1.0 / (p * margin), 2)
        for label, p in combos.items()
        if 0.0 < p * margin < 1.0
    }


def double_chance_probability(fixture: Fixture, label: str) -> float | None:
    """True probability of a Double Chance outcome, from the h2h fair prices.

    Derived from the match-result probabilities rather than by de-vigging the
    Double Chance market itself, because that market's three outcomes sum to
    2.0 and providers disagree on how they name them. Returns None when the
    label can't be matched to a pair of results.
    """
    probs = fixture.fair_probabilities
    if not probs:
        return None

    normalised = label.casefold()
    covered = {
        key for key in (fixture.home_team, fixture.away_team, fixture.draw_key)
        if key.casefold() in normalised
    }
    # A Double Chance outcome always covers exactly two of the three results.
    if len(covered) != 2:
        return None
    return min(sum(probs.get(key, 0.0) for key in covered), 1.0)


def apply(fixture: Fixture, method: str = "proportional") -> Fixture:
    """De-vig a fixture's prices and build its Selections. Mutates in place."""
    if not fixture.h2h_prices:
        return fixture

    fixture.overround = overround(fixture.h2h_prices)
    fixture.fair_probabilities = fair_probabilities(fixture.h2h_prices, method)

    selections: list[Selection] = []

    def add(market: str, label: str, odds: float, probability: float,
            source: str) -> None:
        selections.append(Selection(
            fixture_id=fixture.id,
            league_key=fixture.league_key,
            league_short=fixture.league_short,
            home_team=fixture.home_team,
            away_team=fixture.away_team,
            kickoff=fixture.kickoff,
            market=market,
            label=label,
            odds=odds,
            fair_probability=probability,
            price_source=source,
        ))

    for outcome, price in fixture.h2h_prices.items():
        add(MARKET_H2H, outcome, price,
            fixture.fair_probabilities.get(outcome, 0.0), fixture.price_source)

    # Real Double Chance prices if the API returned them, estimates otherwise.
    dc_prices = fixture.double_chance_prices
    dc_source = fixture.price_source
    if not dc_prices:
        dc_prices = derived_double_chance(fixture)
        dc_source = SOURCE_DERIVED

    for outcome, price in dc_prices.items():
        probability = double_chance_probability(fixture, outcome)
        if probability is None:
            # Can't tell which results the label covers — skip rather than guess.
            continue
        add(MARKET_DOUBLE_CHANCE, outcome, price, probability, dc_source)

    fixture.selections = selections
    return fixture

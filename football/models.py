"""Core data types for the 15:00 card.

A Fixture is one match. It carries the raw Sky Bet prices and, once de-vigged,
a list of Selections — the individual things you could actually back. Bets are
built by combining Selections from *different* fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Market identifiers, matching The Odds API's own keys.
MARKET_H2H = "h2h"
MARKET_DOUBLE_CHANCE = "double_chance"

# Where a price came from. Surfaced in the UI so a substituted price is never
# mistaken for a real Sky Bet quote.
SOURCE_SKYBET = "skybet"
SOURCE_MEDIAN = "market median"
SOURCE_DERIVED = "derived"
# Demo-mode only: an invented price, used so the UI can be explored without a
# key. Rendered with a loud warning — never treat one as a real quote.
SOURCE_ILLUSTRATIVE = "illustrative"


# The fractions a UK bookmaker actually prints, shortest to longest. Converting
# decimals straight to fractions gives things like 51/100, which no board ever
# shows — prices are quoted on this ladder, so display snaps to it.
_ODDS_LADDER: tuple[tuple[int, int], ...] = (
    (1, 100), (1, 50), (1, 33), (1, 25), (1, 20), (1, 16), (1, 14), (1, 12),
    (1, 10), (1, 9), (1, 8), (1, 7), (1, 6), (1, 5), (2, 9), (1, 4), (2, 7),
    (3, 10), (1, 3), (4, 11), (2, 5), (4, 9), (1, 2), (8, 15), (4, 7), (8, 13),
    (4, 6), (8, 11), (4, 5), (5, 6), (10, 11), (1, 1), (11, 10), (6, 5), (5, 4),
    (11, 8), (6, 4), (13, 8), (7, 4), (15, 8), (2, 1), (85, 40), (9, 4), (5, 2),
    (11, 4), (3, 1), (10, 3), (7, 2), (4, 1), (9, 2), (5, 1), (11, 2), (6, 1),
    (13, 2), (7, 1), (15, 2), (8, 1), (17, 2), (9, 1), (10, 1), (11, 1), (12, 1),
    (14, 1), (16, 1), (18, 1), (20, 1), (22, 1), (25, 1), (28, 1), (33, 1),
    (40, 1), (50, 1), (66, 1), (80, 1), (100, 1), (150, 1), (200, 1), (250, 1),
    (500, 1), (1000, 1),
)


def to_fractional(decimal_odds: float) -> str:
    """Render decimal odds the way a UK bookmaker would write them.

    Snaps to the nearest rung of the standard odds ladder, so 1.51 reads as
    "1/2" rather than "51/100". Evens is written "EVS". The decimal price is
    always shown alongside, so this is presentation only.
    """
    profit = decimal_odds - 1.0
    if profit <= 0:
        return "—"

    numerator, denominator = min(
        _ODDS_LADDER, key=lambda rung: abs(rung[0] / rung[1] - profit)
    )
    if numerator == denominator:
        return "EVS"
    return f"{numerator}/{denominator}"


@dataclass
class Selection:
    """One backable outcome at one price."""

    fixture_id: str
    league_key: str
    league_short: str
    home_team: str
    away_team: str
    kickoff: datetime
    market: str
    label: str  # e.g. "Everton" or "Everton or Draw"
    odds: float  # decimal
    fair_probability: float  # after margin removal
    price_source: str = SOURCE_SKYBET
    # Double Chance only: whether a drawn match settles this leg as a winner.
    # False marks the "Home or Away" (12) form, which loses on a draw.
    covers_draw: bool = True

    @property
    def fixture_label(self) -> str:
        return f"{self.home_team} v {self.away_team}"

    @property
    def fractional(self) -> str:
        return to_fractional(self.odds)

    @property
    def market_label(self) -> str:
        return "Double Chance" if self.market == MARKET_DOUBLE_CHANCE else "Match Result"

    @property
    def is_derived(self) -> bool:
        return self.price_source != SOURCE_SKYBET

    def expected_value(self, stake: float) -> float:
        """Expected profit/loss on this stake. Negative is the normal case."""
        return round(self.fair_probability * (self.odds - 1.0) * stake
                     - (1.0 - self.fair_probability) * stake, 2)


@dataclass
class Fixture:
    """A single 15:00 kick-off with its prices."""

    id: str
    league_key: str
    league_name: str
    league_short: str
    home_team: str
    away_team: str
    kickoff: datetime
    # Raw decimal prices keyed by outcome name, as quoted.
    h2h_prices: dict[str, float] = field(default_factory=dict)
    double_chance_prices: dict[str, float] = field(default_factory=dict)
    price_source: str = SOURCE_SKYBET
    # Populated by devig.
    fair_probabilities: dict[str, float] = field(default_factory=dict)
    overround: float = 0.0
    selections: list[Selection] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.home_team} v {self.away_team}"

    @property
    def draw_key(self) -> str:
        return "Draw"

    def opponent_of(self, team: str) -> str:
        return self.away_team if team == self.home_team else self.home_team

    def price_for(self, outcome: str) -> float | None:
        return self.h2h_prices.get(outcome)


@dataclass
class BetLeg:
    """A Selection as it appears inside a placed bet."""

    selection: Selection
    result: str = "pending"  # pending | won | lost | void


@dataclass
class Bet:
    """One recommended bet: a named combination of legs at a combined price."""

    kind: str  # banker | underdog_acca | double | treble | fourfold | fivefold
    title: str
    legs: list[BetLeg]
    stake: float
    # Set when the bet cannot be built — the UI shows this instead of a slip.
    unavailable_reason: str | None = None
    # Optional companion suggestion, e.g. a system bet for a huge acca.
    note: str | None = None
    status: str = "pending"  # pending | won | lost
    returns: float = 0.0

    @property
    def is_available(self) -> bool:
        return self.unavailable_reason is None and bool(self.legs)

    @property
    def combined_odds(self) -> float:
        odds = 1.0
        for leg in self.legs:
            odds *= leg.selection.odds
        return round(odds, 2)

    @property
    def combined_fractional(self) -> str:
        return to_fractional(self.combined_odds)

    @property
    def joint_probability(self) -> float:
        """Assumes legs are independent — true enough for separate fixtures."""
        prob = 1.0
        for leg in self.legs:
            prob *= leg.selection.fair_probability
        return prob

    @property
    def potential_return(self) -> float:
        return round(self.stake * self.combined_odds, 2)

    @property
    def potential_profit(self) -> float:
        return round(self.potential_return - self.stake, 2)

    @property
    def expected_value(self) -> float:
        """Expected profit/loss. Bookmaker margin makes this negative on average."""
        return round(self.joint_probability * self.potential_profit
                     - (1.0 - self.joint_probability) * self.stake, 2)

    @property
    def has_derived_price(self) -> bool:
        return any(leg.selection.is_derived for leg in self.legs)


@dataclass
class Slip:
    """Every recommendation for one Saturday."""

    date: str  # ISO date
    generated_at: str
    fixtures: list[Fixture]
    bets: list[Bet]
    stake: float

    @property
    def fixture_count(self) -> int:
        return len(self.fixtures)

    @property
    def available_bets(self) -> list[Bet]:
        return [b for b in self.bets if b.is_available]

"""Shared fixtures. Prices here are invented purely to exercise the maths."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from football import devig  # noqa: E402
from football.models import Fixture  # noqa: E402
from football.odds_client import load_config  # noqa: E402

UK = ZoneInfo("Europe/London")


@pytest.fixture
def config() -> dict:
    return load_config()


def make_fixture(fixture_id: str, home: str, away: str,
                 home_odds: float, draw_odds: float, away_odds: float,
                 league_key: str = "soccer_epl") -> Fixture:
    fixture = Fixture(
        id=fixture_id,
        league_key=league_key,
        league_name="Test League",
        league_short="TL",
        home_team=home,
        away_team=away,
        kickoff=datetime(2026, 8, 22, 15, 0, tzinfo=UK),
        h2h_prices={home: home_odds, "Draw": draw_odds, away: away_odds},
    )
    return devig.apply(fixture)


@pytest.fixture
def even_card() -> list[Fixture]:
    """Three near-even matches — no favourite anywhere, like a real bad card."""
    return [
        make_fixture("f1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30),
        make_fixture("f2", "Ipswich Town", "Sunderland", 2.78, 3.31, 2.68),
        make_fixture("f3", "Nottingham Forest", "Leeds United", 2.32, 3.30, 3.15),
    ]


@pytest.fixture
def mismatch_card() -> list[Fixture]:
    """A card with clear favourites and several 4/1+ underdogs."""
    return [
        make_fixture("m1", "Derby County", "Cardiff City", 1.53, 3.90, 5.75),
        make_fixture("m2", "Preston North End", "Wolves", 5.20, 3.80, 1.68),
        make_fixture("m3", "Southampton", "Stoke City", 1.72, 3.70, 5.20),
        make_fixture("m4", "West Ham United", "Charlton Athletic", 1.44, 4.50, 7.00),
        make_fixture("m5", "Swansea City", "Sheffield United", 2.80, 3.30, 2.65),
        make_fixture("m6", "Wrexham", "Watford", 3.20, 3.45, 2.25),
    ]

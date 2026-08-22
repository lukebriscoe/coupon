from __future__ import annotations

import pytest

from football import devig
from football.models import MARKET_DOUBLE_CHANCE, MARKET_H2H, SOURCE_DERIVED
from tests.conftest import make_fixture

EVERTON = {"Everton": 2.23, "Draw": 3.35, "Crystal Palace": 3.30}


def test_overround_measures_the_margin():
    assert devig.overround(EVERTON) == pytest.approx(1.0499, abs=1e-4)


@pytest.mark.parametrize("method", ["proportional", "shin"])
def test_fair_probabilities_sum_to_one(method):
    probs = devig.fair_probabilities(EVERTON, method)
    assert sum(probs.values()) == pytest.approx(1.0, abs=1e-9)


def test_proportional_matches_hand_calculation():
    probs = devig.fair_probabilities(EVERTON, "proportional")
    assert probs["Everton"] == pytest.approx(0.4271, abs=1e-4)
    assert probs["Draw"] == pytest.approx(0.2843, abs=1e-4)
    assert probs["Crystal Palace"] == pytest.approx(0.2886, abs=1e-4)


def test_devig_always_lowers_the_implied_probability():
    """Removing margin can only make a selection look less likely, never more."""
    probs = devig.fair_probabilities(EVERTON, "proportional")
    for outcome, odds in EVERTON.items():
        assert probs[outcome] < 1.0 / odds


def test_shin_takes_more_margin_off_the_longshot():
    """Shin's whole point: books load more juice onto outsiders."""
    prices = {"Favourite": 1.20, "Draw": 6.00, "Longshot": 15.00}
    proportional = devig.fair_probabilities(prices, "proportional")
    shin = devig.fair_probabilities(prices, "shin")

    assert shin["Longshot"] < proportional["Longshot"]
    assert shin["Favourite"] > proportional["Favourite"]


def test_unknown_method_is_rejected():
    with pytest.raises(ValueError, match="Unknown de-vig method"):
        devig.fair_probabilities(EVERTON, "nonsense")


def test_empty_prices_do_not_explode():
    assert devig.fair_probabilities({}, "proportional") == {}


def test_apply_builds_match_result_selections():
    fixture = make_fixture("f1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30)
    h2h = [s for s in fixture.selections if s.market == MARKET_H2H]

    assert {s.label for s in h2h} == {"Everton", "Draw", "Crystal Palace"}
    assert sum(s.fair_probability for s in h2h) == pytest.approx(1.0, abs=1e-9)


def test_derived_double_chance_is_flagged_and_priced_sensibly():
    fixture = make_fixture("f1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30)
    dc = {s.label: s for s in fixture.selections if s.market == MARKET_DOUBLE_CHANCE}

    home_or_draw = dc["Everton or Draw"]
    # 42.71% + 28.43%
    assert home_or_draw.fair_probability == pytest.approx(0.7114, abs=1e-3)
    # Must be shorter than either leg alone, and never odds-on-impossible.
    assert 1.0 < home_or_draw.odds < 2.23
    assert home_or_draw.price_source == SOURCE_DERIVED
    assert home_or_draw.is_derived


def test_double_chance_probabilities_cover_two_outcomes_each():
    fixture = make_fixture("f1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30)
    dc = [s for s in fixture.selections if s.market == MARKET_DOUBLE_CHANCE]

    assert len(dc) == 3
    # Each result is covered by exactly two of the three, so the total is 2.0.
    assert sum(s.fair_probability for s in dc) == pytest.approx(2.0, abs=1e-6)


def test_real_double_chance_prices_are_used_when_available():
    fixture = make_fixture("f1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30)
    fixture.double_chance_prices = {"Everton or Draw": 1.36}
    devig.apply(fixture)

    dc = [s for s in fixture.selections if s.market == MARKET_DOUBLE_CHANCE]
    assert len(dc) == 1
    assert dc[0].odds == 1.36
    assert dc[0].price_source != SOURCE_DERIVED


def test_unrecognisable_double_chance_label_is_skipped():
    fixture = make_fixture("f1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30)
    fixture.double_chance_prices = {"Something Else Entirely": 1.36}
    devig.apply(fixture)

    assert not [s for s in fixture.selections if s.market == MARKET_DOUBLE_CHANCE]


def test_fixture_with_no_prices_is_left_alone():
    from football.models import Fixture
    from datetime import datetime

    fixture = Fixture(
        id="empty", league_key="k", league_name="n", league_short="s",
        home_team="A", away_team="B", kickoff=datetime(2026, 8, 22, 15, 0),
    )
    devig.apply(fixture)
    assert fixture.selections == []

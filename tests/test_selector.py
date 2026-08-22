from __future__ import annotations

from itertools import combinations

import pytest

from football import selector
from football.models import MARKET_DOUBLE_CHANCE

STAKE = 10.0


# ── Which Double Chance forms are backable ────────────────────────

def test_home_or_away_double_chance_is_excluded_by_default(mismatch_card, config):
    """"Forest or Leeds" wins on anything but a draw — a bet against one
    outcome rather than a pick, and unreadable on a coupon."""
    pool = selector.candidate_selections(mismatch_card, config)
    dc = [s for s in pool if s.market == MARKET_DOUBLE_CHANCE]

    assert dc, "team-or-draw selections should still be available"
    assert all(s.covers_draw for s in dc)


def test_home_or_away_can_be_re_enabled(mismatch_card, config):
    permissive = {**config, "double_chance": {"allow_home_or_away": True}}
    pool = selector.candidate_selections(mismatch_card, permissive)

    assert any(not s.covers_draw for s in pool)


def test_match_result_selections_are_never_filtered(mismatch_card, config):
    """The draw-coverage rule applies to Double Chance only."""
    pool = selector.candidate_selections(mismatch_card, config)
    labels = {s.label for s in pool if s.market != MARKET_DOUBLE_CHANCE}

    for fixture in mismatch_card:
        assert fixture.home_team in labels
        assert fixture.away_team in labels
        assert "Draw" in labels


def test_excluded_form_cannot_reach_any_bet(mismatch_card, config):
    slip = selector.build_slip(mismatch_card, config)
    for bet in slip.available_bets:
        for leg in bet.legs:
            assert leg.selection.covers_draw, \
                f"{bet.title} contains a home-or-away leg: {leg.selection.label}"


# ── Poisson binomial ──────────────────────────────────────────────

def test_probability_at_least_all_legs_is_the_product():
    probs = [0.6, 0.7, 0.8]
    assert selector.probability_at_least(probs, 3) == pytest.approx(0.6 * 0.7 * 0.8)


def test_probability_at_least_zero_is_certain():
    assert selector.probability_at_least([0.1, 0.2], 0) == 1.0


def test_probability_at_least_more_than_available_is_impossible():
    assert selector.probability_at_least([0.9, 0.9], 3) == 0.0


def test_probability_at_least_partial_matches_hand_calculation():
    # Exactly-two-or-more from three 50/50s = 4 of the 8 equally likely outcomes.
    assert selector.probability_at_least([0.5, 0.5, 0.5], 2) == pytest.approx(0.5)


def test_partial_success_is_far_likelier_than_a_clean_sweep():
    probs = [0.6] * 10
    assert selector.probability_at_least(probs, 7) > selector.probability_at_least(probs, 10) * 50


# ── Banker ────────────────────────────────────────────────────────

def test_banker_picks_the_best_paying_confident_selection(mismatch_card, config):
    pool = selector.candidate_selections(mismatch_card, config)
    bet = selector.build_banker(pool, config, STAKE)

    assert bet.is_available
    assert len(bet.legs) == 1
    # Swansea or Draw (63.6% at 1.51, £3.25 expected profit) edges out the
    # shortest straight favourite, Derby at 1.53 (60.3%, £3.20).
    assert bet.legs[0].selection.label == "Swansea City or Draw"
    assert bet.legs[0].selection.odds == pytest.approx(1.51, abs=0.01)


def test_banker_respects_both_floors(mismatch_card, config):
    pool = selector.candidate_selections(mismatch_card, config)
    bet = selector.build_banker(pool, config, STAKE)
    selection = bet.legs[0].selection

    assert selection.fair_probability >= config["banker"]["min_probability"]
    assert selection.odds >= config["banker"]["min_odds"]


def test_banker_prefers_profit_over_bare_certainty(mismatch_card, config):
    """A 95% shot at 1.05 is not a better banker than a 62% shot at 1.60."""
    pool = selector.candidate_selections(mismatch_card, config)
    chosen = selector.build_banker(pool, config, STAKE).legs[0].selection

    qualifying = [
        s for s in pool
        if s.fair_probability >= config["banker"]["min_probability"]
        and s.odds >= config["banker"]["min_odds"]
    ]
    best_profit = max(s.fair_probability * (s.odds - 1.0) for s in qualifying)
    assert chosen.fair_probability * (chosen.odds - 1.0) == pytest.approx(best_profit)


def test_banker_declines_rather_than_lowering_the_bar(even_card, config):
    """A card of coin flips must yield no banker, not a downgraded one."""
    strict = {**config, "banker": {"min_probability": 0.95, "min_odds": 1.50}}
    pool = selector.candidate_selections(even_card, strict)
    bet = selector.build_banker(pool, strict, STAKE)

    assert not bet.is_available
    assert bet.legs == []
    assert "95%" in bet.unavailable_reason
    assert "coin flip" in bet.unavailable_reason


def test_banker_explains_when_safe_picks_pay_nothing(even_card, config):
    expensive = {**config, "banker": {"min_probability": 0.40, "min_odds": 9.0}}
    pool = selector.candidate_selections(even_card, expensive)
    bet = selector.build_banker(pool, expensive, STAKE)

    assert not bet.is_available
    assert "wouldn't return a worthwhile profit" in bet.unavailable_reason


def test_banker_on_an_empty_card(config):
    bet = selector.build_banker([], config, STAKE)
    assert not bet.is_available
    assert "No fixtures" in bet.unavailable_reason


# ── The 4/1+ underdog accumulator ─────────────────────────────────

def test_underdog_acca_backs_every_team_facing_a_big_outsider(mismatch_card, config):
    bet = selector.build_underdog_acca(mismatch_card, config, STAKE)

    assert bet.is_available
    assert [leg.selection.label for leg in bet.legs] == [
        "Derby County",        # Cardiff 5.75
        "Wolves",              # Preston 5.20
        "Southampton",         # Stoke 5.20
        "West Ham United",     # Charlton 7.00
    ]


def test_underdog_acca_uses_match_result_not_double_chance(mismatch_card, config):
    bet = selector.build_underdog_acca(mismatch_card, config, STAKE)
    assert all(leg.selection.market != MARKET_DOUBLE_CHANCE for leg in bet.legs)


def test_underdog_acca_combined_price(mismatch_card, config):
    bet = selector.build_underdog_acca(mismatch_card, config, STAKE)
    assert bet.combined_odds == pytest.approx(6.37, abs=0.01)
    assert bet.potential_return == pytest.approx(63.66, abs=0.05)


def test_underdog_acca_is_empty_when_no_outsider_qualifies(even_card, config):
    """Today's real PL card: longest 3pm underdog is 3.30, so nothing qualifies."""
    bet = selector.build_underdog_acca(even_card, config, STAKE)

    assert not bet.is_available
    assert "4/1" in bet.unavailable_reason


def test_underdog_acca_threshold_is_exclusive_below_four_to_one(config):
    from tests.conftest import make_fixture

    just_under = [make_fixture("x", "Home", "Away", 1.60, 3.80, 4.95)]
    just_over = [make_fixture("y", "Home", "Away", 1.60, 3.80, 5.00)]

    assert not selector.build_underdog_acca(just_under, config, STAKE).is_available
    assert selector.build_underdog_acca(just_over, config, STAKE).is_available


def test_large_underdog_acca_gets_a_system_bet_alternative(config):
    from tests.conftest import make_fixture

    card = [
        make_fixture(f"big{i}", f"Home {i}", f"Away {i}", 1.50, 4.00, 6.00)
        for i in range(8)
    ]
    bet = selector.build_underdog_acca(card, config, STAKE)

    assert len(bet.legs) == 8
    assert bet.note is not None
    assert "any 5 from 8" in bet.note
    # The honest point of the note: partial success is vastly likelier.
    assert bet.joint_probability < 0.10


def test_small_underdog_acca_has_no_system_bet_note(mismatch_card, config):
    bet = selector.build_underdog_acca(mismatch_card, config, STAKE)
    assert bet.note is None


# ── Double ────────────────────────────────────────────────────────

def _brute_force_best_double(pool, min_odds):
    best, best_prob = None, -1.0
    for a, b in combinations(pool, 2):
        if a.fixture_id == b.fixture_id or a.odds * b.odds < min_odds:
            continue
        joint = a.fair_probability * b.fair_probability
        if joint > best_prob:
            best, best_prob = (a, b), joint
    return best_prob


def test_double_is_the_most_likely_valid_pair(even_card, config):
    pool = selector.candidate_selections(even_card, config)
    bet = selector.build_double(pool, config, STAKE)
    expected = _brute_force_best_double(pool, config["double"]["min_odds"])

    assert bet.is_available
    assert bet.joint_probability == pytest.approx(expected, abs=1e-9)


def test_double_is_optimal_on_a_card_with_favourites(mismatch_card, config):
    pool = selector.candidate_selections(mismatch_card, config)
    bet = selector.build_double(pool, config, STAKE)
    expected = _brute_force_best_double(pool, config["double"]["min_odds"])

    assert bet.joint_probability == pytest.approx(expected, abs=1e-9)


def test_double_legs_come_from_different_matches(mismatch_card, config):
    pool = selector.candidate_selections(mismatch_card, config)
    bet = selector.build_double(pool, config, STAKE)
    assert len({leg.selection.fixture_id for leg in bet.legs}) == 2


def test_double_clears_the_odds_floor(even_card, config):
    pool = selector.candidate_selections(even_card, config)
    bet = selector.build_double(pool, config, STAKE)
    assert bet.combined_odds >= config["double"]["min_odds"]


def test_double_declines_when_nothing_reaches_the_floor(even_card, config):
    greedy = {**config, "double": {"min_odds": 500.0}}
    pool = selector.candidate_selections(even_card, greedy)
    bet = selector.build_double(pool, greedy, STAKE)

    assert not bet.is_available
    assert "too short-priced" in bet.unavailable_reason


# ── Accumulators ──────────────────────────────────────────────────

def _limits(config):
    return config["accumulator_legs"]


def test_accumulator_has_the_requested_number_of_legs(mismatch_card, config):
    pool = selector.candidate_selections(mismatch_card, config)
    for spec in config["accumulators"]:
        bet = selector.build_accumulator(pool, spec, STAKE, _limits(config))
        assert len(bet.legs) == spec["legs"], spec["name"]


def test_accumulator_never_doubles_up_on_one_match(mismatch_card, config):
    pool = selector.candidate_selections(mismatch_card, config)
    for spec in config["accumulators"]:
        bet = selector.build_accumulator(pool, spec, STAKE, _limits(config))
        fixture_ids = [leg.selection.fixture_id for leg in bet.legs]
        assert len(fixture_ids) == len(set(fixture_ids)), spec["name"]


def test_accumulator_trades_safety_for_price_to_clear_the_floor(mismatch_card, config):
    """Top-N by probability alone would be a pile of 1.10 shots paying nothing."""
    pool = selector.candidate_selections(mismatch_card, config)
    spec = {"legs": 4, "name": "Fourfold", "min_odds": 5.0}

    naive = selector._greedy_by_probability(pool, 4)
    naive_odds = 1.0
    for s in naive:
        naive_odds *= s.odds

    bet = selector.build_accumulator(pool, spec, STAKE, _limits(config))
    assert naive_odds < spec["min_odds"]        # the naive pick is too short
    assert bet.combined_odds >= spec["min_odds"]  # the built one isn't


def test_accumulator_legs_are_all_bets_worth_placing(mismatch_card, config):
    """Regression: the engine used to pad accumulators with 1.01 near-certainties
    and dump all the risk on one longshot leg. Every leg must stand on its own."""
    pool = selector.candidate_selections(mismatch_card, config)
    limits = _limits(config)

    for spec in config["accumulators"]:
        bet = selector.build_accumulator(pool, spec, STAKE, limits)
        for leg in bet.legs:
            assert leg.selection.odds >= limits["min_odds"], \
                f"{spec['name']}: {leg.selection.label} at {leg.selection.odds}"
            assert leg.selection.fair_probability >= limits["min_probability"], \
                f"{spec['name']}: {leg.selection.label}"


def test_accumulator_does_not_hang_the_bet_on_a_single_longshot(mismatch_card, config):
    """No one leg may be dramatically longer than the rest of the bet."""
    pool = selector.candidate_selections(mismatch_card, config)
    spec = {"legs": 5, "name": "Fivefold", "min_odds": 8.0}
    bet = selector.build_accumulator(pool, spec, STAKE, _limits(config))

    longest = max(leg.selection.odds for leg in bet.legs)
    assert longest < bet.combined_odds / 2, \
        "one leg is carrying more than half the accumulator's price"


def test_accumulator_declines_when_the_card_is_too_short(even_card, config):
    """Three fixtures cannot make a fourfold — one leg per match."""
    pool = selector.candidate_selections(even_card, config)
    spec = {"legs": 4, "name": "Fourfold", "min_odds": 5.0}
    bet = selector.build_accumulator(pool, spec, STAKE, _limits(config))

    assert not bet.is_available
    assert "A fourfold needs 4" in bet.unavailable_reason


def test_accumulator_notes_when_it_cannot_reach_the_target_price(even_card, config):
    pool = selector.candidate_selections(even_card, config)
    spec = {"legs": 3, "name": "Treble", "min_odds": 999.0}
    bet = selector.build_accumulator(pool, spec, STAKE, _limits(config))

    assert bet.is_available  # still a valid bet, just not the target price
    assert "short of the" in bet.note


# ── Whole slip ────────────────────────────────────────────────────

def test_slip_contains_the_four_recommended_bets(mismatch_card, config):
    slip = selector.build_slip(mismatch_card, config)
    assert [b.kind for b in slip.bets] == [
        "banker", "underdog_acca", "double", "treble",
    ]


def test_each_bet_carries_its_own_stake(mismatch_card, config):
    """The banker is a tenner; the 4/1+ acca is a pound."""
    slip = selector.build_slip(mismatch_card, config)
    stakes = {b.kind: b.stake for b in slip.bets}

    assert stakes["banker"] == 10.0
    assert stakes["underdog_acca"] == 1.0
    assert stakes["double"] == 10.0
    assert stakes["treble"] == 10.0


def test_underdog_acca_returns_are_based_on_its_own_stake(mismatch_card, config):
    slip = selector.build_slip(mismatch_card, config)
    acca = next(b for b in slip.bets if b.kind == "underdog_acca")

    assert acca.potential_return == pytest.approx(acca.combined_odds, abs=0.01)
    assert acca.expected_value > -1.01  # can never lose more than the £1 staked


def test_slip_reports_honest_expected_value(mismatch_card, config):
    """Bookmaker margin means every bet should show a negative EV."""
    slip = selector.build_slip(mismatch_card, config)
    for bet in slip.available_bets:
        assert bet.expected_value < 0, f"{bet.title} claims a positive edge"


def test_slip_survives_an_empty_card(config):
    slip = selector.build_slip([], config)
    assert slip.available_bets == []
    assert all(b.unavailable_reason for b in slip.bets)


def test_potential_return_is_stake_times_price(mismatch_card, config):
    slip = selector.build_slip(mismatch_card, config)
    for bet in slip.available_bets:
        assert bet.potential_return == pytest.approx(
            bet.stake * bet.combined_odds, abs=0.01)

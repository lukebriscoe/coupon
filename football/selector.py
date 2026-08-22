"""The bet builders.

Everything here works on de-vigged probabilities, so selections are comparable
across fixtures regardless of how heavily each match is juiced.

Two rules apply to every accumulator:
  * One leg per fixture. Two selections from the same match are correlated, and
    Sky Bet won't let you combine them anyway.
  * Nothing is presented as a certainty. Every bet reports its true joint
    probability and expected value alongside the potential return.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from math import comb

from football.models import (
    MARKET_DOUBLE_CHANCE,
    Bet,
    BetLeg,
    Fixture,
    Selection,
    Slip,
)


# ── Probability helpers ───────────────────────────────────────────

def probability_at_least(probabilities: list[float], k: int) -> float:
    """Chance that at least k of these independent selections win.

    A Poisson binomial, computed by dynamic programming over the exact
    distribution of the number of winners.
    """
    if k <= 0:
        return 1.0
    if k > len(probabilities):
        return 0.0

    # distribution[i] = probability that exactly i legs have won so far.
    distribution = [1.0]
    for p in probabilities:
        nxt = [0.0] * (len(distribution) + 1)
        for wins, prob in enumerate(distribution):
            nxt[wins] += prob * (1.0 - p)
            nxt[wins + 1] += prob * p
        distribution = nxt
    return sum(distribution[k:])


# ── Candidate pool ────────────────────────────────────────────────

def candidate_selections(fixtures: list[Fixture], config: dict) -> list[Selection]:
    """Every backable selection across the card, in the configured markets."""
    eligible = set(config["eligible_markets"])
    return [
        selection
        for fixture in fixtures
        for selection in fixture.selections
        if selection.market in eligible
    ]


def _greedy_by_probability(pool: list[Selection], count: int) -> list[Selection]:
    """Highest-probability selections, at most one per fixture."""
    picks: list[Selection] = []
    used: set[str] = set()
    for selection in sorted(pool, key=lambda s: s.fair_probability, reverse=True):
        if selection.fixture_id in used:
            continue
        picks.append(selection)
        used.add(selection.fixture_id)
        if len(picks) == count:
            break
    return picks


def _combined_odds(selections: list[Selection]) -> float:
    odds = 1.0
    for selection in selections:
        odds *= selection.odds
    return odds


def _swap_candidates(picks: list[Selection], pool: list[Selection]):
    """Every legal single-leg substitution, as (index, replacement) pairs.

    A replacement must lengthen the price, and must not duplicate a fixture
    already used by one of the other legs.
    """
    used = {s.fixture_id for s in picks}
    for index, leg in enumerate(picks):
        blocked = used - {leg.fixture_id}
        for candidate in pool:
            if candidate.odds <= leg.odds or candidate.fair_probability <= 0.0:
                continue
            if candidate.fixture_id in blocked:
                continue
            yield index, leg, candidate


def _lift_to_odds_floor(picks: list[Selection], pool: list[Selection],
                        min_odds: float, max_swaps: int = 60) -> list[Selection]:
    """Trade probability for price until the combination clears the floor.

    The safest legs are all Double Chance shots priced around 1.20, so the
    top-N by probability is a near-certainty that pays almost nothing. Legs are
    swapped up in price one at a time, always taking the best exchange rate
    available — the most price gained per unit of probability given up, measured
    in logs so the trade-off compares fairly across very different prices.

    Once a single swap can finish the job, the one leaving the highest joint
    probability wins, which stops the accumulator overshooting its target.
    """
    picks = list(picks)

    for _ in range(max_swaps):
        current = _combined_odds(picks)
        if current >= min_odds:
            break

        finishers: list[tuple[float, int, Selection]] = []
        best_rate = 0.0
        best_trade: tuple[int, Selection] | None = None

        for index, leg, candidate in _swap_candidates(picks, pool):
            if current / leg.odds * candidate.odds >= min_odds:
                # This swap clears the floor on its own — rank by what's left.
                resulting = (candidate.fair_probability / leg.fair_probability)
                finishers.append((resulting, index, candidate))
                continue

            odds_gain = math.log(candidate.odds / leg.odds)
            probability_cost = math.log(leg.fair_probability / candidate.fair_probability)
            # A longer price at no cost in probability is a free upgrade.
            rate = math.inf if probability_cost <= 0 else odds_gain / probability_cost
            if rate > best_rate:
                best_rate, best_trade = rate, (index, candidate)

        if finishers:
            _, index, candidate = max(finishers, key=lambda f: f[0])
            picks[index] = candidate
            break

        if best_trade is None:
            break  # nothing left to trade — the card can't reach the target
        index, candidate = best_trade
        picks[index] = candidate

    return picks


def _legs(selections: list[Selection]) -> list[BetLeg]:
    return [BetLeg(selection=s) for s in selections]


# ── The six bets ──────────────────────────────────────────────────

def build_banker(pool: list[Selection], config: dict, stake: float) -> Bet:
    """The single selection with the best profit at genuinely high confidence.

    Two floors have to be cleared: the selection must be likely enough to
    deserve the name, and it must pay enough to be worth a tenner. If nothing
    clears both, the bet is declined rather than the bar being lowered.
    """
    cfg = config["banker"]
    min_probability = cfg["min_probability"]
    min_odds = cfg["min_odds"]

    qualifying = [
        s for s in pool
        if s.fair_probability >= min_probability and s.odds >= min_odds
    ]

    if not qualifying:
        best = max(pool, key=lambda s: s.fair_probability, default=None)
        if best is None:
            reason = "No fixtures with prices."
        elif best.fair_probability < min_probability:
            reason = (
                f"Nothing on today's card reaches {min_probability:.0%} confidence. "
                f"The strongest selection is {best.label} at "
                f"{best.fair_probability:.1%} — that is close to a coin flip, "
                "not a banker."
            )
        else:
            reason = (
                f"The safe selections are all priced under {min_odds:.2f}, so a "
                f"£{stake:.0f} stake wouldn't return a worthwhile profit."
            )
        return Bet(kind="banker", title="Banker of the Day", legs=[],
                   stake=stake, unavailable_reason=reason)

    # Expected profit, not raw probability — a 95% shot at 1.05 is a bad banker.
    best = max(qualifying, key=lambda s: s.fair_probability * (s.odds - 1.0) * stake)
    return Bet(kind="banker", title="Banker of the Day",
               legs=_legs([best]), stake=stake)


def build_underdog_acca(fixtures: list[Fixture], config: dict, stake: float) -> Bet:
    """Back every team whose opponent is priced at 4/1 or longer.

    Implemented literally as specified. On a full 3pm card this can qualify a
    dozen teams, and a twelve-leg accumulator of 60% shots lands roughly twice
    in a thousand attempts — so when the leg count gets silly, a system-bet
    alternative is attached rather than the selections being quietly trimmed.
    """
    cfg = config["underdog_acca"]
    threshold = cfg["opponent_min_odds"]

    picks: list[Selection] = []
    for fixture in fixtures:
        for team in (fixture.home_team, fixture.away_team):
            opponent_price = fixture.price_for(fixture.opponent_of(team))
            if opponent_price is None or opponent_price < threshold:
                continue
            match = next(
                (s for s in fixture.selections
                 if s.market != MARKET_DOUBLE_CHANCE and s.label == team),
                None,
            )
            if match is not None:
                picks.append(match)

    title = f"Opponents {_fractional_threshold(threshold)}+ Acca"

    if not picks:
        return Bet(
            kind="underdog_acca", title=title, legs=[], stake=stake,
            unavailable_reason=(
                f"No 15:00 fixture has an underdog priced at "
                f"{_fractional_threshold(threshold)} or longer, so this bet has "
                "nothing to include today."
            ),
        )

    bet = Bet(kind="underdog_acca", title=title, legs=_legs(picks), stake=stake)

    if len(picks) >= cfg["system_bet_threshold"]:
        total = len(picks)
        needed = max(2, int(total * cfg["system_bet_ratio"]))
        probabilities = [s.fair_probability for s in picks]
        all_win = bet.joint_probability
        partial = probability_at_least(probabilities, needed)
        bet.note = (
            f"All {total} legs landing is a {all_win:.2%} shot — this is a "
            f"lottery ticket, not a strategy. The same {total} selections as an "
            f"“any {needed} from {total}” system bet ({comb(total, needed):,} lines) "
            f"pays out {partial:.1%} of the time, which keeps the idea intact at "
            "a realistic hit rate."
        )

    return bet


def build_double(pool: list[Selection], config: dict, stake: float) -> Bet:
    """The two-leg combination most likely to land, at a price worth placing."""
    min_odds = config["double"]["min_odds"]

    best_pair: tuple[Selection, Selection] | None = None
    best_probability = 0.0

    ranked = sorted(pool, key=lambda s: s.fair_probability, reverse=True)
    for i, first in enumerate(ranked):
        # Once the best possible partner can't beat the leader, stop looking.
        if first.fair_probability * ranked[0].fair_probability <= best_probability:
            break
        for second in ranked[i + 1:]:
            if first.fixture_id == second.fixture_id:
                continue
            joint = first.fair_probability * second.fair_probability
            if joint <= best_probability:
                break  # ranked descending — no later partner will do better
            if first.odds * second.odds < min_odds:
                continue
            best_pair, best_probability = (first, second), joint

    if best_pair is None:
        return Bet(
            kind="double", title="Banker Double", legs=[], stake=stake,
            unavailable_reason=(
                f"No two selections from different fixtures combine to "
                f"{min_odds:.2f} or better. Today's card is too short-priced "
                "for a double to be worth placing."
            ),
        )

    return Bet(kind="double", title="Banker Double",
               legs=_legs(list(best_pair)), stake=stake)


def build_accumulator(pool: list[Selection], spec: dict, stake: float,
                      leg_limits: dict | None = None) -> Bet:
    """A treble, fourfold or fivefold: the safest legs that clear the odds floor."""
    legs_wanted = spec["legs"]
    min_odds = spec["min_odds"]
    title = spec["name"]

    limits = leg_limits or {}
    min_leg_odds = limits.get("min_odds", 1.0)
    min_leg_probability = limits.get("min_probability", 0.0)
    eligible = [
        s for s in pool
        if s.odds >= min_leg_odds and s.fair_probability >= min_leg_probability
    ]

    fixture_count = len({s.fixture_id for s in eligible})
    if fixture_count < legs_wanted:
        return Bet(
            kind=title.lower(), title=title, legs=[], stake=stake,
            unavailable_reason=(
                f"Only {fixture_count} fixture{'s' if fixture_count != 1 else ''} "
                f"on today's 15:00 card offer a leg worth including — at least "
                f"{min_leg_probability:.0%} likely and priced {min_leg_odds:.2f} "
                f"or better. A {title.lower()} needs {legs_wanted}, and two legs "
                "from the same match can't be combined."
            ),
        )

    pool = eligible
    picks = _greedy_by_probability(pool, legs_wanted)
    picks = _lift_to_odds_floor(picks, pool, min_odds)

    bet = Bet(kind=title.lower(), title=title, legs=_legs(picks), stake=stake)

    if bet.combined_odds < min_odds:
        bet.note = (
            f"Best available price is {bet.combined_odds:.2f}, short of the "
            f"{min_odds:.2f} target — today's card doesn't offer enough value "
            f"for a {title.lower()} at this confidence level."
        )
    return bet


# ── Slip assembly ─────────────────────────────────────────────────

def _fractional_threshold(decimal_odds: float) -> str:
    from football.models import to_fractional
    return to_fractional(decimal_odds)


def build_slip(fixtures: list[Fixture], config: dict,
               slip_date: str | None = None) -> Slip:
    """Every recommendation for one Saturday's 15:00 card."""
    stake = config["stake"]
    pool = candidate_selections(fixtures, config)

    bets = [
        build_banker(pool, config, stake),
        build_underdog_acca(fixtures, config, stake),
        build_double(pool, config, stake),
    ]
    leg_limits = config.get("accumulator_legs", {})
    bets.extend(
        build_accumulator(pool, spec, stake, leg_limits)
        for spec in config["accumulators"]
    )

    if slip_date is None:
        slip_date = (
            fixtures[0].kickoff.date().isoformat() if fixtures
            else datetime.now(timezone.utc).date().isoformat()
        )

    return Slip(
        date=slip_date,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        fixtures=fixtures,
        bets=bets,
        stake=stake,
    )

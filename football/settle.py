"""Settle logged slips against real results.

The Odds API's /scores endpoint returns completed fixtures. Each leg is matched
by fixture id, its result decided, and the bet marked won or lost. An
accumulator needs every leg — one loser settles the whole thing.
"""
from __future__ import annotations

from football.models import MARKET_DOUBLE_CHANCE
from football.odds_client import OddsClient
from football.store import load_slips, save_slips

HOME, AWAY, DRAW = "home", "away", "draw"


def outcome_of(scores: list[dict], home_team: str, away_team: str) -> str | None:
    """Which of home/away/draw actually happened. None if not yet decided."""
    lookup = {s.get("name"): s.get("score") for s in scores or []}
    home_score, away_score = lookup.get(home_team), lookup.get(away_team)
    if home_score is None or away_score is None:
        return None
    try:
        home_goals, away_goals = int(home_score), int(away_score)
    except (TypeError, ValueError):
        return None

    if home_goals > away_goals:
        return HOME
    if away_goals > home_goals:
        return AWAY
    return DRAW


def leg_result(leg: dict, result: str) -> str:
    """Whether one leg won, given the match outcome.

    Match Result legs are decided by an exact label match. Double Chance legs
    cover two of the three outcomes, so the label is read to work out which.
    """
    home, away = leg["home_team"], leg["away_team"]
    label = leg["label"]

    if leg["market"] == MARKET_DOUBLE_CHANCE:
        normalised = label.casefold()
        covered = set()
        if home.casefold() in normalised:
            covered.add(HOME)
        if away.casefold() in normalised:
            covered.add(AWAY)
        if "draw" in normalised:
            covered.add(DRAW)
        if len(covered) != 2:
            return "pending"  # can't interpret the label — leave it alone
        return "won" if result in covered else "lost"

    if label == home:
        return "won" if result == HOME else "lost"
    if label == away:
        return "won" if result == AWAY else "lost"
    if label.casefold() == "draw":
        return "won" if result == DRAW else "lost"
    return "pending"


def settle_slips(client: OddsClient, days_from: int = 3) -> dict:
    """Settle every pending bet that now has a result. Returns a summary."""
    slips = load_slips()
    pending = [
        (slip, bet) for slip in slips for bet in slip.get("bets", [])
        if bet.get("status") == "pending"
    ]
    if not pending:
        return {"settled": 0, "still_pending": 0, "leagues_checked": 0, "errors": []}

    league_keys = {
        leg["league_key"]
        for _, bet in pending for leg in bet.get("legs", [])
    }

    results: dict[str, str] = {}
    errors: list[str] = []
    for league_key in sorted(league_keys):
        try:
            events = client.fetch_scores(league_key, days_from=days_from)
        except Exception as e:  # noqa: BLE001 — one bad league mustn't block the rest
            errors.append(f"{league_key}: {e}")
            continue
        for event in events:
            if not event.get("completed"):
                continue
            outcome = outcome_of(
                event.get("scores") or [],
                event.get("home_team", ""),
                event.get("away_team", ""),
            )
            if outcome is not None:
                results[event["id"]] = outcome

    settled_count = 0
    for _, bet in pending:
        legs = bet.get("legs", [])
        if not legs:
            continue

        for leg in legs:
            if leg.get("result") == "pending":
                outcome = results.get(leg["fixture_id"])
                if outcome is not None:
                    leg["result"] = leg_result(leg, outcome)

        leg_results = [leg.get("result", "pending") for leg in legs]
        if any(r == "lost" for r in leg_results):
            bet["status"] = "lost"
            bet["returns"] = 0.0
            settled_count += 1
        elif all(r == "won" for r in leg_results):
            bet["status"] = "won"
            bet["returns"] = round(bet["stake"] * bet["combined_odds"], 2)
            settled_count += 1

    save_slips(slips)
    return {
        "settled": settled_count,
        "still_pending": len(pending) - settled_count,
        "leagues_checked": len(league_keys),
        "errors": errors,
    }

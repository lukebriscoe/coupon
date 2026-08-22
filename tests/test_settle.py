from __future__ import annotations

import pytest

from football import settle
from football.models import MARKET_DOUBLE_CHANCE, MARKET_H2H


def scores(home_team, home_goals, away_team, away_goals):
    return [
        {"name": home_team, "score": str(home_goals)},
        {"name": away_team, "score": str(away_goals)},
    ]


# ── Reading the result ────────────────────────────────────────────

def test_home_win():
    assert settle.outcome_of(scores("Everton", 2, "Palace", 0), "Everton", "Palace") == "home"


def test_away_win():
    assert settle.outcome_of(scores("Everton", 0, "Palace", 1), "Everton", "Palace") == "away"


def test_draw():
    assert settle.outcome_of(scores("Everton", 1, "Palace", 1), "Everton", "Palace") == "draw"


def test_missing_score_is_undecided():
    assert settle.outcome_of([{"name": "Everton", "score": "1"}], "Everton", "Palace") is None


def test_empty_scores_are_undecided():
    assert settle.outcome_of([], "Everton", "Palace") is None


def test_non_numeric_score_is_undecided():
    assert settle.outcome_of(
        scores("Everton", "?", "Palace", 1), "Everton", "Palace") is None


# ── Settling a leg ────────────────────────────────────────────────

def h2h_leg(label):
    return {"home_team": "Everton", "away_team": "Crystal Palace",
            "market": MARKET_H2H, "label": label}


def dc_leg(label):
    return {"home_team": "Everton", "away_team": "Crystal Palace",
            "market": MARKET_DOUBLE_CHANCE, "label": label}


@pytest.mark.parametrize("label,result,expected", [
    ("Everton", "home", "won"),
    ("Everton", "draw", "lost"),
    ("Everton", "away", "lost"),
    ("Crystal Palace", "away", "won"),
    ("Crystal Palace", "home", "lost"),
    ("Draw", "draw", "won"),
    ("Draw", "home", "lost"),
])
def test_match_result_legs(label, result, expected):
    assert settle.leg_result(h2h_leg(label), result) == expected


@pytest.mark.parametrize("label,result,expected", [
    ("Everton or Draw", "home", "won"),
    ("Everton or Draw", "draw", "won"),
    ("Everton or Draw", "away", "lost"),
    ("Crystal Palace or Draw", "away", "won"),
    ("Crystal Palace or Draw", "home", "lost"),
    ("Everton or Crystal Palace", "home", "won"),
    ("Everton or Crystal Palace", "away", "won"),
    ("Everton or Crystal Palace", "draw", "lost"),
])
def test_double_chance_legs(label, result, expected):
    assert settle.leg_result(dc_leg(label), result) == expected


def test_uninterpretable_double_chance_label_stays_pending():
    assert settle.leg_result(dc_leg("Mystery Market"), "home") == "pending"


def test_uninterpretable_match_result_label_stays_pending():
    assert settle.leg_result(h2h_leg("Both Teams To Score"), "home") == "pending"


# ── Settling a whole slip ─────────────────────────────────────────

class FakeClient:
    """Stands in for The Odds API's /scores endpoint."""

    def __init__(self, events):
        self.events = events

    def fetch_scores(self, league_key, days_from=2):
        return [e for e in self.events if e["sport_key"] == league_key]


def event(fixture_id, home, home_goals, away, away_goals, completed=True):
    return {
        "id": fixture_id, "sport_key": "soccer_epl", "completed": completed,
        "home_team": home, "away_team": away,
        "scores": scores(home, home_goals, away, away_goals),
    }


def make_stored_slip(tmp_path, monkeypatch, legs, kind="double"):
    slips_file = tmp_path / "slips.json"
    monkeypatch.setattr(settle, "load_slips", lambda: _stored)
    monkeypatch.setattr(settle, "save_slips",
                        lambda data: slips_file.write_text(str(data)))
    _stored[:] = [{
        "date": "2026-08-22",
        "bets": [{
            "kind": kind, "title": "Test Bet", "stake": 10.0,
            "status": "pending", "returns": 0.0,
            "combined_odds": 4.0, "legs": legs,
        }],
    }]
    return _stored


_stored: list = []


def leg(fixture_id, label, market=MARKET_H2H, home="Everton", away="Crystal Palace"):
    return {
        "fixture_id": fixture_id, "league_key": "soccer_epl",
        "home_team": home, "away_team": away,
        "market": market, "label": label, "result": "pending",
    }


def test_accumulator_wins_only_when_every_leg_wins(tmp_path, monkeypatch):
    stored = make_stored_slip(tmp_path, monkeypatch, [
        leg("a", "Everton"),
        leg("b", "Ipswich Town", home="Ipswich Town", away="Sunderland"),
    ])
    client = FakeClient([
        event("a", "Everton", 2, "Crystal Palace", 0),
        event("b", "Ipswich Town", 1, "Sunderland", 0),
    ])

    summary = settle.settle_slips(client)
    bet = stored[0]["bets"][0]

    assert summary["settled"] == 1
    assert bet["status"] == "won"
    assert bet["returns"] == 40.0  # £10 at 4.00


def test_one_losing_leg_sinks_the_accumulator(tmp_path, monkeypatch):
    stored = make_stored_slip(tmp_path, monkeypatch, [
        leg("a", "Everton"),
        leg("b", "Ipswich Town", home="Ipswich Town", away="Sunderland"),
    ])
    client = FakeClient([
        event("a", "Everton", 2, "Crystal Palace", 0),
        event("b", "Ipswich Town", 0, "Sunderland", 3),
    ])

    settle.settle_slips(client)
    bet = stored[0]["bets"][0]

    assert bet["status"] == "lost"
    assert bet["returns"] == 0.0


def test_bet_stays_pending_until_every_leg_has_a_result(tmp_path, monkeypatch):
    stored = make_stored_slip(tmp_path, monkeypatch, [
        leg("a", "Everton"),
        leg("b", "Ipswich Town", home="Ipswich Town", away="Sunderland"),
    ])
    # Only the first match has finished.
    client = FakeClient([event("a", "Everton", 2, "Crystal Palace", 0)])

    summary = settle.settle_slips(client)
    bet = stored[0]["bets"][0]

    assert bet["status"] == "pending"
    assert summary["still_pending"] == 1
    assert bet["legs"][0]["result"] == "won"
    assert bet["legs"][1]["result"] == "pending"


def test_incomplete_matches_are_ignored(tmp_path, monkeypatch):
    stored = make_stored_slip(tmp_path, monkeypatch, [leg("a", "Everton")])
    client = FakeClient([event("a", "Everton", 1, "Crystal Palace", 0, completed=False)])

    settle.settle_slips(client)
    assert stored[0]["bets"][0]["status"] == "pending"


def test_a_failing_league_does_not_block_settlement(tmp_path, monkeypatch):
    stored = make_stored_slip(tmp_path, monkeypatch, [leg("a", "Everton")])

    class Broken(FakeClient):
        def fetch_scores(self, league_key, days_from=2):
            raise RuntimeError("network down")

    summary = settle.settle_slips(Broken([]))
    assert summary["errors"]
    assert stored[0]["bets"][0]["status"] == "pending"


def test_nothing_pending_is_a_no_op(monkeypatch):
    monkeypatch.setattr(settle, "load_slips", lambda: [])
    summary = settle.settle_slips(FakeClient([]))
    assert summary == {"settled": 0, "still_pending": 0,
                       "leagues_checked": 0, "errors": []}

"""In-play prices must never reach the selection engine.

The /odds endpoint keeps serving a fixture after it kicks off, but the prices
become live: a team two goals up shortens to about 1.01. Left unfiltered, that
reads as a 99% pre-match certainty, and an accumulator built from a card of
them multiplied out past 10^23 on the first real deployment.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from football.odds_client import OddsClient

KICKOFF_UTC = "2026-08-22T14:00:00Z"  # 15:00 BST
TARGET = date(2026, 8, 22)


def event(event_id: str, home: str, away: str, home_price: float,
          draw_price: float, away_price: float,
          commence: str = KICKOFF_UTC) -> dict:
    return {
        "id": event_id,
        "commence_time": commence,
        "home_team": home,
        "away_team": away,
        "bookmakers": [{
            "key": "skybet",
            "markets": [{
                "key": "h2h",
                "outcomes": [
                    {"name": home, "price": home_price},
                    {"name": "Draw", "price": draw_price},
                    {"name": away, "price": away_price},
                ],
            }],
        }],
    }


@pytest.fixture
def client(config, monkeypatch) -> OddsClient:
    c = OddsClient(api_key="test-key", config=config)
    monkeypatch.setattr(c, "_get", lambda *a, **k: [
        event("e1", "Everton", "Crystal Palace", 2.23, 3.35, 3.30),
        event("e2", "Ipswich Town", "Sunderland", 1.67, 3.30, 5.50),
    ])
    return c


LEAGUE = {"key": "soccer_epl", "name": "Premier League", "short": "PL"}


def test_fixtures_are_returned_before_kick_off(client):
    before = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    fixtures, in_play = client.fetch_league(LEAGUE, TARGET, now=before)

    assert len(fixtures) == 2
    assert in_play == 0


def test_fixtures_are_dropped_once_they_kick_off(client):
    during = datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)  # 16:30 BST
    fixtures, in_play = client.fetch_league(LEAGUE, TARGET, now=during)

    assert fixtures == []
    assert in_play == 2


def test_kick_off_minute_itself_counts_as_started(client):
    at_kickoff = datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)
    fixtures, in_play = client.fetch_league(LEAGUE, TARGET, now=at_kickoff)

    assert fixtures == []
    assert in_play == 2


def test_one_minute_before_kick_off_still_counts(client):
    just_before = datetime(2026, 8, 22, 13, 59, tzinfo=timezone.utc)
    fixtures, _ = client.fetch_league(LEAGUE, TARGET, now=just_before)

    assert len(fixtures) == 2


def test_card_warns_when_fixtures_are_excluded_for_being_in_play(client, monkeypatch):
    monkeypatch.setitem(client.config, "leagues", [LEAGUE])
    during = datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)

    fixtures, warnings = client.fetch_card(TARGET, now=during)

    assert fixtures == []
    assert any("already kicked off" in w for w in warnings)
    assert any("in-play" in w for w in warnings)


def test_card_is_clean_before_kick_off(client, monkeypatch):
    monkeypatch.setitem(client.config, "leagues", [LEAGUE])
    before = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)

    fixtures, warnings = client.fetch_card(TARGET, now=before)

    assert len(fixtures) == 2
    assert not any("kicked off" in w for w in warnings)


def test_in_play_prices_cannot_produce_an_absurd_accumulator(client, monkeypatch):
    """The regression that shipped: live prices multiplied out past 10^23."""
    monkeypatch.setattr(client, "_get", lambda *a, **k: [
        # A team 2-0 up in play, and its opponent drifting to a huge price.
        event("e1", "Everton", "Crystal Palace", 1.01, 26.0, 220.0),
        event("e2", "Ipswich Town", "Sunderland", 1.02, 34.0, 340.0),
    ])
    monkeypatch.setitem(client.config, "leagues", [LEAGUE])
    during = datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)

    fixtures, _ = client.fetch_card(TARGET, now=during)
    assert fixtures == [], "in-play prices reached the engine"


# ── Which kick-offs each fetch returns ────────────────────────────

LUNCHTIME_UTC = "2026-08-29T11:30:00Z"  # 12:30 BST
THREE_UTC = "2026-08-29T14:00:00Z"      # 15:00 BST
TEATIME_UTC = "2026-08-29T16:30:00Z"    # 17:30 BST
SATURDAY = date(2026, 8, 29)


@pytest.fixture
def full_day(config, monkeypatch) -> OddsClient:
    """A Saturday with a lunchtime, a 3pm and a tea-time kick-off."""
    c = OddsClient(api_key="test-key", config=config)
    monkeypatch.setitem(c.config, "leagues", [LEAGUE])
    monkeypatch.setattr(c, "_get", lambda *a, **k: [
        event("lunch", "Liverpool", "Forest", 1.44, 4.75, 6.00, LUNCHTIME_UTC),
        event("three", "Cardiff", "Sheffield United", 2.60, 3.75, 2.50, THREE_UTC),
        event("tea", "Spurs", "Newcastle", 2.20, 3.60, 3.20, TEATIME_UTC),
    ])
    return c


MORNING = datetime(2026, 8, 29, 6, 0, tzinfo=timezone.utc)


def test_fetch_day_returns_every_kick_off(full_day):
    fixtures, _ = full_day.fetch_day(SATURDAY, now=MORNING)
    assert {f.id for f in fixtures} == {"lunch", "three", "tea"}


def test_fetch_card_returns_only_the_three_oclock_games(full_day):
    fixtures, _ = full_day.fetch_card(SATURDAY, now=MORNING)
    assert [f.id for f in fixtures] == ["three"]


def test_fetch_day_is_ordered_by_kick_off(full_day):
    fixtures, _ = full_day.fetch_day(SATURDAY, now=MORNING)
    assert [f.id for f in fixtures] == ["lunch", "three", "tea"]


def test_is_kickoff_time_identifies_the_three_oclock_window(full_day):
    fixtures, _ = full_day.fetch_day(SATURDAY, now=MORNING)
    at_three = [f for f in fixtures if full_day.is_kickoff_time(f.kickoff)]
    assert [f.id for f in at_three] == ["three"]

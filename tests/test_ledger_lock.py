"""A recorded slip must not be rewritten once it could have been staked.

On 29 August 2026 the Saturday build was queued by GitHub and did not start
until 13:55 UTC — five minutes before kick-off. It rebuilt the coupon from
fresh prices, changed the banker from Cardiff to Blackburn, and replaced the
recorded slip. The bet that had actually been placed that morning was no
longer in the ledger, and settlement would have resolved a different one.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from football import store
from football.models import Bet, BetLeg, Selection, Slip

UK = store.UK
MATCH_DAY = "2026-08-29"


def make_slip(label: str, date_iso: str = MATCH_DAY) -> Slip:
    selection = Selection(
        fixture_id="f1", league_key="soccer_efl_champ", league_short="CH",
        home_team="Cardiff City", away_team="Sheffield United",
        kickoff=datetime(2026, 8, 29, 15, 0, tzinfo=UK),
        market="h2h", label=label, odds=1.54, fair_probability=0.62,
    )
    bet = Bet(kind="banker", title="Banker of the Day",
              legs=[BetLeg(selection=selection)], stake=10.0)
    return Slip(date=date_iso, generated_at="2026-08-28T16:49:00+00:00",
                fixtures=[], bets=[bet], stake=10.0)


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "SLIPS_FILE", tmp_path / "slips.json")


def at(hour: int, day: int = 29) -> datetime:
    return datetime(2026, 8, day, hour, 0, tzinfo=UK)


# ── Before the cut-off, a rebuild is an improvement ────────────────

def test_first_recording_always_writes():
    assert store.record_slip(make_slip("Cardiff City or Draw"), now=at(9))
    assert store.load_slips()[0]["bets"][0]["legs"][0]["label"] == "Cardiff City or Draw"


def test_rebuild_before_match_day_replaces_freely():
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(16, day=28))
    assert store.record_slip(make_slip("Blackburn Rovers or Draw"), now=at(7))
    assert store.load_slips()[0]["bets"][0]["legs"][0]["label"] == "Blackburn Rovers or Draw"


def test_rebuild_early_on_match_day_still_replaces():
    """An 08:00 build is fresher and nobody has staked yet."""
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(16, day=28))
    assert store.record_slip(make_slip("Blackburn Rovers or Draw"), now=at(8))
    assert store.load_slips()[0]["bets"][0]["legs"][0]["label"] == "Blackburn Rovers or Draw"


# ── After the cut-off, the record stands ───────────────────────────

def test_the_regression_a_late_build_cannot_rewrite_the_slip():
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(16, day=28))

    written = store.record_slip(make_slip("Blackburn Rovers or Draw"), now=at(14))

    assert written is False
    assert store.load_slips()[0]["bets"][0]["legs"][0]["label"] == "Cardiff City or Draw"


def test_lock_closes_exactly_at_the_configured_hour():
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(16, day=28))

    assert store.record_slip(make_slip("Nine"), lock_after_hour=10, now=at(9))
    assert not store.record_slip(make_slip("Ten"), lock_after_hour=10, now=at(10))


def test_a_past_slip_is_never_rewritten():
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(9))
    assert not store.record_slip(make_slip("Blackburn Rovers or Draw"), now=at(9, day=30))


def test_settled_legs_lock_the_slip_regardless_of_time():
    """Belt and braces: a rebuild must never destroy a settled result."""
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(16, day=28))
    slips = store.load_slips()
    slips[0]["bets"][0]["legs"][0]["result"] = "won"
    store.save_slips(slips)

    # Early on match day, which would normally be replaceable.
    assert not store.record_slip(make_slip("Blackburn Rovers or Draw"), now=at(7))
    assert store.load_slips()[0]["bets"][0]["legs"][0]["result"] == "won"


def test_a_different_saturday_is_unaffected():
    store.record_slip(make_slip("Cardiff City or Draw"), now=at(9))
    later = make_slip("Next week", date_iso="2026-09-05")
    assert store.record_slip(later, now=at(9, day=30))
    assert len(store.load_slips()) == 2


def test_empty_slips_are_not_recorded():
    slip = make_slip("Cardiff City or Draw")
    slip.bets = []
    assert not store.record_slip(slip, now=at(9))
    assert store.load_slips() == []

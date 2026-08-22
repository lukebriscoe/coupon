"""The 15:00 filter has to hold across the BST/GMT boundary.

A hardcoded UTC offset would work all summer and then silently drop every
fixture from the last Sunday in October. These tests pin both sides of it.
"""
from __future__ import annotations

from datetime import date

import pytest

from football.odds_client import OddsClient


@pytest.fixture
def client(config) -> OddsClient:
    return OddsClient(api_key="", config=config)


def test_bst_three_pm_is_fourteen_hundred_utc(client):
    """August: UK is BST (UTC+1), so 15:00 local is 14:00Z."""
    assert client.is_target_kickoff("2026-08-22T14:00:00Z", date(2026, 8, 22))


def test_gmt_three_pm_is_fifteen_hundred_utc(client):
    """December: UK is GMT (UTC+0), so 15:00 local is 15:00Z."""
    assert client.is_target_kickoff("2026-12-05T15:00:00Z", date(2026, 12, 5))


def test_summer_offset_is_not_applied_in_winter(client):
    """14:00Z in December is a 14:00 kick-off, not a 15:00 one."""
    assert not client.is_target_kickoff("2026-12-05T14:00:00Z", date(2026, 12, 5))


def test_winter_offset_is_not_applied_in_summer(client):
    """15:00Z in August is a 16:00 kick-off."""
    assert not client.is_target_kickoff("2026-08-22T15:00:00Z", date(2026, 8, 22))


def test_the_weekend_the_clocks_change(client):
    """25 Oct 2026 is the switch to GMT — 15:00 local is 15:00Z that afternoon."""
    assert client.is_target_kickoff("2026-10-25T15:00:00Z", date(2026, 10, 25))
    # The Saturday before is still BST.
    assert client.is_target_kickoff("2026-10-24T14:00:00Z", date(2026, 10, 24))


@pytest.mark.parametrize("commence", [
    "2026-08-22T11:30:00Z",  # 12:30 lunchtime kick-off
    "2026-08-22T16:30:00Z",  # 17:30 tea-time kick-off
    "2026-08-22T19:00:00Z",  # 20:00 evening kick-off
])
def test_other_kick_off_slots_are_excluded(client, commence):
    assert not client.is_target_kickoff(commence, date(2026, 8, 22))


def test_a_different_saturday_is_excluded(client):
    assert not client.is_target_kickoff("2026-08-29T14:00:00Z", date(2026, 8, 22))


def test_matching_time_on_any_date_when_no_target_given(client):
    assert client.is_target_kickoff("2026-08-29T14:00:00Z", None)


def test_offset_notation_is_handled(client):
    assert client.is_target_kickoff("2026-08-22T14:00:00+00:00", date(2026, 8, 22))


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-13-45T99:00:00Z"])
def test_unparseable_timestamps_are_rejected_not_raised(client, bad):
    assert not client.is_target_kickoff(bad, date(2026, 8, 22))

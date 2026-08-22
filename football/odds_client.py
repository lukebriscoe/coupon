"""Client for The Odds API (the-odds-api.com).

Sky Bet publishes no API and every odds aggregator blocks scraping, so a
licensed feed is the only reliable way to get their prices. Responses are
cached to data/cache/ and credit spend is tracked in data/api_usage.json so a
morning of refreshing doesn't burn through the 500/month free tier.

Set ODDS_API_KEY in .env. Without it the client runs in demo mode against
data/demo_card.json so the app is still explorable.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv

from football.models import SOURCE_MEDIAN, SOURCE_SKYBET, Fixture

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

API_BASE = "https://api.the-odds-api.com/v4"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
USAGE_FILE = PROJECT_ROOT / "data" / "api_usage.json"
CONFIG_FILE = PROJECT_ROOT / "config.yaml"
DEMO_FILE = PROJECT_ROOT / "data" / "demo_card.json"


class RateLimitError(Exception):
    """Raised when The Odds API reports the monthly credit quota is exhausted."""


class OddsAPIError(Exception):
    """Raised when the API returns an error we can't recover from."""


def load_config(path: Path | None = None) -> dict:
    """Read config.yaml. Every threshold in the app comes from here."""
    with open(path or CONFIG_FILE) as f:
        return yaml.safe_load(f)


class OddsClient:
    def __init__(self, api_key: str | None = None, config: dict | None = None):
        self.config = config or load_config()
        odds_cfg = self.config["odds"]

        self.api_key = api_key if api_key is not None else os.getenv("ODDS_API_KEY", "")
        self.bookmaker = odds_cfg["bookmaker"]
        self.region = odds_cfg["region"]
        self.odds_format = odds_cfg["odds_format"]
        self.cache_ttl = timedelta(hours=odds_cfg["cache_ttl_hours"])
        self.credit_limit = odds_cfg["monthly_credit_limit"]
        self.markets = odds_cfg["markets"]
        self.fallback_markets = odds_cfg["fallback_markets"]

        kickoff = self.config["kickoff"]
        self.timezone = ZoneInfo(kickoff["timezone"])
        self.kickoff_hour = kickoff["hour"]
        self.kickoff_minute = kickoff["minute"]

        self.session = requests.Session()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def demo_mode(self) -> bool:
        """True when no key is configured — the app serves a sample card."""
        return not self.api_key or self.api_key == "your_api_key_here"

    # ── HTTP / caching ────────────────────────────────────────────

    def _cache_key(self, url: str, params: dict) -> str:
        # The API key is excluded so rotating it doesn't invalidate the cache.
        safe = {k: v for k, v in params.items() if k != "apiKey"}
        raw = f"{url}:{json.dumps(safe, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _get_cached(self, key: str) -> Any | None:
        path = CACHE_DIR / f"{key}.json"
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl.total_seconds():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None

    def _set_cache(self, key: str, data: Any) -> None:
        (CACHE_DIR / f"{key}.json").write_text(json.dumps(data, default=str))

    def _get(self, endpoint: str, params: dict, use_cache: bool = True) -> Any:
        url = f"{API_BASE}{endpoint}"
        params = {**params, "apiKey": self.api_key}
        key = self._cache_key(url, params)

        if use_cache:
            cached = self._get_cached(key)
            if cached is not None:
                return cached

        response = self.session.get(url, params=params, timeout=20)

        if response.status_code == 401:
            raise OddsAPIError("The Odds API rejected the key. Check ODDS_API_KEY in .env.")
        if response.status_code == 429:
            raise RateLimitError(
                "The Odds API monthly credit quota is exhausted. "
                "It resets at the start of your billing month."
            )
        if response.status_code == 422:
            # Usually an unsupported market for the current plan.
            raise OddsAPIError(f"The Odds API rejected the request: {response.text}")
        if not response.ok:
            raise OddsAPIError(f"The Odds API returned {response.status_code}: {response.text}")

        self._record_usage(response.headers)
        data = response.json()
        self._set_cache(key, data)
        return data

    # ── Credit tracking ───────────────────────────────────────────

    @staticmethod
    def _usage_key(when: date | None = None) -> str:
        return (when or date.today()).strftime("%Y-%m")

    @staticmethod
    def _load_usage() -> dict:
        if USAGE_FILE.exists():
            try:
                return json.loads(USAGE_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _record_usage(self, headers: Any) -> None:
        """Prefer the API's own counters; fall back to counting locally."""
        usage = self._load_usage()
        month = usage.setdefault(self._usage_key(), {"used": 0, "remaining": None})

        used = headers.get("x-requests-used")
        remaining = headers.get("x-requests-remaining")
        if used is not None:
            try:
                month["used"] = int(used)
            except ValueError:
                month["used"] += 1
        else:
            month["used"] += 1
        if remaining is not None:
            try:
                month["remaining"] = int(remaining)
            except ValueError:
                pass

        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps(usage, indent=2))

    def get_credit_usage(self) -> tuple[int, int]:
        """(credits used this month, monthly limit) for the UI banner."""
        month = self._load_usage().get(self._usage_key(), {})
        used = month.get("used", 0)
        remaining = month.get("remaining")
        if remaining is not None:
            return used, used + remaining
        return used, self.credit_limit

    # ── Fixtures ──────────────────────────────────────────────────

    def is_target_kickoff(self, commence_time: str, target: date | None = None) -> bool:
        """True when a UTC kick-off lands at 15:00 UK time on the target date.

        Uses a real timezone rather than a fixed offset — hardcoding +1 would
        break every match from late October, when the UK falls back to GMT.
        """
        try:
            utc = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
        except ValueError:
            return False
        if utc.tzinfo is None:
            utc = utc.replace(tzinfo=timezone.utc)

        local = utc.astimezone(self.timezone)
        if local.hour != self.kickoff_hour or local.minute != self.kickoff_minute:
            return False
        return target is None or local.date() == target

    def _extract_prices(self, event: dict, market_key: str) -> tuple[dict[str, float], str]:
        """Get Sky Bet's prices for one market, or the market median if absent.

        A substituted price is flagged so it's never shown as a Sky Bet quote.
        """
        by_bookmaker: dict[str, dict[str, float]] = {}
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != market_key:
                    continue
                prices = {
                    o["name"]: float(o["price"])
                    for o in market.get("outcomes", [])
                    if o.get("name") and o.get("price")
                }
                if prices:
                    by_bookmaker[bookmaker["key"]] = prices

        if self.bookmaker in by_bookmaker:
            return by_bookmaker[self.bookmaker], SOURCE_SKYBET
        if not by_bookmaker:
            return {}, SOURCE_SKYBET

        outcomes: dict[str, list[float]] = {}
        for prices in by_bookmaker.values():
            for name, price in prices.items():
                outcomes.setdefault(name, []).append(price)
        median = {name: round(statistics.median(v), 2) for name, v in outcomes.items()}
        return median, SOURCE_MEDIAN

    def _parse_event(self, event: dict, league: dict) -> Fixture | None:
        h2h, source = self._extract_prices(event, "h2h")
        if not h2h:
            return None

        dc, _ = self._extract_prices(event, "double_chance")
        utc = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))

        return Fixture(
            id=event["id"],
            league_key=league["key"],
            league_name=league["name"],
            league_short=league["short"],
            home_team=event["home_team"],
            away_team=event["away_team"],
            kickoff=utc.astimezone(self.timezone),
            h2h_prices=h2h,
            double_chance_prices=dc,
            price_source=source,
        )

    def fetch_league(self, league: dict, target: date, use_cache: bool = True,
                     now: datetime | None = None) -> tuple[list[Fixture], int]:
        """One league's 15:00 fixtures for the target date, pre-match only.

        Returns the fixtures and a count of those dropped for having kicked
        off. The /odds endpoint keeps serving a fixture once it is under way,
        but the prices become in-play: a team two goals up shortens to about
        1.01, which is not a 99% pre-match shot and must never reach the
        selection engine.
        """
        params = {
            "regions": self.region,
            "bookmakers": self.bookmaker,
            "oddsFormat": self.odds_format,
            "dateFormat": "iso",
        }

        try:
            events = self._get(
                f"/sports/{league['key']}/odds",
                {**params, "markets": ",".join(self.markets)},
                use_cache=use_cache,
            )
        except OddsAPIError:
            # Double Chance isn't on every plan — retry with match result only.
            events = self._get(
                f"/sports/{league['key']}/odds",
                {**params, "markets": ",".join(self.fallback_markets)},
                use_cache=use_cache,
            )

        now = now or datetime.now(timezone.utc)
        fixtures: list[Fixture] = []
        in_play = 0
        for event in events:
            if not self.is_target_kickoff(event.get("commence_time", ""), target):
                continue
            fixture = self._parse_event(event, league)
            if fixture is None:
                continue
            if fixture.kickoff <= now:
                in_play += 1
                continue
            fixtures.append(fixture)
        return fixtures, in_play

    def fetch_card(self, target: date, use_cache: bool = True,
                   now: datetime | None = None) -> tuple[list[Fixture], list[str]]:
        """The full 15:00 card across every configured league, pre-match only.

        Returns the fixtures plus any warnings, so one dead league doesn't take
        down the whole page.
        """
        if self.demo_mode:
            return self._load_demo_card(), [
                "Demo mode — no ODDS_API_KEY set. These are sample prices, "
                "not live Sky Bet quotes."
            ]

        fixtures: list[Fixture] = []
        warnings: list[str] = []
        in_play = 0
        for league in self.config["leagues"]:
            try:
                found, started = self.fetch_league(league, target, use_cache, now)
                fixtures.extend(found)
                in_play += started
            except RateLimitError:
                raise
            except (OddsAPIError, requests.RequestException) as e:
                warnings.append(f"{league['name']}: could not load odds ({e}).")

        if in_play:
            warnings.append(
                f"{in_play} fixture{'s' if in_play != 1 else ''} already kicked "
                "off, so the prices are in-play and have been excluded. A coupon "
                "is only meaningful before 15:00."
            )

        fixtures.sort(key=lambda f: (
            [lg["key"] for lg in self.config["leagues"]].index(f.league_key),
            f.label,
        ))
        return fixtures, warnings

    # ── Demo mode ─────────────────────────────────────────────────

    def _load_demo_card(self) -> list[Fixture]:
        if not DEMO_FILE.exists():
            return []
        raw = json.loads(DEMO_FILE.read_text())
        by_key = {lg["key"]: lg for lg in self.config["leagues"]}

        fixtures = []
        for item in raw.get("fixtures", []):
            league = by_key.get(item["league_key"])
            if league is None:
                continue
            kickoff = datetime.fromisoformat(item["kickoff"])
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=self.timezone)
            fixtures.append(Fixture(
                id=item["id"],
                league_key=league["key"],
                league_name=league["name"],
                league_short=league["short"],
                home_team=item["home_team"],
                away_team=item["away_team"],
                kickoff=kickoff,
                h2h_prices=item["h2h"],
                price_source=item.get("price_source", SOURCE_MEDIAN),
            ))
        return fixtures

    # ── Results ───────────────────────────────────────────────────

    def fetch_scores(self, league_key: str, days_from: int = 2) -> list[dict]:
        """Completed scores for settlement. Costs 2 credits per league."""
        if self.demo_mode:
            return []
        return self._get(
            f"/sports/{league_key}/scores",
            {"daysFrom": days_from, "dateFormat": "iso"},
            use_cache=True,
        )

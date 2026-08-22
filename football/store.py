"""Slip persistence and running P&L.

Every Saturday's recommendations get logged so the model can be judged on what
it actually returned rather than on how good it felt at the time.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from football.models import Bet, BetLeg, Selection, Slip

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SLIPS_FILE = PROJECT_ROOT / "data" / "slips.json"


# ── Serialisation ─────────────────────────────────────────────────

def _leg_to_dict(leg: BetLeg) -> dict:
    s = leg.selection
    return {
        "fixture_id": s.fixture_id,
        "league_key": s.league_key,
        "league_short": s.league_short,
        "home_team": s.home_team,
        "away_team": s.away_team,
        "kickoff": s.kickoff.isoformat(),
        "market": s.market,
        "label": s.label,
        "odds": s.odds,
        "fair_probability": s.fair_probability,
        "price_source": s.price_source,
        "result": leg.result,
    }


def _leg_from_dict(data: dict) -> BetLeg:
    return BetLeg(
        selection=Selection(
            fixture_id=data["fixture_id"],
            league_key=data["league_key"],
            league_short=data["league_short"],
            home_team=data["home_team"],
            away_team=data["away_team"],
            kickoff=datetime.fromisoformat(data["kickoff"]),
            market=data["market"],
            label=data["label"],
            odds=data["odds"],
            fair_probability=data["fair_probability"],
            price_source=data["price_source"],
        ),
        result=data.get("result", "pending"),
    )


def _bet_to_dict(bet: Bet) -> dict:
    return {
        "kind": bet.kind,
        "title": bet.title,
        "stake": bet.stake,
        "status": bet.status,
        "returns": bet.returns,
        "note": bet.note,
        "unavailable_reason": bet.unavailable_reason,
        "legs": [_leg_to_dict(leg) for leg in bet.legs],
        # Snapshotted so history shows the price as recommended, even if the
        # market moved afterwards.
        "combined_odds": bet.combined_odds,
        "joint_probability": bet.joint_probability,
    }


def _bet_from_dict(data: dict) -> Bet:
    bet = Bet(
        kind=data["kind"],
        title=data["title"],
        legs=[_leg_from_dict(leg) for leg in data.get("legs", [])],
        stake=data["stake"],
        unavailable_reason=data.get("unavailable_reason"),
        note=data.get("note"),
        status=data.get("status", "pending"),
        returns=data.get("returns", 0.0),
    )
    return bet


def slip_to_dict(slip: Slip) -> dict:
    """Only the bets are persisted — fixtures are re-fetchable from the API."""
    return {
        "date": slip.date,
        "generated_at": slip.generated_at,
        "stake": slip.stake,
        "fixture_count": slip.fixture_count,
        "bets": [_bet_to_dict(bet) for bet in slip.bets],
    }


# ── Load / save ───────────────────────────────────────────────────

def load_slips() -> list[dict]:
    if SLIPS_FILE.exists():
        try:
            return json.loads(SLIPS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def save_slips(slips: list[dict]) -> None:
    SLIPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SLIPS_FILE.write_text(json.dumps(slips, indent=2))


def record_slip(slip: Slip) -> None:
    """Log a slip, replacing any existing entry for the same date.

    Only bets that could actually be built are stored — there's nothing to
    settle or measure about a bet the engine declined to make.
    """
    payload = slip_to_dict(slip)
    payload["bets"] = [b for b in payload["bets"] if b["legs"]]
    if not payload["bets"]:
        return

    slips = [s for s in load_slips() if s.get("date") != slip.date]
    slips.append(payload)
    slips.sort(key=lambda s: s.get("date", ""))
    save_slips(slips)


def get_slip(slip_date: str) -> dict | None:
    return next((s for s in load_slips() if s.get("date") == slip_date), None)


def bets_from_slip(record: dict) -> list[Bet]:
    return [_bet_from_dict(b) for b in record.get("bets", [])]


# ── Performance ───────────────────────────────────────────────────

def summarise(slips: list[dict]) -> dict:
    """Overall P&L across every settled bet."""
    bets = [b for slip in slips for b in slip.get("bets", [])]
    settled = [b for b in bets if b.get("status") in ("won", "lost")]
    won = [b for b in settled if b["status"] == "won"]

    staked = sum(b["stake"] for b in settled)
    returned = sum(b.get("returns", 0.0) for b in settled)
    net = returned - staked

    # Cumulative P&L in date order, for the performance chart.
    chronological = sorted(
        (
            (slip.get("date", ""), bet)
            for slip in slips for bet in slip.get("bets", [])
            if bet.get("status") in ("won", "lost")
        ),
        key=lambda pair: pair[0],
    )
    cumulative: list[float] = []
    running = 0.0
    for _, bet in chronological:
        running += bet.get("returns", 0.0) - bet["stake"]
        cumulative.append(round(running, 2))

    return {
        "total": len(bets),
        "settled": len(settled),
        "pending": len(bets) - len(settled),
        "won": len(won),
        "staked": round(staked, 2),
        "returned": round(returned, 2),
        "net": round(net, 2),
        "roi": round(net / staked * 100, 1) if staked else 0.0,
        "strike_rate": round(len(won) / len(settled) * 100, 1) if settled else 0.0,
        "cumulative": cumulative,
    }


def summarise_by_kind(slips: list[dict]) -> list[dict]:
    """P&L broken down by bet type — which of the six actually makes money."""
    kinds: dict[str, list[dict]] = {}
    for slip in slips:
        for bet in slip.get("bets", []):
            kinds.setdefault(bet["kind"], []).append(bet)

    rows = []
    for kind, bets in kinds.items():
        stats = summarise([{"date": "", "bets": bets}])
        rows.append({
            "kind": kind,
            "title": bets[0].get("title", kind.title()),
            **stats,
        })
    rows.sort(key=lambda r: r["net"], reverse=True)
    return rows

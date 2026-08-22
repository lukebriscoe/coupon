"""Coupon — Flask web application."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import Flask, redirect, render_template, request, url_for

from football import devig, selector, store
from football.models import (
    SOURCE_DERIVED,
    SOURCE_ILLUSTRATIVE,
    SOURCE_MEDIAN,
    to_fractional,
)
from football.odds_client import OddsAPIError, OddsClient, RateLimitError, load_config
from football.settle import settle_slips

app = Flask(__name__)

_client: OddsClient | None = None


def get_client() -> OddsClient:
    global _client
    if _client is None:
        _client = OddsClient()
    return _client


@app.errorhandler(RateLimitError)
def handle_rate_limit(e: RateLimitError):
    return render_template("error.html", title="Out of credits", message=str(e)), 429


@app.errorhandler(OddsAPIError)
def handle_api_error(e: OddsAPIError):
    return render_template("error.html", title="Odds unavailable", message=str(e)), 502


@app.context_processor
def inject_globals():
    client = get_client()
    used, limit = client.get_credit_usage()
    return {
        "credits_used": used,
        "credits_limit": limit,
        "demo_mode": client.demo_mode,
        "today": date.today(),
        # Set by freeze.py. Hides anything that needs a live server — the
        # refresh link and the settle form can't work on a static host.
        "static_build": app.config.get("STATIC_BUILD", False),
        "built_at": app.config.get("BUILT_AT", ""),
    }


def next_saturday(from_date: date | None = None) -> date:
    """Today if it's Saturday, otherwise the Saturday coming."""
    day = from_date or date.today()
    return day + timedelta(days=(5 - day.weekday()) % 7)


def build_card(target: date, use_cache: bool = True):
    """Fetch the 15:00 card and turn it into a slip of recommendations."""
    client = get_client()
    config = client.config

    fixtures, warnings = client.fetch_card(target, use_cache=use_cache)
    for fixture in fixtures:
        devig.apply(fixture, config["devig"]["method"])

    slip = selector.build_slip(fixtures, config, slip_date=target.isoformat())
    return slip, warnings


# ── Routes ────────────────────────────────────────────────────────

@app.route("/")
def index():
    target_param = request.args.get("date")
    try:
        target = date.fromisoformat(target_param) if target_param else next_saturday()
    except ValueError:
        target = next_saturday()

    slip, warnings = build_card(target)

    if slip.available_bets and not get_client().demo_mode:
        store.record_slip(slip)

    by_league: dict[str, list] = {}
    for fixture in slip.fixtures:
        by_league.setdefault(fixture.league_name, []).append(fixture)

    return render_template(
        "index.html",
        slip=slip,
        warnings=warnings,
        target=target,
        by_league=by_league,
        is_today=target == date.today(),
    )


@app.route("/refresh")
def refresh():
    """Bust the odds cache and re-fetch. Costs one credit per league."""
    target_param = request.args.get("date")
    try:
        target = date.fromisoformat(target_param) if target_param else next_saturday()
    except ValueError:
        target = next_saturday()

    build_card(target, use_cache=False)
    return redirect(url_for("index", date=target.isoformat()))


@app.route("/history")
def history():
    slips = sorted(store.load_slips(), key=lambda s: s.get("date", ""), reverse=True)
    return render_template("history.html", slips=slips,
                           summary=store.summarise(slips))


@app.route("/slip/<slip_date>")
def slip_detail(slip_date: str):
    record = store.get_slip(slip_date)
    if record is None:
        return render_template(
            "error.html", title="No slip found",
            message=f"Nothing was logged for {slip_date}.",
        ), 404
    return render_template("slip.html", record=record,
                           bets=store.bets_from_slip(record))


@app.route("/performance")
def performance():
    slips = store.load_slips()
    return render_template(
        "performance.html",
        summary=store.summarise(slips),
        by_kind=store.summarise_by_kind(slips),
    )


@app.route("/settle", methods=["POST"])
def settle():
    summary = settle_slips(get_client())
    return render_template("settled.html", summary=summary)


# ── Template filters ──────────────────────────────────────────────

@app.template_filter("pct")
def pct_filter(value: float, places: int = 1) -> str:
    return f"{value * 100:.{places}f}%"


@app.template_filter("money")
def money_filter(value: float) -> str:
    return f"£{value:,.2f}"


@app.template_filter("signed_money")
def signed_money_filter(value: float) -> str:
    return f"{'+' if value >= 0 else '−'}£{abs(value):,.2f}"


@app.template_filter("fractional")
def fractional_filter(value: float) -> str:
    return to_fractional(value)


@app.template_filter("kickoff")
def kickoff_filter(value: datetime) -> str:
    return value.strftime("%H:%M")


@app.template_filter("longdate")
def longdate_filter(value) -> str:
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    return value.strftime("%A %-d %B %Y")


@app.template_filter("confidence")
def confidence_filter(probability: float) -> str:
    """Bucket a probability for colour-coding on the coupon."""
    if probability >= 0.70:
        return "strong"
    if probability >= 0.55:
        return "fair"
    if probability >= 0.40:
        return "thin"
    return "longshot"


@app.template_filter("source_warning")
def source_warning_filter(source: str) -> str | None:
    """Human-readable warning for any price that isn't a live Sky Bet quote."""
    return {
        SOURCE_ILLUSTRATIVE: "Invented demo price — not a real quote",
        SOURCE_MEDIAN: "Market median — Sky Bet price unavailable",
        SOURCE_DERIVED: "Estimated from the match-result prices",
    }.get(source)


if __name__ == "__main__":
    app.run(debug=True, port=5057)

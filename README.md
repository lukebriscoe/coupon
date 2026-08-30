# Coupon

Coupon reads the Sky Bet prices for the UK weekend football card and recommends
five bets — four on Saturday, one on Sunday.

| Bet | Stake | What it is |
|---|---|---|
| **Banker of the Day** | £10 | The single selection with the best profit at genuinely high confidence |
| **Opponents 4/1+ Acca** | £1 | Every team whose opponent is priced at 4/1 or longer, in one accumulator. The only bet that looks beyond 15:00 |
| **Banker Double** | £5 | The two-leg combination most likely to land, at a price worth placing |
| **Treble** | £4 | The three safest legs that still clear an odds floor |
| **Sunday Banker** | £10 | The same rule again on Sunday, where every kick-off is in scope |

**£30 across the weekend** — £20 on Saturday, £10 on Sunday. Stake follows
confidence: most of it on the bankers, a pound on the 4/1+ acca as a lottery ticket.
All five are set per-bet in `config.yaml`, and a test asserts Saturday's four still
add up to £20.

Saturday's bets come from the 15:00 kick-offs (bar the acca). Sunday has no 3pm
blackout — the games run from lunchtime to the evening — so every kick-off counts.

Nothing here places a bet. It's all just for fun, and every bet shows its
true probability and expected value next to the potential return.

## Setup

Requires Python 3.9+.

```bash
cd ~/Projects/coupon
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env      # then add your key
./venv/bin/python app.py  # http://127.0.0.1:5057
```

### The API key

Sky Bet has no public API and every odds aggregator blocks scraping, so prices come
from [The Odds API](https://the-odds-api.com), which carries Sky Bet under the
`skybet` bookmaker key. The free tier is 500 credits a month.

Without a key the app runs in **demo mode** against `data/demo_card.json` — real
fixtures for 22 August 2026, but the prices are market composites and invented
placeholders, labelled as such throughout the UI.

Everything tunable lives in `config.yaml` — thresholds, odds floors, leagues, stake,
de-vig method.

## How it works

1. **Fetch** — one request per league (`soccer_epl`, `soccer_efl_champ`,
   `soccer_england_league1`, `soccer_england_league2`), filtered to kick-offs at
   exactly 15:00 Europe/London. The timezone is real, not a fixed offset, so the
   filter survives the October switch to GMT.
2. **De-vig** — Sky Bet's three prices imply more than 100%; that excess is their
   margin. It's removed so probabilities are comparable across fixtures that are
   juiced by different amounts. Proportional by default, Shin available in config.
3. **Select** — the four bets are built from the de-vigged pool, one leg per fixture.

The card shows every Saturday kick-off, but the banker, double and treble are built
from the 15:00 games only. The 4/1+ acca is the exception: it can use any kick-off,
because broadcasters lift the mismatches out into the 12:30 and 17:30 slots, leaving
3pm as systematically the flattest games on the card. One request per league already
returns the whole day, so the wider view costs nothing extra.

## Published site

Live at **[lukebriscoe.com/coupon/](https://lukebriscoe.com/coupon/)**.

`.github/workflows/publish.yml` rebuilds daily: weekday mornings to keep next
Saturday's prices fresh, three attempts on Saturday morning for the definitive
card, and an evening settle each of Saturday and Sunday. Sunday mirrors Saturday:
morning builds to record the banker before the lock, then a night settle. It can
also be triggered by hand.

Three morning attempts because GitHub queues scheduled workflows and can start
them hours late — on 29 August 2026 the 08:30 build did not begin until 13:55.
The real protection is the ledger lock: past 10:00 UK on match day a recorded
slip is frozen, so a late build refreshes the page but cannot rewrite the bets
you placed. `ledger.lock_after_hour` sets the cut-off.

Settlement waits until the evening because the scores feed lags: at 16:55 the
15:00 games still reported `completed: false` with live scores showing.

Odds move all week, so a page built on Sunday is badly out of date by Friday while
still looking current. The daily rebuild keeps it within a day, and the page also
carries its own build time and warns in the browser if what you're reading is more
than 24 hours old.

Build it locally with:

```bash
./venv/bin/python freeze.py --output _site --prefix /coupon
```

The site lives under a path prefix, so pages are rendered against a `base_url`
carrying it — Werkzeug puts that in `SCRIPT_NAME` and every `url_for` comes out
correctly prefixed. Setting `SCRIPT_NAME` via `environ_base` *looks* equivalent
but is silently ignored, because the test client binds its URL adapter from
`base_url`. Anything needing a live server (the refresh link, the settle button)
is hidden in static builds.

`data/slips.json` is committed by the workflow so history and performance survive
between runs. That ledger is public, as is everything else on the site.

## Layout

```
app.py               Flask routes
freeze.py            renders the site to static HTML for Pages
config.yaml          every threshold
football/
  odds_client.py     The Odds API — caching, credit tracking, kick-off filters
  devig.py           margin removal, Double Chance derivation
  selector.py        the four bet builders
  models.py          Fixture, Selection, Bet, Slip
  store.py           slip logging and P&L
  settle.py          results and settlement
.github/workflows/
  publish.yml        daily build, Sunday settle, deployed to Pages
  tests.yml          pytest and a static-build smoke test
```

## Tracking

Slips generated from live odds are logged to `data/slips.json`. After the results
are in, hit **Settle logged bets** — `/scores` is fetched (2 credits per league),
each leg resolved, and P&L updated. `/performance` breaks the record down by bet
type, which is the only way to find out whether any of the four actually pays.

Demo runs are not logged; there'd be nothing real to measure.

## Tests

```bash
./venv/bin/python -m pytest tests/ -q
```

153 tests. The ones that matter most: de-vigged probabilities always sum to 1;
the double is verified optimal by brute force; the 15:00 filter is pinned on both
sides of the BST/GMT boundary; and one losing leg always sinks an accumulator.

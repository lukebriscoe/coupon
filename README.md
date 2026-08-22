# Coupon

Every Saturday, Coupon reads the Sky Bet prices for every UK football fixture kicking
off at 15:00 and recommends four bets.

| Bet | Stake | What it is |
|---|---|---|
| **Banker of the Day** | £10 | The single selection with the best profit at genuinely high confidence |
| **Opponents 4/1+ Acca** | £1 | Every team whose opponent is priced at 4/1 or longer, in one accumulator |
| **Banker Double** | £10 | The two-leg combination most likely to land, at a price worth placing |
| **Treble** | £10 | The three safest legs that still clear an odds floor |

The 4/1+ acca is staked at a pound because it's a twelve-leg lottery ticket on a busy
card, not because it's a lesser bet. Stakes are per-bet in `config.yaml`.

Nothing here places a bet. Everything is a recommendation, and every bet shows its
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
`skybet` bookmaker key. The free tier is 500 credits a month; a full Saturday run
across the four English leagues costs about 4, so roughly 17 a month.

Without a key the app runs in **demo mode** against `data/demo_card.json` — real
fixtures for 22 August 2026, but the prices are market composites and invented
placeholders, labelled as such throughout the UI.

## How it works

1. **Fetch** — one request per league (`soccer_epl`, `soccer_efl_champ`,
   `soccer_england_league1`, `soccer_england_league2`), filtered to kick-offs at
   exactly 15:00 Europe/London. The timezone is real, not a fixed offset, so the
   filter survives the October switch to GMT.
2. **De-vig** — Sky Bet's three prices imply more than 100%; that excess is their
   margin. It's removed so probabilities are comparable across fixtures that are
   juiced by different amounts. Proportional by default, Shin available in config.
3. **Select** — the four bets are built from the de-vigged pool, one leg per fixture.

### Design decisions worth knowing

- **A bet can be declined.** If nothing on the card reaches the confidence floor,
  the banker comes back as "no bet" with the reason, rather than the threshold
  being quietly lowered. A card of coin flips has no banker on it.
- **Double Chance legs are eligible — but only "Team or Draw".** On a typical 3pm
  card, no straight win is a 60%+ shot at a payable price; "Team or Draw" usually
  is. Double Chance has a third form, "Home or Away", which loses only on a draw.
  It's a real market, but it's a bet against one outcome rather than a pick, it
  reads as nonsense on a coupon ("Forest or Leeds"), and it's the form the derived
  pricing is least reliable on. Off by default; `double_chance.allow_home_or_away`
  turns it back on. Where the API doesn't return the market at all, prices are
  estimated from the match-result probabilities and flagged as estimates.
- **The 4/1+ acca is literal, with a warning.** Implemented exactly as specified,
  but on a full card it can qualify a dozen teams — and a twelve-leg accumulator of
  60% shots lands about twice in a thousand. Above six legs the app attaches an
  "any N from M" system-bet alternative with its real hit rate. It doesn't trim your
  selections for you.
- **Accumulators trade safety for price.** Top-N by probability alone is a pile of
  1.10 shots that pays nothing, so legs are swapped up in price — always taking the
  best exchange rate, the most price gained per unit of probability given up —
  until the combination clears its floor.
- **Every accumulator leg has to stand on its own.** Maximising joint probability
  against a combined-odds target is very nearly a self-cancelling objective, so
  without a floor the engine pads the bet with 1.01 near-certainties and dumps all
  the real risk on a single longshot. `accumulator_legs` in `config.yaml` requires
  every leg to be odds-on and priced 1.20 or better — which is also roughly where
  bookmakers stop counting legs towards an accumulator.

Everything tunable lives in `config.yaml` — thresholds, odds floors, leagues, stake,
de-vig method.

## Layout

```
app.py               Flask routes
config.yaml          every threshold
football/
  odds_client.py     The Odds API — caching, credit tracking, 15:00 filter
  devig.py           margin removal, Double Chance derivation
  selector.py        the four bet builders
  models.py          Fixture, Selection, Bet, Slip
  store.py           slip logging and P&L
  settle.py          results and settlement
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

116 tests. The ones that matter most: de-vigged probabilities always sum to 1;
the double is verified optimal by brute force; the 15:00 filter is pinned on both
sides of the BST/GMT boundary; and one losing leg always sinks an accumulator.

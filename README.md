# Kepler

Kepler is a one-screen revenue dashboard for a small business that sells through a funnel (ads or content → opt-in → call → sale). You give it one goal, such as one sale a day, and your conversion benchmarks. It works the funnel backwards from that goal, reads every data source every 15 minutes, and ranks your bottlenecks by how much monthly revenue each one is costing you. The math is plain Python; an optional language model only writes the read-out from numbers Kepler already computed. It also grades your acquisition lanes and judges every ad against your target cost per lead, with an auto-cut that ships switched off.

Walkthrough video: [youtu.be/_C43Hci6WtE](https://youtu.be/_C43Hci6WtE) · Project page: [daymionalvarez.com/portfolio/kepler](https://daymionalvarez.com/portfolio/kepler)

## Quickstart

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/alvarezdaymion/kepler.git
cd kepler
uv run kepler demo --serve        # synthetic data, no keys; open http://127.0.0.1:8765
```

`kepler demo --scenario mixed` shows a funnel where every step converts, so the ranking spreads out.

To use your own data, run `uv run kepler init`, edit `kepler.toml`, put credentials in `.env`, then `uv run kepler check` and `uv run kepler run`. Or hand the repo to your coding agent and say "set up Kepler for my business": [SETUP.md](SETUP.md) is written for it.

## What you see

1. **Goal.** Sales per day against the goal over the last 7 complete days.
2. **Funnel, worked back from the goal.** At your benchmark rates, one sale a day needs about 4 held calls, 6 booked calls, 51 opt-ins, 171 clicks and 11,400 impressions a day. Kepler shows those next to what you actually did, and the same sum at your real rates.
3. **Bottlenecks by revenue at stake.** For each step, monthly volume into it × (benchmark − actual) × the rates of every later step × revenue per sale. If opt-in → booked call is 0% against a 12% benchmark, that step tops the list with a dollar figure, and every step above it reads "$0 until Opt-in to booked call is fixed", because more traffic into a broken step earns nothing.
4. **Blind spots.** Steps no source can see. They show as "Not tracked", never as 0.
5. **Customer journey.** 15 configurable stages grouped attraction → conversion → fulfillment.
6. **Lanes.** Paid ads, email, organic and search, phone sales and partners, each KPI green, yellow or red against its target.
7. **Ad health and auto-cut.** A verdict per ad, keyed by ad ID.
8. **Data health.** Every source with its status (ok, stale, missing), data age and last error.

## How it runs

`kepler run` is one controller tick. Schedule it every 15 minutes (launchd, cron and systemd examples are in [examples/scheduling](examples/scheduling)).

1. Every configured source is read. Sources are read-only.
2. A source that fails keeps its last good copy and is labelled **stale**. One whose data is older than its `max_age_minutes` is stale too. One that has never been read is **missing**, and its metrics are unknown.
3. Every number is computed in Python from that data.
4. Auto-cut runs (off by default), then the optional analysis (at most once a day by default).
5. `state/snapshot.json` and the HTML pages in `site/` are rewritten. `kepler serve` serves them on localhost.

A failing source, auto-cut or analysis never stops the rest of the tick.

## Data sources

| Source | Status | What it provides |
|---|---|---|
| Meta Marketing API | Real connector | Spend, impressions, link clicks and lead actions per ad per day; ad status; the single pause action used by auto-cut |
| Stripe | Real connector, read-only | Sales, cash collected and refunds per day from charges |
| CSV import | Real | Any daily metric (CRM opt-ins, bookings and shows; email; Search Console; YouTube; partners) and CRM leads per ad ID |
| Demo | Synthetic | Everything above, for `kepler demo` |

There are no built-in connectors for CRMs such as GoHighLevel, for Google Search Console or for YouTube. Export a daily CSV from those tools (templates in [examples/csv](examples/csv)), or write a connector: a class with a `name` and `fetch(since, until)` that returns a `SourceData` (see `src/kepler/sources/base.py`).

## Ad rules and auto-cut

T is your target cost per lead (`[ads] target_cpl`). Verdicts use each ad's cumulative numbers over the last 30 days:

| Condition | Verdict |
|---|---|
| Spend under 2T | Testing. Never cut. |
| Spend 2T or more, 0 leads | Cut |
| Cost per lead at or under T | Keep |
| Cost per lead over T, up to 2T | Warning. Becomes a cut once it has stayed in warning longer than `warning_max_hours` (72 by default). |
| Cost per lead over 2T | Cut |

Auto-cut has three modes, set with `[auto_cut] mode`:

- `off` (default): nothing is read or changed. The Ad health page lists what the rules would cut.
- `dry-run`: each candidate is re-read and checked by the guard. Nothing is changed. Run this for a while first.
- `live`: the same checks, then each ad that passes is set to paused and read back.

It only ever pauses. It never turns an ad on, never changes a budget and never edits anything else, and the Meta connector refuses any other write before it is sent. It also holds when the snapshot is more than 20 minutes old, when more than 3 ads would be paused in one run, or when `campaign_ids` is set and the ad is outside it. Every live pause is logged to `state/auto-cut.jsonl`.

### The bad-data guard

Bulk reports are sometimes wrong. One data pull said a brand-new ad had spent $74 on 11 impressions; its real spend was $3.25. Cutting on that pauses an ad that is working. Kepler has two protections:

1. **Everything is keyed by ad ID, never by ad name.** Duplicating a campaign copies ad names, and totals summed by name would give a new ad an old ad's spend.
2. **Before any cut, that one ad's spend is re-read straight from the source.** The cut is held when the snapshot shows more than $1 (`spend_tolerance`) above the fresh read, or when either read implies a cost per 1,000 impressions above $500 (`max_cpm`), or spend with no impressions.

`tests/test_guard.py`, `tests/test_ads.py` and `tests/test_autocut.py` reproduce the $74.49-on-11-impressions read and a duplicated ad name.

## Configuration

Everything lives in `kepler.toml` (start from `uv run kepler init`; defaults are in `src/kepler/config.py`):

- `[goal]`: sales per day and revenue per sale.
- `[[funnel.steps]]`: each step's `from` and `to` metric, benchmark rate, minimum volume before it's judged, and the action to show.
- `[windows]`: pace window (7 days), conversion-rate window (30 days), lane window (7 days).
- `[[journey]]` and `[[lanes]]`: stages and lane KPIs (a metric, or `"metric / metric"`), each with a target.
- `[ads]` and `[auto_cut]`: the rules and guard above.
- `[analysis]`: the optional model read-out.
- `[[sources]]`: what to read. Credentials come from environment variables, never from this file (see `.env.example`).

## Cost

Reading Meta and Stripe is free, so the 15-minute loop costs nothing to run. The only metered call is the optional analysis panel: one model request per `every_minutes` (1,440, once a day, by default), no matter how often the controller ticks. With it off, the panel says so and everything else works.

## Development

```bash
uv run pytest
uv run ruff check
```

Runtime code uses only the Python standard library. The `llm` extra adds the Anthropic SDK for the analysis panel.

## License

MIT. See [LICENSE](LICENSE).

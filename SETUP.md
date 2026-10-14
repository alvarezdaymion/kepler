# Setting up Kepler (for a coding agent)

You are helping the owner of a small business set up Kepler on their own data. Work through the steps in order, ask before each choice that is theirs to make, and run the check at the end of each step. Never write credentials into `kepler.toml`, into the repo, or into chat; they go in `.env` (git-ignored) or the environment of the scheduler.

## 1. Run the demo

```bash
uv run kepler demo
```

Show the owner the printed summary and, if they want, `uv run kepler demo --serve` (http://127.0.0.1:8765). Explain the overview: goal, funnel worked back from the goal, bottlenecks ranked by revenue at stake, blind spots, lanes, ad health, data health. Done when the demo prints a bottleneck list and "guard held Quiz - static".

## 2. Pick the goal and rates

Run `uv run kepler init` to create `kepler.toml`, then ask:

1. **The goal.** Sales per day (or a fraction: 0.5 is one every two days) and the average revenue per sale.
2. **The funnel.** Which steps a buyer goes through. The default is impressions → link clicks → opt-ins → booked calls → held calls → sales. Remove or rename steps to match (steps must chain: each `from` is the previous step's `to`). Businesses without calls might use impressions → clicks → opt-ins → sales.
3. **Benchmarks.** For each step, the rate they should hit. If they have six months of their own numbers, use those. Otherwise keep the defaults and say they are starting lines to replace with real data.
4. **Target cost per lead** (`[ads] target_cpl`) if they run paid ads. A useful starting point: revenue per sale × (lead → sale rate) × the share of revenue they can spend on ads.

Done when `uv run kepler check` prints "Config OK" and the goal you agreed.

## 3. Connect sources

Add a `[[sources]]` block per system (examples are commented at the bottom of `kepler.toml`). Ask which of these they use:

- **Meta ads** (`type = "meta"`): they create a system user in Business Settings with `ads_read` on the ad account and generate a token. Put `META_ACCESS_TOKEN` and `META_AD_ACCOUNT_ID` in `.env`. If they have no CRM export, add `rename = { meta_leads = "optins" }` so Meta's lead count drives the funnel.
- **Stripe** (`type = "stripe"`): they create a restricted key with read access to Charges only and put it in `STRIPE_API_KEY`. If they bill in installments, set `min_sale_amount` so only the first payment counts as a sale, or count sales from the CRM instead.
- **CRM, email, Search Console, YouTube, partners**: there are no built-in connectors. Set up a daily CSV export (`type = "csv"`, templates in `examples/csv/`) or, if they want, write a connector in `src/kepler/sources/` that implements `fetch(since, until) -> SourceData`, with a test using a fake transport (see `tests/test_meta.py`). Connectors must only read.

A metric no source provides shows as "Not tracked". That is expected; do not fill it with zeros.

Done when `uv run kepler check` shows "env set" or "file found" for every source and `uv run kepler run` lists each source as `ok`. If one is `missing` or `stale`, read its error in the output and fix it before moving on.

## 4. Schedule the controller every 15 minutes

Pick the example for their system in `examples/scheduling/` (launchd on macOS, systemd on Linux, cron anywhere), replace `/path/to/kepler` and `/path/to/uv` (`which uv`), and install it. For the page, run `uv run kepler serve` (localhost only) or point any static file server at `site/`.

Done when `state/snapshot.json` has a `generated_at` within the last 15 minutes after a wait, and the Data health table shows recent data ages.

## 5. Auto-cut: dry run first

Auto-cut ships with `mode = "off"`. Only if the owner wants it:

1. Set `[auto_cut] mode = "dry-run"` and `source` to the name of their Meta source. Optionally set `campaign_ids` on the Meta source to limit it to the campaigns it may touch.
2. Let it run for at least a few days. Review the Ad health page with the owner: every "would pause" line should be an ad they agree should be off, and every "guard held" line should be explained.
3. Only after that review, and only when the owner explicitly asks for it, set `mode = "live"` and give the token `ads_management` as well as `ads_read`.

It only ever pauses ads. Do not change the rules in `src/kepler/ads.py` or the guard in `src/kepler/guard.py` to get a cut through; change `[ads]` thresholds in `kepler.toml` if the owner decides the targets are wrong.

## 6. Optional: the analysis panel

If the owner wants a written read-out: `uv sync --extra llm`, put `ANTHROPIC_API_KEY` in `.env`, set `[analysis] enabled = true`. It makes one model call per `every_minutes` (once a day by default); tell them that is the only metered cost. The model only explains numbers Kepler computed.

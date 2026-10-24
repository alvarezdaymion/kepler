# Setting up Kepler (for a coding agent)

You are helping the owner of a small business set up Kepler on their own data. Work through the steps in order, ask before each choice that is theirs to make, and run the check at the end of each step. Never write credentials into `kepler.toml`, into the repo, or into chat; they go in `.env` (git-ignored) or the environment of the scheduler.

## 1. Run the demo

```bash
uv run kepler demo
```

Show the owner the printed summary and, if they want, `uv run kepler demo --serve` (http://127.0.0.1:8765). Explain the overview: goal, funnel worked back from the goal, bottlenecks ranked by revenue at stake, blind spots, lanes, ad health, data health. Done when the demo prints a bottleneck list and "guard held Quiz - static".

## 2. Pick the goal and rates

Run `uv run kepler init` to create `kepler.toml` and copy `.env.example` to `.env`, then ask:

1. **The goal.** Sales per day (or a fraction: 0.5 is one every two days) and the average revenue per sale. Only USD is currently supported; do not configure another currency or import amounts in a different currency.
2. **The funnel.** Which steps a buyer goes through. The default is impressions → link clicks → opt-ins → booked calls → held calls → sales. Remove or rename steps to match (steps must chain: each `from` is the previous step's `to`). Businesses without calls might use impressions → clicks → opt-ins → sales.
3. **Benchmarks.** For each step, the rate they should hit. If they have six months of their own numbers, use those. Otherwise keep the defaults and say they are starting lines to replace with real data.
4. **Target cost per lead** (`[ads] target_cpl`) if they run paid ads. A useful starting point: revenue per sale × (lead → sale rate) × the share of revenue they can spend on ads.

Done when `uv run --env-file .env kepler check` prints "Config OK" and the goal you agreed.

## 3. Connect sources

Add a `[[sources]]` block per system (examples are commented at the bottom of `kepler.toml`). Ask which of these they use:

- **Meta ads** (`type = "meta"`): they create a system user in Business Settings with `ads_read` on the ad account and generate a token. Put `META_ACCESS_TOKEN` and `META_AD_ACCOUNT_ID` in `.env`. If they have no CRM export, add `rename = { meta_leads = "optins" }` so Meta's lead count drives the funnel.
- **Stripe** (`type = "stripe"`): they create a restricted key with read access to Charges only and put it in `STRIPE_API_KEY`. If they bill in installments, set `min_sale_amount` so only the first payment counts as a sale, or count sales from the CRM instead.
- **CRM, email, Search Console, YouTube, partners**: there are no built-in connectors. Set up a daily CSV export (`type = "csv"`, templates in `examples/csv/`) or, if they want, write a connector in `src/kepler/sources/` that implements `fetch(since, until) -> SourceData`, with a test using a fake transport (see `tests/test_meta.py`). Connectors must only read.

A metric no source provides shows as "Not tracked". That is expected; do not fill it with zeros.

CSV dates must use `YYYY-MM-DD`, with nonblank, unique headers and exactly one cell per header in every row. Compact dates, duplicate headers and extra or missing cells fail the export. Stripe reads also fail on missing or invalid refund fields: refund cents must be integers within the charge amount, and the boolean full-refund flag must agree.

Meta action entries must include a valid `action_type` and a finite, nonnegative numeric `value`, even for actions not counted as leads. An incomplete entry fails the read; a usable last copy remains stale.

For CRM ad leads, set `leads_from = "ad_leads"`. A successful CSV with the required headers and no rows means zero leads. Every configured ad-lead export is required: if any is missing, the combined count is unknown and warning timers reset. Recovery starts a new warning period once all required inputs are healthy. It never falls back to platform leads. Ad lead counts must be explicit nonnegative whole numbers. Malformed responses, invalid caches and incomplete pagination cannot be used as healthy data.

Done when `uv run --env-file .env kepler check` shows "env set" or "file found" for every source and `uv run --env-file .env kepler run` lists each source as `ok`. The CLI does not load `.env` itself. If a source is `missing` or `stale`, read its error in the output and fix it before moving on.

## 4. Schedule the controller every 15 minutes

Pick the example for their system in `examples/scheduling/` (launchd on macOS, systemd on Linux, cron anywhere), replace `/path/to/kepler` and `/path/to/uv` (`which uv`), and install it. These examples already load `.env`. For the page, run `uv run --env-file .env kepler serve` (localhost only) or point any static file server at `site/`. Keep a persistent state directory on a local filesystem with file locks; overlapping ticks using it are serialized.

Done when `state/snapshot.json` has a `generated_at` within the last 15 minutes after a wait, and the Data health table shows recent data ages.

## 5. Auto-cut: dry run first

Auto-cut ships with `mode = "off"`. Only if the owner wants it:

1. Set `[auto_cut] mode = "dry-run"` and `source` to the name of their Meta source. Optionally set `campaign_ids` on the Meta source to limit it to the campaigns it may touch.
2. Let it run for at least a few days. Review the Ad health page with the owner: every "would pause" line should be an ad they agree should be off, and every "guard held" line should be explained.
3. Only after that review, and only when the owner explicitly asks for it, set `mode = "live"` and give the token `ads_management` as well as `ads_read`.

It only ever pauses ads. Do not change the rules in `src/kepler/ads.py` or the guard in `src/kepler/guard.py` to get a cut through; change `[ads]` thresholds in `kepler.toml` if the owner decides the targets are wrong.

Both dry-run and live hold cuts on stale or missing ad and selected CRM data and apply the same campaign and status preflight. Judgment covers exactly `judge_days` dates including today. Warning timers reset on disappearance, stale inputs, or observation gaps longer than twice the controller interval.

Live attempts are durably journaled by ad ID before writing in `state/auto-cut.jsonl`. Acknowledgment and verification are recorded separately; failed verification is an error. Previously attempted ads are never retried automatically, including after renaming a source. If an outcome is uncertain, inspect the provider and journal with the owner and handle further action manually. Preserve the journal; do not delete it to force a retry.

## 6. Optional: the analysis panel

If the owner wants a written read-out: `uv sync --extra llm`, put `ANTHROPIC_API_KEY` in `.env`, set `[analysis] enabled = true`, and run with `uv run --env-file .env kepler run`. It makes at most one model request attempt per `every_minutes` (once a day by default), including failures, refusals and interrupted attempts; SDK retries are disabled. Attempts are saved before calling using the current time after reads and lock waits. Tell them that is the only metered cost. The model only explains numbers Kepler computed.

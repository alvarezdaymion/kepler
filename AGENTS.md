# AGENTS.md

Kepler reads a business's data sources every 15 minutes, computes its funnel against a goal, ranks bottlenecks by revenue at stake, and judges ads against a target cost per lead.

If you were asked to set Kepler up for someone, follow [SETUP.md](SETUP.md). The rules below are for changing the code.

## Layout

- `src/kepler/config.py`: config schema and defaults; `src/kepler/templates/kepler.example.toml` mirrors it.
- `src/kepler/sources/`: one module per system. `base.py` defines `SourceData`, `AdRow` and the `Source` / `AdSource` protocols.
- `src/kepler/metrics.py`: merges sources into one store; unknown metrics return `None`.
- `src/kepler/funnel.py`, `bottlenecks.py`, `journey.py`, `lanes.py`, `ads.py`: the math. Pure functions over the store.
- `src/kepler/guard.py`, `autocut.py`: the bad-data guard and the only write path.
- `src/kepler/analysis.py`: the optional model read-out.
- `src/kepler/controller.py`: one tick. `web.py`: HTML. `cli.py`: commands.
- `src/kepler/state.py`: interprocess locks and durable local state writes.

## Rules

- **Math in code, judgment in the model.** Every number on the page is computed in Python. The analysis step receives computed numbers and must not be asked to calculate totals, rates or money.
- **Unknown is not zero.** A metric no source provides is `None` and renders as "Not tracked". A source that read successfully and found nothing is a real 0.
- `SourceData.ad_leads = None` means the source does not provide CRM ad leads; `[]` means a successful empty export. Never substitute platform leads for selected CRM leads.
- When CRM leads are selected, every configured ad-lead export is required. A missing export makes the combined count unknown; missing or stale required inputs reset warning timers until all recover.
- **Stale is labelled.** A failed read keeps the last good copy and marks the source stale. A failing source, auto-cut or analysis never stops the tick.
- Reject malformed responses, invalid numbers and unfinished pagination. Validate caches before using them; an unusable cache leaves the source missing.
- Validate the type and numeric value of every Meta action entry before counting leads, including entries for other action types. Incomplete entries fail the read.
- CSV dates must be canonical `YYYY-MM-DD`; reject blank or duplicate headers and rows whose cell count differs from the headers. Validate Stripe refund amounts and flags before counting a charge.
- **Read-only.** Connectors send GET requests. The single exception is `MetaSource.pause_ad`, whose `_post` refuses anything but `status=PAUSED` on one numeric ad ID. Do not add other writes.
- **Auto-cut stays off by default** and only pauses. It must never turn an ad on, raise a budget or edit anything else. Every candidate passes `guard.check` first, in dry-run and live alike.
- Both modes require healthy input provenance, freshness at decision time, and the same read-only status and campaign preflight. Live reserves each ad by immutable ad ID in a durable journal before writing, records acknowledgment and verification, and never automatically retries an attempted ad, even after a source is renamed.
- Lock the state directory for the entire tick, including analysis and page writes. Keep the pause journal and analysis attempt state across runs.
- **Key ads by ID, never by name.**
- **No metered calls in the 15-minute loop** other than the analysis step. Durably reserve each attempt using the current clock after reads and lock waits; failures and interruptions consume the full `analysis.every_minutes` interval too. Disable SDK retries.
- Assess source health after all reads using the current clock, and timestamp snapshots when they are built.
- **Secrets** come from environment variables. Use `uv run --env-file .env kepler ...` for manual commands with credentials. Never commit `.env`, `kepler.toml`, `state/` or `site/`, and never put tokens in errors, logs or the page (`http.py` errors omit provider and transport text).
- Money is currently USD only; reject unsupported business and Stripe source currencies.
- Runtime code is standard library only; the Anthropic SDK is an optional extra.

## Development

- Python 3.11+, `uv`. Run `uv run pytest` and `uv run ruff check` before a PR.
- Add a failing test with each behavior change. Network code is tested with a fake transport; tests never call real APIs.
- Test data is synthetic: short fake IDs, no real names, emails or phone numbers.

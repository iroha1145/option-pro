# PR #174 measurement and replay

Head is recorded by git. Statistics version is `us-eod-research-stats-v1.0`. Feature version stays `us-eod-research-features-v1.5`.

## Closed this round

- Shared average-rank Spearman. Constant or thin samples return null plus a reason. Pair order is invariant. No jitter, no ticker ranks.
- Factor IC is grouped by `(signal_session, theme, algorithm, profile, horizon, label_horizon)`. n>=10 is within that cross-section only.
- Final LOW_SCORE eligibility is not the scorer universe. Event performance is separate.
- `independent_events` from disjoint-set increments is `SUPERSEDED_METRIC`. Continuous streams use security/family/horizon time overlap.
- Runner passes the full same-track T-complete pool plus SPY/QQQ. Theme B names stay in A's reference set, not A's candidates. Theme tags are not verified industry.
- Shared `precomputed_raws` must still run theme membership. A cache hit cannot skip `candidate_ids`. Theme gates are reapplied only on candidates so one residual extract can be reused.
- `_size_notional` uses a dimensionless risk distance. 1/2/0.5/10 geometry scales and 2-for-1 support 90 vs 45 match.
- Cash acquisitions share the sell finalize path and write `cash_in` / `filled_at` / `exit_reason`. Later lot dividends recompute the originating trade. Zero consideration is filled; missing price is not.
- `signal_available_at` sets the earliest open. NEXT_DAY_CONFIRM is a research policy, not vendor `finalized_at`.
- Funnel keeps `SHORT_HISTORY` in `warmup`, not `setup_not_met`.

## Tests

- Old closeout cases remain in `tests/test_pr174_followup.py`.
- Measurement isolation plus integration asserts: `tests/test_pr174_measurement_acceptance.py`.

## Continuous window

- Cache replay only. 24×A/B/C/D×balanced×mid. Not 864.
- Sessions: 1634, 2018-01-02 through 2024-06-28. Holdout 2024-07-01 sealed.
- Snapshots / function calls: 156864. Recomputes: 0. Outcomes inspected: 4205304. Registered configs: 1920.
- Raw history span 6.49 years. Evaluable continuous years 6.48. Feature-ready names 211. Not a decade.
- 2018 eligible count is 0 (warmup / SHORT_HISTORY). First eligible: 2019-01-04 biotech A LLY.
- Empty theme-date ratio 0.846. Crypto eligible 0. Parameters were not tuned to remove zeros.
- Grouped IC: 322544 groups, 146581 defined, 175963 undefined (`CROSS_SECTION_BELOW_N`). Valid group dates 1376. 2018 has no scorer-universe pairs.
- Independent events: 4514, definition `SECURITY_FAMILY_HORIZON_TIME_OVERLAP`.
- Executed backtests: 0. No winners list from market PnL.
- Label maturity cuts: 5→2024-06-21, 20→2024-05-30, 63→2024-03-28.
- Row file is gitignored. Schema, sha256, and commands are in `measurement_results.json` / `measurement_factor_row_schema.json`.
- Per-group IC tape is gitignored `measurement_ic_groups.jsonl`. Review summary is `measurement_ic_summary.json`.

## Artifacts

- 17-day IC/event fields: `closeout_historical_abcd.SUPERSEDED.json`. Original 17-day file and old `retrieved_at` (`2026-09-16T17:24:41.368841+00:00`) are not rewritten.
- Portfolio counterexamples: `measurement_counterexamples/two_classes.json`.
- Pairing contract: `measurement_runner_pairing.json` (candidates identical; reference pool 15 vs 201).
- F pack: `measurement_results.json`.

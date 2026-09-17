# PR #174 measurement and replay

Head is recorded by git. Statistics version is `us-eod-research-stats-v1.0`. Feature version stays `us-eod-research-features-v1.5`.

## Closed this round

- Shared average-rank Spearman. Constant or thin samples return null plus a reason. Pair order is invariant. No jitter, no ticker ranks.
- Factor IC is grouped by `(signal_session, theme, algorithm, profile, horizon, label_horizon)`. n>=10 is within that cross-section only.
- Final LOW_SCORE eligibility is not the scorer universe. Event performance is separate.
- `independent_events` from disjoint-set increments is `SUPERSEDED_METRIC`. Continuous streams use security/family/horizon time overlap.
- Runner passes the full same-track T-complete pool plus SPY/QQQ. Theme B names stay in A's reference set, not A's candidates. Theme tags are not verified industry.
- `_size_notional` uses a dimensionless risk distance. 1/2/0.5/10 geometry scales and 2-for-1 support 90 vs 45 match.
- Cash acquisitions share the sell finalize path and write `cash_in` / `filled_at` / `exit_reason`. Later lot dividends recompute the originating trade. Zero consideration is filled; missing price is not.
- `signal_available_at` sets the earliest open. NEXT_DAY_CONFIRM is a research policy, not vendor `finalized_at`.

## Tests

- Old 10 closeout cases remain in `tests/test_pr174_followup.py`.
- New 6 isolated names plus integration asserts: `tests/test_pr174_measurement_acceptance.py`.

## Artifacts

- 17-day IC/event fields: `closeout_historical_abcd.SUPERSEDED.json`. Original 17-day file and old `retrieved_at` are not rewritten.
- Portfolio counterexamples: `measurement_counterexamples/two_classes.json`.
- Continuous runner: `scripts/run_measurement_continuous_abcd.py`.

## Not done until the continuous job finishes

Full-window 24×A/B/C/D×balanced×mid row file, grouped IC, and funnel counts. 864 is not claimed. Holdout from 2024-07-01 stays sealed. No merge, no promotion.

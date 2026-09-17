# PR #174 closeout A–E

Head after this round is recorded by git. Feature version is `us-eod-research-features-v1.5`.

## Closed classes

- A. `simulate_portfolio` sizes in the ledger from current cash. It no longer sorts the full signal history by score and pre-splits leftover. Research `start`/`end` are explicit or the panel bounds.
- B. Exits keep `original_planned_exit` / `current_attempt` / `filled_at`. Buy and sell share halt, zero-volume, and missing-quote gates. Missing days, multi-day halts, resume, delist terminals, and end-of-sample opens are recorded.
- C. Dividend receivables bind to `trade_id` / `lot_id`. Pay after exit still credits the originating trade. Missing vendor `pay_date` is `EX_PLUS_ONE_RESEARCH_PROXY`. Equity is marked; trade net is settled cash including lot dividends.
- D. The complete-T pool is built before `extract_raw`. Residual peers use the target series object, so a long-history name missing T cannot `KeyError` on `panel[target]`. Snapshot keeps structured rejects.
- E. `live_capture` and `historical_reconstruction` are explicit. Recapture clock is never written as vendor `finalized_at`. Yahoo-style sources use disclosed `NEXT_DAY_CONFIRM`. The 2026-09-16 `INVALID_EOD_CAPTURE` `retrieved_at` stays frozen.

## Tests

- `tests/test_pr174_followup.py` — isolated 10-case names plus halt/delist/EOD extras.
- `tests/test_pr174_snapshot_stale_integration.py` — ≥420 bars, missing last/internal T, late source, ETF track mismatch.

Isolation blob numbers that differ (residual 0.22085 vs current 0.5396566315790488) come from the current adjacent-session residual, not from dropping the invariance.

## Historical first pass

Offline Yahoo cache replay only. Bars after `2024-06-28` dropped. Holdout `2024-07-01` sealed.

- registered 1920
- executed_snapshots 1632
- executed_backtests 0
- outcomes_inspected 1632
- engineering_fixtures 0
- sessions 17: 2023-06-12..23 daily plus year/quarter ends through 2024-06-28
- remaining daily dates: REGISTERED_NOT_RUN
- Yahoo audit: 213 names, 331010 complete OHLC bars, 0 missing H/L/vol/TRI in the clipped tape
- IC is Spearman of eligible score vs TRI forward 5/20/63, only as a signal diagnostic. Most themes have n<10 so IC stays null. Not PnL.
- Portfolio win-rate not reported: Yahoo Close is not a verified raw executable print.

## Not done

No merge, no promotion, no new families, no score-threshold tuning, no production / A0 / T1 / realtime / daily-stock / options / account changes. Holdout from 2024-07-01 stays sealed. Remaining profiles/horizons and full daily 2018–2024 stream wait for the next research-scope confirmation.

# PR #174 data readiness

Head `ffc8cb1e99897e54272033b986678e057542b8a7`. Review anchor `35d8a08769174f4a56a9eae5af793852f3e1189d`. Protocol `us-eod-research-recovery-v1`.
Recovery dataset `us-eod-recovered-yahoo-cache-v1` is not spliced onto B0.
B0 / Round 1 / Round 1b / Round 2 / freeze files are not rewritten. Holdout 2024-07-01 stays sealed.

## Inventory

- `research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl` role=continuous_runner_yahoo_cache status=present sha256=d910d86505e1914eb14aaacea2ddabc8d61b1f097ab83c513f07750539f32d48 securities_n=214 first=2018-01-02 last=2026-09-16 last_allowed=2024-06-28 actions=no_licensed_ledger; splits_already_in_close_series vintage=download_time_not_pit
- `research/option_pro_us_eod_v1/data/cache/round3_after_close/last_bars.pkl` role=after_close_recapture_cache status=present sha256=827d706e76ea2abeca82616d0a6d8ab227c96160dbd08e5def52c91a17cbbfdc securities_n=214 first=2026-08-20 last=2026-09-16 last_allowed=None actions=no_licensed_ledger; splits_already_in_close_series vintage=download_time_not_pit
- `research/option_pro_us_eod_v1/data/cache/offline_replay/daily_bars.parquet` role=offline_replay_spy_nvda_fixture status=present sha256=f8de36f35ffdfcd532f84fecbb2258d0aa99dd2d06a2de3337f09e34bff77044 securities_n=2 first=2018-01-02 last=2018-01-08 last_allowed=2018-01-08 actions=absent vintage=download_time_not_pit
- `research/option_pro_us_eod_v1/return_pack/measurement_factor_rows.jsonl` role=b0_feature_label_tape status=present sha256=b490ba6d83965a0c3fe60c76afab8205fc2a0d76cc6577cdf23cb838e447f75a securities_n=None first=2018-01-02 last=2024-06-28 last_allowed=2024-06-28 actions=not_in_this_tape vintage=frozen_b0
- `research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/daily_bars.parquet` role=public_yahoo_current_universe_bars status=missing sha256=n/a securities_n=None first=None last=None last_allowed=None actions=unknown_until_file_restored vintage=removed_from_public_git
- `research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/security_master.csv` role=current_universe_security_master status=present sha256=08d7ca7c0435a7dc4b261d624b2aa9d29e3faaf00f99eea2a9d01d645cff680d securities_n=214 first=2015-01-02 last=2026-09-16 last_allowed=None actions=not_in_this_file vintage=current_list_locator_for_missing_parquet
- `research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/theme_snapshot_rows.parquet` role=revoked_invalid_eod_snapshot_rows status=present sha256=82866d86d1ebabc2dbf4a910d232ac679eeb78499ac6e7a17ebcbeba8b3bb3de securities_n=211 first=2026-09-16 last=2026-09-16 last_allowed=None actions=absent vintage=INVALID_EOD_CAPTURE
- `research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/manifest.json` role=missing_parquet_locator status=present sha256=e21cb1dfaaba58552ddb2e027c35b577f80967ee3f0b894338ceafdcc50fa385 securities_n=None first=None last=None last_allowed=None actions=unknown_until_file_restored vintage=INVALID_EOD_CAPTURE_locator
- `research/option_pro_us_eod_v1/return_pack/hashes.json` role=historical_public_bars_digest status=present sha256=2ec1abcc10f35bce24aef7c2ba71915607604a66e59b4ae75f38c1e4d03cbd13 securities_n=None first=None last=None last_allowed=None actions=unknown_until_file_restored vintage=2026-09-16T17:24:41.368841+00:00
- `research/option_pro_us_eod_v1/data/cache` role=project_cache_tree status=directory sha256=n/a securities_n=None first=None last=None last_allowed=None actions=None vintage=scanned
- `research/option_pro_us_eod_v1/return_pack/fixtures/synthetic_daily_bars.parquet` role=ci_fixture_not_market_history status=present sha256=3b94c24280ad30662ea3dc6384e4dc90662cefd37c264f77fe2a99fa8002566c securities_n=2 first=2024-01-02 last=2024-01-03 last_allowed=2024-01-03 actions=absent vintage=offline_export
- `research/option_pro_us_eod_v1/data/local` role=authorized_local_export_root status=missing sha256=n/a securities_n=None first=None last=None last_allowed=None actions=None vintage=None
- `None` role=RESEARCH_EOD_LOCAL_DATA status=unset sha256=n/a securities_n=None first=None last=None last_allowed=None actions=None vintage=None
- `['/opt/cursor/artifacts', '/home/ubuntu/.cursor/projects/workspace/uploads']` role=authorized_cursor_artifact_dirs status=scanned_no_additional_market_tape sha256=n/a securities_n=None first=None last=None last_allowed=None actions=None vintage=None

## Recovered Yahoo cache

- symbols 214 bars 449597 ohlc_violations 0
- SPY allowed complete T-days 1633 first 2018-01-02 last_allowed 2024-06-28
- raw_ne_close_bars 0 (C stays blocked)

## Split-window samples

- TSLA 2020-08-31: no 4-for-1 or 5-for-1 raw gap; close already looks split-adjusted; C blocked
- TSLA 2022-08-25: no 4-for-1 or 5-for-1 raw gap; close already looks split-adjusted; C blocked

## Decade budget

- raw_start 2018-01-02 allowed_end 2024-06-28 span_years 6.485968514715948
- ten_year_evaluable False
- D first_scoreable 2019-04-26
- D label 20 mature 2019-05-24

## Automotive descriptive

- status DESCRIPTIVE_ONLY close_to_close_n 641 next_day_confirm_n 641
- mean equal-weight T→T+20 -0.018760051666892083
- mean excess vs SPY -0.02461621086365604
- NAV NOT_INVENTED

## Stage labels

- composite: 已有候选实现，历史验证/组合验证/生产晋升尚未完成
- three profiles / horizons: 已实现未验证

## Stop

- RECOVERED_CACHE_ARCHIVED: Existing Yahoo diagnostic cache is present, hashed, and A/B checks ran. C stays blocked. Unique remaining user gap is membership/delist tape.
- unique gap: historical_constituent_membership_and_delist_tape

No production champion. No unseal. No new weight grid.

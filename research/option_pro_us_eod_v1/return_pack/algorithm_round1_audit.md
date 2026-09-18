# PR #174 algorithm round 1

Head `5754b4652c618e51f9eee42a958ee9fa58a109d0`. Review anchor `155685daf93982d480fa06a06eb095f7bf8ae984` remains the B0 code/data freeze.
B0 continuous files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.

Local full suite on predecessor `432ac3f6`: 3895 passed, 6 skipped, 325.49s. This head only removes the extra EOF blank that failed `git diff --check`. GitHub `test` on `5754b465` is terminal success on both jobs.

## Isolation

- run_signature `a8ceb257e38e521922736f8dd41eac3edd44214a48bde87df785c9fb3b2502c3`
- Checkpoint mismatch raises `CheckpointSignatureError`; profile/horizon/registry_version cannot reuse done keys.
- Crash after row append without checkpoint replays the session and skips `committed_row_key`.
- Cache layers: feature / score / event / IC. Weight variants reuse factors and rescore.

## Coverage

- 24×4 capability matrix written to `algorithm_round1_capability_matrix.json`.
- G-missing cells that fail coverage_min=0.9 are DATA_INSUFFICIENT data-capability failures.
- PRICE_ONLY_DIAGNOSTIC and D_MARKET_RESIDUAL_DIAGNOSTIC are named tracks, not same-track ablations.

## Events

- Trading-session adjacency. Weekend/holiday gaps are not new events.
- Count name: 去重事件组数. `independent_events` is null. 4514 is SUPERSEDED_EVENT_COUNT.
- Baseline theme-report groups: 10203.
- Baseline global-book groups: 9813. Same-day cross-theme hits are one global group. 10761 is `SAME_DAY_CROSS_THEME_DOUBLE_COUNT`.
- Theme/family/5/20/63 split: `algorithm_round1_event_revision.json` and `algorithm_round1_event_map.json`.

## Ablation

- Registered configurations: 1258.
- Actual rescore calls: 18354834.
- Feature-reuse hits: 2803536.
- Unique snapshots: 1401768.
- Inspected outcomes / transformed label rows: 4205304.
- Pairing cells statistically thin: 1848 / 3774.
- Neighbors N_M_PLUS_5PP, N_S_PLUS_5PP, N_SCORE_FLOOR_PLUS_10PCT were registered before scoring.
- No family was inverted or deleted because an IC was negative.
- No cross-track champion.

## Date ranges

- Raw window: {'start': '2018-01-02', 'end': '2024-06-28', 'n_sessions': 1634, 'n_calendar_days': 2370, 'evaluable_years_div_252': None, 'note': 'session count is not evaluable years; /252 is SUPERSEDED'}.
- Empty ratios use all-window / post-warmup / data-capable denominators.
- `len(sessions)/252` is not evaluable years.

## §1–§6 evidence (current tree)

Status key: PROVED = current file/test/command evidence; UNVERIFIED = required but not yet terminal; NOT_DONE = explicitly out of this pause.

| Req | Evidence now | Status |
| --- | --- | --- |
| B0 freeze, files not overwritten | `measurement_results.json` / `measurement_factor_rows.jsonl` (3.9GB, sha256 `b490ba6d…e447f75a`) still present; algorithm files are new `algorithm_round1_*` | PROVED |
| Canonical `run_signature` + checkpoint reject | `runs.run_signature`, `CheckpointSignatureError`; `test_resume_probe_rejects_stale_profile_horizon_registry` (`stale_reuse_observed is False`) | PROVED |
| Same-signature resume / crash no-dup | `test_same_signature_resume_does_not_recompute`, `test_crash_after_rows_without_checkpoint_does_not_duplicate` | PROVED |
| Feature vs score cache | Weight variants reuse factors (`feature_reuse_hits` 2803536) and rescore (`actual_rescore_calls` 18354834) | PROVED |
| Trading-day events, weekend/holiday not new | `test_weekend_and_holiday_are_not_new_events`; name 去重事件组数; `independent_events` null; 4514 SUPERSEDED | PROVED |
| Same-day cross-theme global dedupe | `test_same_day_cross_theme_does_not_inflate_global_events`; baseline 10203 theme / 9813 global; 10761 DOUBLE_COUNT | PROVED |
| B setup_id vs other episode | `test_b_keeps_frozen_setup_id_others_use_episode` | PROVED |
| 24×4 G-missing capability | `test_coverage_matrix_matches_g_missing_probe`; 14 cells match `coverage_probe` | PROVED |
| Empty-ratio three denominators | `algorithm_round1_date_ranges.json` 24 themes, all-window / post-warmup / data-capable | PROVED |
| `/252` not evaluable years | `evaluable_years_div_252` is null | PROVED |
| PRICE_ONLY / D_MARKET named tracks | `test_diagnostic_tracks_are_named_and_eight_key`; no G on/off fake ablation when coverage fails | PROVED |
| 24-theme balanced/mid ablation + 3 neighbors | `algorithm_round1_ablation_pairing.json` 3774 cells; neighbors registered in manifest before IC | PROVED |
| Theme cards keep/cut/continue/inherit | 51 继续检验 / 45 无结论+继承共享先验 / 0 删减; no champion | PROVED |
| Snapshot `geometry_close` | `snapshot.py` + `ledger.py`; `test_snapshot_rows_carry_geometry_close` | PROVED |
| Local full suite this head | `/opt/cursor/artifacts/pytest_432ac3f6.log`: 3895 passed, 6 skipped on `432ac3f6`; this SHA only removes the EOF blank | PROVED |
| Current-head GitHub CI | `5754b465` push `35312765304` and PR `35312768981` both `test` jobs completed success. `432ac3f6` PR `35311320321` failed on EOF blank and is historical only. | PROVED |
| Portfolio / raw CA book | not run | NOT_DONE |
| New E / weekly / macro | planned only | NOT_DONE |
| Holdout / merge / 864 / executed_backtests | sealed; no merge; 864 not claimed; `executed_backtests=0` | PROVED invariant |

GitHub `test` on research head `5754b465` is terminal success:
push https://github.com/iroha1145/option-pro/actions/runs/35312765304
pull_request https://github.com/iroha1145/option-pro/actions/runs/35312768981
`432ac3f6` PR run 35311320321 remains the historical EOF-blank failure.

# PR #174 algorithm round 1

Head `68ff3b787b53d21baf8f3746518a339ac86038a5`. Review anchor `155685daf93982d480fa06a06eb095f7bf8ae984` remains the B0 code/data freeze.
B0 continuous files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.

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
- Baseline global-book groups: 9813.

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

## Not done

- Portfolio / raw corporate-action verified book remains unrun.
- New E / weekly / macro stay planned.
- Holdout remains sealed. Search stops after these preregistered neighbors.


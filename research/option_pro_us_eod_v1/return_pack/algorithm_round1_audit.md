# PR #174 algorithm round 1

Review anchor `155685daf93982d480fa06a06eb095f7bf8ae984` is the restricted B0 freeze. Holdout `2024-07-01` stays sealed. B0 continuous files are not rewritten. `executed_backtests` stays 0.

## Registered before scoring

Neighbors `N_M_PLUS_5PP`, `N_S_PLUS_5PP`, `N_SCORE_FLOOR_PLUS_10PCT` and the year / two-year date blocks are in `algorithm_round1_neighbors.json` and `algorithm_round1_manifest.json`. They were not fit to this IC.

The 24×4 G-missing capability matrix is in `algorithm_round1_capability_matrix.json`. Fourteen cells are `DATA_INSUFFICIENT` when G is missing and the other seven factors are 100. Those cells are data-capability failures, not strategy losses.

`PRICE_ONLY_DIAGNOSTIC` and `D_MARKET_RESIDUAL_DIAGNOSTIC` are named tracks. They are not same-track G on/off ablations and are not used to pick a champion.

## Isolation and event口径

Signed checkpoints reject a different profile, horizon, registry version, data hash, or label policy. Crash after a row append without a checkpoint replays the session and skips `committed_row_key`. Feature caches may be reused; scores must be recomputed.

Independent events now use trading-session adjacency. Weekend and holiday gaps are not new events. The count is 去重事件组数. `independent_events` is null. 4514 remains `SUPERSEDED_EVENT_COUNT`.

## Ablation status

The first 24-theme balanced/mid pass is executed from the frozen B0 factor tape after this registration. Pairing tables, event revision, theme cards, and current-head CI are required before this round is complete. “Not run” is not a pass.

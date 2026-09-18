# PR #174 algorithm round 1b

Head `184e8b75` records the driver path fix. The full-tape analysis ran from `0252d3f6`. Review anchor `70a57ce5b60acfedb7ac4337b13aa2ee3a627d5f`.
B0 and Round 1 files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.

## Isolation

- source_row_sha256 `b490ba6d83965a0c3fe60c76afab8205fc2a0d76cc6577cdf23cb838e447f75a` matches the frozen expected hash.
- Literal `b0_measurement_factor_rows` is rejected. `--limit` uses a prefix hash and a separate directory.
- run_signature `41acb6227d1f52c69262cac49bb46270cadbc062c38d0dd61433f9be0a589e92` binds tape hash, capability mask, variant definitions, event/pairing/stat rules, bootstrap seed 174, and code hashes. URL/CI metadata is outside the signature.

## Capability

- 96-cell matrix: 14 DATA_INSUFFICIENT, 82 can_score_without_G with actual_G_observed=false.
- Those 82 historical ABLATION_DROP_G runs are capability-confounded and were not re-run.
- PRICE_ONLY_DIAGNOSTIC is the ablation track. D_MARKET_RESIDUAL_DIAGNOSTIC is an alias, not a second experiment.

## Pairing and events

- Common-member rerank IC difference. Own-set IC subtraction is not a pair.
- Circular date-block bootstrap seed 174, 2000 repeats, H and 2H. Year tables are descriptive.
- PRICE_ONLY baseline theme-report fragments 15822 / overlap groups 6257. Global fragments 15120 / overlap 5984.
- 4514 remains SUPERSEDED_EVENT_COUNT. Group counts are not N_eff.

## Neighbors and cards

- Legacy N_M_PLUS_5PP / N_S_PLUS_5PP stay legacy_raw_weight_bump.
- True +5pp IDs: N_M_PLUS_5PP_REALLOC_V1, N_S_PLUS_5PP_REALLOC_V1. Floor 74*1.1=81.4; scorer pair-diff mean 0.0.
- 24 theme cards. Decisions: 47 CONTINUE_CANDIDATE, 31 CROSS_SECTION_THIN, 14 DATA_CAPABILITY_BLOCKED, 4 NO_ROBUST_INCREMENT. 0 champion. 0 family deleted.
- Pairing shards are one file per theme, each under 1MB.

## Counts

- registered 1152; rescore 16821216; feature reuse 2803536
- unique snapshots 1401768; inspected / transformed 4205304
- pairing cells 3456

## Not done

- Portfolio / raw corporate-action book remains unrun.
- New E / weekly / macro stay planned.
- Current-head GitHub CI is recorded after both test jobs succeed on this SHA.
- Holdout remains sealed. Search stops after these registered realloc/floor neighbors.

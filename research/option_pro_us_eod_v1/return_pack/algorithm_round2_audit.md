# PR #174 algorithm round 2

Head `e17d9c23e594de8cd0b6351e7da9ad1f4868ef55`. Review anchor `ddbd042004fd28b1cb84d8427ab920b1f0c71776`.
B0, Round 1, and Round 1b files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.

## Isolation

- source_row_sha256 `b490ba6d83965a0c3fe60c76afab8205fc2a0d76cc6577cdf23cb838e447f75a` matches the frozen expected hash.
- run_signature `e3fbb87eaf69ead59820284ec27d88df514b23d66667cb2582101b84cf1c779d` binds tape hash, stats v1.1, trim-to-timeline rule, variant definitions, seed 174, and this-round code hashes. URL/CI metadata is outside the signature.
- registered 1152; actual_rescore_calls 16821216; feature_reuse_hits 2803536; unique_snapshots 1401768; inspected 4205304. Counts are separate. No extra family-E / weekly / macro grid.
- daily paired-diff artifact sha256 `a19a07f4237ac7430365e2ea082a97ffce9b312b34284aa0dc9a2b9380cc0aff` (gitignored jsonl; public hash only).

## A. Guards

- Pairing rejects non-finite score/label before the common set. `common_n`, Spearman N, and member hash agree. Duplicate keys still raise when the first row is invalid.
- B0 scan: 4205304 rows, 0 non-finite score, 0 non-finite label. This round only adds a guard. It does not imply that prior pairing results are invalid.
- Bootstrap stitches then crops each replicate to the original timeline. All 5760 H/2H bands report `n_samples_per_replicate=1634`. Seed 174, 2000 repeats, H/2H unchanged as a choice.
- H/2H revision vs R1b: 3456 common cells, 3600 interval pairs, 3183 shifted after trim, 417 identical. Mean width 0.02907 -> 0.02911. Point ICs are not rewritten. No price re-download.
- Eight-factor DATA_INSUFFICIENT no longer overwrites an evaluable PRICE_ONLY card. All 24 D families are `D_MARKET_RESIDUAL_DIAGNOSTIC` aliases on the price track, not a second experiment. 0 DATA_CAPABILITY_BLOCKED cards.
- `mean_common_n` uses defined-pair dates only. 178 label-20 baselines with own_ic_days>=10 are not statistically_thin. 0 baselines are thin just because pair_days=0.
- Drop Δ<0 with H entirely below 0 is KEEP_FACTOR, not an abs-magnitude improvement. `next_neighbors` are objects with family/profile/score_horizon/label_horizon/track/variant/direction/reason.

## B. 24-theme decisions (balanced/mid, main label 20)

KEEP_FACTOR_EVIDENCE 15; NO_ROBUST_INCREMENT 20; CONTINUE_CANDIDATE 25; CROSS_SECTION_THIN 36; DATA_CAPABILITY_BLOCKED 0. No champion. No family deleted.

Reviewer starting hints on the revised tape:

- semiconductors A label 20: `N_M_PLUS_5PP_REALLOC_V1` mean -0.00421, H CI [-0.00860, 0.00019] crosses 0. Keep M baseline. Not a candidate slot.
- software A label 20: `PRICE_DROP_M` mean -0.00961, H CI [-0.04569, 0.02887]; `PRICE_DROP_R` mean -0.01430, H CI [-0.03483, 0.00620]. Both cross 0. Keep first. No slot spent here.
- Every D eight-factor cell remains DATA_INSUFFICIENT; the price track is EVALUABLE on all 24 themes. Do not write "please run the price track first".

Per-theme shards are in `algorithm_round2_pairing/` (max 493237 bytes). Compact rows with direction/Δ/intervals are in `algorithm_round2_evidence_table.json` and `algorithm_round2_overview.json`.

## C. Targeted candidates (max 3, all exploratory)

Registered from the revised ablation table only when label-20 H and 2H were both above 0 and yearly same-side. Unified M+/S+ was not reapplied to every theme.

1. `R2_automotive_D_residual_momentum_V_DOWNWEIGHT_5PP` — executed. Source `PRICE_DROP_V`. Δ mean +0.00866; H [0.00357, 0.01443]; 2H [0.00318, 0.01490]; pair_days 312; mean_common_n 10.0; insufficient_date_n 1321; eligible_signal_n 103. Still exploratory. Cross-section sits on the N=10 floor. Not a sealed winner.
2. `R2_etfs_D_residual_momentum_P_DOWNWEIGHT_5PP` — executed. Source `PRICE_DROP_P`. Δ mean +0.00135; H [-0.00017, 0.00282]; 2H [-0.00022, 0.00293]. After the -5pp downweight both intervals cross 0. Keep price baseline.
3. `R2_fintech_B_confirmed_base_breakout_S_DOWNWEIGHT_5PP` — executed. Source `PRICE_DROP_S`. Δ mean +0.00947; H [-0.00128, 0.02089]; 2H [-0.00087, 0.02025]; eligible_signal_n 0. Intervals cross 0. Keep price baseline. Missing matched execution prices; no NAV/win-rate invented.

Score-floor neighbor IC is expected to match the scorer; judge floors on eligible coverage, not scorer IC. T close to T+H close is a signal label only.

## D. Next stage

Only the automotive D / V small downweight still has same-side H and 2H after execution, and even that cell is N=10 with 1321 insufficient dates. Recommend at most that one limited continue, plus keep-baseline everywhere else. Do not expand family E, weekly, macro, or a full-market collect on the next call. No winner is allowed and none is claimed.

Current-head GitHub CI is recorded after the workflow finishes.

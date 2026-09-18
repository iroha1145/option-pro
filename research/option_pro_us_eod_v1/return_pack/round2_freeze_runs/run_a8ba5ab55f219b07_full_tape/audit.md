# PR #174 freeze and validate

Head `54ad5d7684d7c1eae086333ea756c7514a3f3635`. Review anchor `6159b2388d5bf36eefa7f7aba6c12455c3817585`. Protocol `us-eod-research-freeze-v1`.
B0 / Round 1 / Round 1b / Round 2 files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.

## Frozen object

- candidate `R2_automotive_D_residual_momentum_V_DOWNWEIGHT_5PP`
- V 0.05497251372678729 -> 0.004972513726787288 (requested -5pp, relative 0.9095454548158264)
- control PRICE_ONLY_DIAGNOSTIC / alias D_MARKET_RESIDUAL_DIAGNOSTIC
- ETF D/P historical HALVE_AT_BOUNDARY correction kept; future policy REJECT_INFEASIBLE
- fintech B/S keep baseline

## Validation

- timeline_n 1634; valid_pair_days 312; start 2023-03-06; end 2024-05-30; fraction 0.1909424724602203
- rejection_counts: DEFINED_PAIR 312, COMMON_N_BELOW_10 747, N0_OR_NO_COMMON 553, LABEL_NOT_MATURE 21, N0_NO_THEME_ROWS 1
- mean_common_n 10.0
- mean_delta 0.008663558663558665
- mean_baseline_ic_common 0.10365190365190365
- mean_variant_ic_common 0.1123154623154623
- 312 is the defined common-set pair count on 2023-03-06 through 2024-05-30, not the 2018-2024 tape length.
- The 10 names F, GM, LCID, LI, NIO, RIVN, STLA, TM, TSLA, XPEV are present on every valid day. Member hash is constant. Leave-one always drops N to 9.
- eligible_signal_records 103; matured_label 102; fragments 38; overlap_groups 14. Not 103 independent trades.
- H [0.00357467629010894, 0.01442819904110227]; 2H [0.0031815054211843195, 0.014897760658252279]; seed 174; 2000 repeats; n_samples_per_replicate 1634. Matches the R2 executed band.
- daily sha256 `bf25dae04f7b096f1028f791aa258bd17d2540be0435ecb0fe519fe88a66d25d`
- public table rows 312 (limit 500)
- execution_prices MISSING; nav_winrate_capacity NOT_INVENTED
- stop `FROZEN_EXPLORATORY_CANDIDATE`: H/2H incremental intervals stay positive on the seen window, but every leave-one member drops N below 10. Keep as a frozen exploratory candidate, not a winner.
- Two CIs above 0 are not a production winner. 2024Q2 mean_delta is negative and is retained.

## Yearly (all retained)

- 2023: n=208 mean_delta=0.010606060606060607 baseline_ic=0.07261072261072261 variant_ic=0.08321678321678322 (positive)
- 2024: n=104 mean_delta=0.00477855477855478 baseline_ic=0.16573426573426572 variant_ic=0.17051282051282052 (positive)

## Quarterly (all retained)

- 2023Q1: n=20 mean_delta=0.002424242424242422 baseline_ic=-0.05393939393939394 variant_ic=-0.051515151515151514 (positive)
- 2023Q2: n=62 mean_delta=0.007233626588465299 baseline_ic=-0.05180840664711632 variant_ic=-0.04457478005865103 (positive)
- 2023Q3: n=63 mean_delta=0.01924001924001924 baseline_ic=0.10437710437710439 variant_ic=0.12361712361712363 (positive)
- 2023Q4: n=63 mean_delta=0.007888407888407892 baseline_ic=0.20346320346320346 variant_ic=0.21135161135161137 (positive)
- 2024Q1: n=61 mean_delta=0.009538002980625937 baseline_ic=0.42513661202185793 variant_ic=0.4346746150024839 (positive)
- 2024Q2: n=43 mean_delta=-0.0019732205778717443 baseline_ic=-0.2022551092318534 variant_ic=-0.20422832980972513 (negative)

## Leave-one member (N=10 to N=9 is insufficient)

- F: present=312 lost_below_n=312 remain=0 thin=True
- GM: present=312 lost_below_n=312 remain=0 thin=True
- LCID: present=312 lost_below_n=312 remain=0 thin=True
- LI: present=312 lost_below_n=312 remain=0 thin=True
- NIO: present=312 lost_below_n=312 remain=0 thin=True
- RIVN: present=312 lost_below_n=312 remain=0 thin=True
- STLA: present=312 lost_below_n=312 remain=0 thin=True
- TM: present=312 lost_below_n=312 remain=0 thin=True
- TSLA: present=312 lost_below_n=312 remain=0 thin=True
- XPEV: present=312 lost_below_n=312 remain=0 thin=True

## Project gaps

- 三档三周期: 已实现未验证
- 十年以上数据: 未做
- 历史成员/退市/行业: 未做
- 独立综合层: 未做
- 经济执行与生产EOD接入: 未做

No production champion. No unseal. No new weight grid.

## Current-head GitHub CI

Research-head GitHub CI on `3b1d27e9` is terminal success:

- push: https://github.com/iroha1145/option-pro/actions/runs/35352505494
- pull_request: https://github.com/iroha1145/option-pro/actions/runs/35352511710

A later URL-stamp commit is docs-only and is not a new research revision.

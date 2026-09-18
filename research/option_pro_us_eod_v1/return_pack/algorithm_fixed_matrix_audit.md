# PR #174 fixed matrix and signal validation

Head `77c667bf70d4af9fdf995353de945c0fe74974dc`. Review anchor `c8cd25931f007ce826c7b1e3cd1a1726d48ffae0`. Protocol `us-eod-research-fixed-matrix-v1`.
B0 / Round 1 / Round 1b / Round 2 / freeze / readiness files are not rewritten.
Holdout 2024-07-01 stays sealed. No new weight search.

## Calendar

- version nyse-official-adhoc-v1 official_n 1633 spy_n 1633
- 2018-12-05 closed True
- canonical T+20 from 2024-04-01 2024-04-29

## Quality

- valid 213 invalid 0 insufficient 1 spy VALID
- isolated outliers ['CRWV']
- execution gates EXECUTION_GATES_UNVERIFIED

## 864 plan

- plan_n 864 structured 864 scored 288 score_pending 576
- pending_reason SCORE_HORIZON_FEATURES_NOT_IN_B0
- signature d5758d0c4cf4694274b6d55b956035b85673b4b004d18b198f8c38caf6d64fd5

## Selection vs background

- selection RECOVERED own-identical 1055 differ 24
- background equal_weight_close_to_close_current_list_background
- NEXT_DAY_CONFIRM earliest 2024-04-03
- p05 worst name that day under this lower-order convention, not a time-series portfolio VaR

## Gaps

- current-list multi-profile multi-horizon signal diagnostics can run now
- raw/split/dividend/real volume block economic execution
- backward history shorter than ten evaluable years blocks decade research
- historical master/delist/classification blocks full-market PIT claims

No production champion. No unseal. No new weight grid.

## Current-head GitHub CI

Research-head GitHub CI on `af5a1116` is terminal success:

- push: https://github.com/iroha1145/option-pro/actions/runs/35370139053
- pull_request: https://github.com/iroha1145/option-pro/actions/runs/35370143182

A later URL-stamp commit is docs-only and is not a new research revision.

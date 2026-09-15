"""Pre-registered comparison rules for this algorithm round.

Frozen before candidate results are computed. Do not swap the primary
horizon, Top-K, or cost scenario after seeing numbers.
"""

from __future__ import annotations

from datetime import date

from app.services.research.protocol import PRIMARY_HORIZON, RESEARCH_PROTOCOL_VERSION
from app.services.strength.scoring import FEATURE_VERSION, NORMALIZATION_VERSION, SCORE_VERSION


ALGORITHM_ROUND_ID = "algorithm-fresh-2026-09-14"
ALGORITHM_PROTOCOL_VERSION = "algorithm-round-protocol-v1"

# Design uses development only. Validation is reserved for one locked batch
# after candidates, thresholds, and code are frozen. Sealed stays closed.
DESIGN_SPLIT = "development"
RESERVED_VALIDATION_SPLIT = "validation"
DESIGN_START = date(2019, 1, 2)
DESIGN_END = date(2022, 12, 30)

PRIMARY_SCREENER_HORIZON = PRIMARY_HORIZON
PRIMARY_SCREENER_TOP_K = 10
AUX_SCREENER_TOP_K = (5, 10, 20)
PRIMARY_LABEL = "close_to_close_20d_excess_vs_universe"
TRADABLE_LABEL = "next_open_hold_20d"

COST_SCENARIOS_BPS = (5, 10, 25)
PRIMARY_COST_BPS = 10
COST_NOTE = (
    "10 bps is one-way per fill in simulate_long_only "
    "(buy open * (1+bps), sell open * (1-bps)). "
    "Round-trip is therefore 20 bps at the mid scenario. "
    "All survivors use the same three scenarios; no post-hoc cost pick."
)

MINIMUM_MEANINGFUL = {
    "screener_top10_paired_excess_vs_original": 0.002,
    "screener_acceptable_worst5pct_worsening": 0.005,
    "screener_acceptable_label_coverage_drop": 0.05,
    "c0_acceptable_vacancy_day_share": 0.20,
    "radar_overall_excess_tolerance_vs_raw": 0.002,
    "radar_mae_improvement": 0.005,
}

PRIMARY_METRICS = {
    "screener": (
        "Same-day same-eligible-pool Top10 20d excess vs universe, "
        "paired against original ranking on the same dates. "
        "Close/close is the ranking diagnostic. Next-open 20d hold is tradable."
    ),
    "radar": (
        "Same raw TRIGGERED opportunity set. Report confirmation rate, delay, "
        "price change to executable entry, executable 20d results for confirmed "
        "names, skip-as-zero overall result on all opportunities, fail rate, "
        "MAE/MFE, and missed upside among rejects."
    ),
    "risk": (
        "Concentration, market exposure, and exits are named separately. "
        "A smaller book from fewer names is risk-management, not ranking alpha."
    ),
}

CANDIDATES = (
    {
        "candidate_id": "A0",
        "layer": "ranking",
        "rule": "0.5 * score_mid + 0.5 * score_long; both family scores required",
        "not_changed": ["RSI", "sector_quota", "market_timing", "exits", "universe", "eligibility"],
    },
    {
        "candidate_id": "C0",
        "layer": "risk_concentration",
        "rule": "original ranking, Top10 max 2 per primary sector, fill in original order, vacancies stay vacant",
        "sector_map": "static current theme first-listing; unclassified share one bucket",
        "not_changed": ["ranking_formula", "RSI", "exits"],
    },
    {
        "candidate_id": "T1",
        "layer": "timing",
        "rule": "daily strong proxy on raw TRIGGERED: CLV>=0.70, rvol_daily_20med>=1.5, upper_shadow<=0.15, distance>=0.20 ATR",
        "executable_from": "T+1 open",
        "not_changed": ["screener_ranking", "T2"],
    },
    {
        "candidate_id": "T2",
        "layer": "timing",
        "rule": "T and T+1 closes both hold T-frozen resistance + T-frozen buffer",
        "executable_from": "T+2 open",
        "not_changed": ["screener_ranking", "T1"],
    },
    {
        "candidate_id": "F1",
        "layer": "ranking_filter",
        "rule": "original ranking after rs_spy_63d > 0; missing RS dropped",
        "why": "Top10 crash names looked like extended winners; test a wide relative-strength gate",
        "not_changed": ["ranking_formula", "A0", "exits"],
    },
    {
        "candidate_id": "F2",
        "layer": "ranking",
        "rule": "same-day percentiles of return_63d/rs/return_126d/return_252d, rebuild mid/long without macd, then A0",
        "why": "score_long and ath saturate in Top20; test whether fixed return caps hide ranking",
        "proxy_note": "macd_direction not in compact dump",
        "not_changed": ["RSI", "ath_scale", "exits"],
    },
)

HISTORICAL_TRIALS_ALREADY_RUN = (
    {
        "trial_id": "original-screener-balanced-all",
        "source": "PR #163 / 2026-09-14 findings.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "development Rank IC 0.037; Top-K weaker than 63d momentum",
    },
    {
        "trial_id": "baseline-momentum-63d",
        "source": "PR #163 / 2026-09-14 findings.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "head excess and ledger stronger than production ranking",
    },
    {
        "trial_id": "candidate-disable-market-fit",
        "source": "PR #163 experiment-registry.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "no incremental value",
    },
    {
        "trial_id": "candidate-unadjusted-min-price",
        "source": "PR #163 experiment-registry.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "no difference on already-filtered pool",
    },
    {
        "trial_id": "original-radar-daily-base-theme-universe",
        "source": "PR #163 / 2026-09-14 findings.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "20d vs SPY +1.12%, concentrated in 2020",
    },
    {
        "trial_id": "candidate-radar-exclude-chase-extended",
        "source": "PR #163 experiment-registry.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "slightly worse, not promoted",
    },
    {
        "trial_id": "original-combo-screener-then-radar",
        "source": "PR #163 experiment-registry.md",
        "status": "historical_report_not_current_rerun",
        "claimed": "no predictive increment",
    },
)

ALGORITHM_ROUND_PROTOCOL = {
    "protocol_version": ALGORITHM_PROTOCOL_VERSION,
    "parent_research_protocol": RESEARCH_PROTOCOL_VERSION,
    "round_id": ALGORITHM_ROUND_ID,
    "score_version": SCORE_VERSION,
    "feature_version": FEATURE_VERSION,
    "normalization_version": NORMALIZATION_VERSION,
    "design_split": DESIGN_SPLIT,
    "reserved_validation_split": RESERVED_VALIDATION_SPLIT,
    "sealed": "closed",
    "primary_screener_horizon": PRIMARY_SCREENER_HORIZON,
    "primary_screener_top_k": PRIMARY_SCREENER_TOP_K,
    "primary_label": PRIMARY_LABEL,
    "tradable_label": TRADABLE_LABEL,
    "primary_cost_bps": PRIMARY_COST_BPS,
    "cost_scenarios_bps": list(COST_SCENARIOS_BPS),
    "cost_note": COST_NOTE,
    "minimum_meaningful": MINIMUM_MEANINGFUL,
    "primary_metrics": PRIMARY_METRICS,
    "candidates": list(CANDIDATES),
    "historical_trials_already_run": list(HISTORICAL_TRIALS_ALREADY_RUN),
    "follow_up_budget": 2,
    "defaults_online": False,
}

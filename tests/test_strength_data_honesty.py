"""研究口径的诚实性（2026-07-27 审计批）。

每条测试对应一类此前用占位/哨兵值冒充真实测量的路径：
- 52 周高位必须有接近一年的真实样本（审计 2.6.3）；
- 板块聚合与板块筛选共用完整 theme_ids 成员口径（审计 2.6.4）；
- 市场广度有最低覆盖门槛（审计 2.6.2）；
- Finnhub 基本面补充不再抬高整行 data_quality（审计 2.1.2）；
- put/call 在 call 侧为 0 时是 None 而不是 99.0（审计 2.1.8）；
- 期权热度在无量无仓时如实缺失（审计 2.1.9）；
- 量价匹配把缺失成交量当未观测处理（审计 2.1.11）；
- 「1年分位」标签背后的分布确实取最近一年且有最低样本量（审计 2.6.11）。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.services import signals as signal_service
from app.services.strength import finnhub, market_regime, marketdata, scanner
from app.services.strength import vol_price_match as vpm
from app.services.strength.price_action import compute_price_action


def _history(days: int, *, start_price: float = 100.0) -> pd.DataFrame:
    index = pd.bdate_range("2023-01-02", periods=days, tz="America/New_York")
    prices = [start_price + step * 0.35 for step in range(days)]
    return pd.DataFrame(
        {
            "Open": prices,
            "High": [price * 1.01 for price in prices],
            "Low": [price * 0.99 for price in prices],
            "Close": prices,
            "Volume": [1_000_000.0 + step * 500.0 for step in range(days)],
        },
        index=index,
    )


# ── 52 周高位（2.6.3） ───────────────────────────────────────


def test_short_history_never_claims_a_52_week_high() -> None:
    hist = _history(130)
    row = scanner._feature_row(
        "YOUNG",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert row is not None
    # 130 根 K 线不构成一年：ath_proximity 必须缺失，而不是用短历史
    # 最高价冒充「52 周高位」。
    assert row["ath_proximity"] is None
    assert row["history_days"] == 130


def test_full_year_history_still_reports_ath_proximity() -> None:
    hist = _history(252)
    row = scanner._feature_row(
        "AGED",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert row is not None
    assert row["ath_proximity"] is not None
    assert row["ath_proximity"] == pytest.approx(100.0, abs=1.5)


def test_missing_ath_does_not_produce_the_52w_tag() -> None:
    hist = _history(130)
    feature = scanner._feature_row(
        "YOUNG",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert feature is not None
    intrinsic = scanner._intrinsic_row(
        feature,
        hist,
        range_feature={"status": "disabled", "version": "fixture"},
        range_mode="disabled",
    )
    market = {"score": None, "status": "insufficient_data", "confidence": 0.0}
    scored = scanner._score_rows([intrinsic], market, "balanced", 0.0)
    assert scored, "row must survive scoring"
    assert "接近52周高位" not in (scored[0].get("tags") or [])


# ── 板块聚合成员口径（2.6.4） ────────────────────────────────


def test_sector_strength_counts_every_theme_membership() -> None:
    def _row(ticker: str, themes: list[str], score: float) -> dict:
        return {
            "ticker": ticker,
            "sector_id": themes[0],
            "theme_ids": list(themes),
            "final_score": score,
            "return_20d": 0.10,
            "return_63d": 0.20,
            "return_126d": 0.30,
        }

    rows = [
        # BABA 先被 social_internet 认领，但仍属 china_adr
        _row("BABA", ["social_internet", "china_adr"], 80.0),
        _row("JD", ["china_adr"], 60.0),
        _row("NIO", ["automotive", "china_adr"], 40.0),
    ]
    sectors = {item["sector_id"]: item for item in scanner._sector_strength(rows)}

    china = sectors["china_adr"]
    assert china["count"] == 3, "china_adr 的均值必须包含被靠前主题认领的成员"
    assert china["avg_strength"] == pytest.approx((80.0 + 60.0 + 40.0) / 3, abs=0.1)
    leader_tickers = [leader["ticker"] for leader in china["leaders"]]
    assert "BABA" in leader_tickers

    assert sectors["social_internet"]["count"] == 1
    assert sectors["automotive"]["count"] == 1


# ── 市场广度覆盖门槛（2.6.2） ────────────────────────────────


def _closes(symbols: list[str], days: int) -> dict[str, pd.Series]:
    index = pd.bdate_range("2024-01-02", periods=days)
    series = pd.Series([100.0 + step * 0.5 for step in range(days)], index=index)
    return {symbol: series.copy() for symbol in symbols}


def test_breadth_percentages_require_minimum_coverage() -> None:
    # 只有 3 只 ETF 有数据：低于 ceil(11×0.6)=7 的门槛，读数必须缺失
    closes = _closes(list(market_regime.SECTOR_ETFS[:3]), 260)
    score, detail = market_regime._compute_breadth_score(closes)
    assert detail["sectors_above_50dma"] is None
    assert detail["sectors_above_200dma"] is None
    assert detail["sector_50dma_coverage"] == 3

    full = _closes(list(market_regime.SECTOR_ETFS), 260)
    _score_full, detail_full = market_regime._compute_breadth_score(full)
    assert detail_full["sectors_above_50dma"] == pytest.approx(100.0)
    assert market_regime.MIN_BREADTH_COVERAGE == math.ceil(
        len(market_regime.SECTOR_ETFS) * 0.6
    )


# ── Finnhub data_quality（2.1.2） ────────────────────────────


def test_finnhub_metrics_do_not_lift_row_data_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "ticker": "TEST",
        "data_quality": 40,
        "breakdown": {},
        "data_sources": {},
    }

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {
                "metric": {
                    "marketCapitalization": 1000.0,
                    "peTTM": 25.0,
                    "revenueGrowthTTMYoy": 12.0,
                    "epsGrowthTTMYoy": 10.0,
                    "netProfitMarginTTM": 20.0,
                    "roeTTM": 18.0,
                }
            }

        @staticmethod
        def raise_for_status() -> None:
            return None

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *exc) -> bool:
            return False

        def get(self, *args, **kwargs) -> "_Response":
            return _Response()

    monkeypatch.setattr(finnhub.httpx, "Client", _Client)
    monkeypatch.setattr(finnhub, "_CACHE", {})
    monkeypatch.setattr(
        finnhub,
        "get_settings",
        lambda: type(
            "S",
            (),
            {
                "finnhub_api_key": "test-key",
                "finnhub_enrich_limit": 5,
                "finnhub_base_url": "https://finnhub.test/api/v1",
                "request_timeout": 5.0,
            },
        )(),
    )

    status = finnhub.enrich_rows_with_finnhub([row])

    assert status["enriched"] == 1
    assert row["fundamental_score"] is not None
    # 基本面没有填补任何价格/量能因子：整行数据质量保持真实覆盖率。
    assert row["data_quality"] == 40


# ── put/call 与期权热度哨兵（2.1.8 / 2.1.9） ─────────────────


def test_put_call_ratio_is_none_when_call_side_is_zero() -> None:
    payload = {
        "optionSymbol": ["T-P1", "T-P2"],
        "side": ["put", "put"],
        "volume": [200, 300],
        "openInterest": [0, 0],
        "iv": [0.4, 0.4],
        "dte": [30, 30],
        "updated": [1_700_000_000, 1_700_000_000],
    }
    metrics = marketdata._score_option_payload(payload)
    assert metrics is not None
    assert metrics["put_call_volume"] is None, "99.0 哨兵不得再出现"
    assert metrics["call_volume"] == 0
    assert metrics["put_volume"] == 500


def test_option_heat_is_missing_without_volume_or_open_interest() -> None:
    payload = {
        "optionSymbol": ["T-C", "T-P"],
        "side": ["call", "put"],
        "volume": [0, 0],
        "openInterest": [0, 0],
        "iv": [0.35, 0.35],
        "dte": [30, 30],
        "updated": [1_700_000_000, 1_700_000_000],
    }
    metrics = marketdata._score_option_payload(payload)
    assert metrics is not None
    assert metrics["option_heat_score"] is None
    assert metrics["source_status"] == "insufficient_data"
    assert "volume" in metrics["missing_components"]
    assert "open_interest" in metrics["missing_components"]
    assert "flow_imbalance" in metrics["missing_components"]


# ── 量价匹配缺失成交量（2.1.11） ─────────────────────────────


def test_vol_price_match_treats_missing_volume_as_unobserved() -> None:
    hist = _history(90)
    tampered = hist.copy()
    # 最近 10 天成交量缺失：fillna(0) 时代这会把 recent 美元额压成 0，
    # 伪造「真空型收缩」；现在这些天应被剔除，判定与完整数据一致。
    tampered.iloc[-10:, tampered.columns.get_loc("Volume")] = float("nan")

    clean = vpm.compute_vol_price_match(hist)
    fixed = vpm.compute_vol_price_match(tampered)

    assert fixed["status"] == "active"
    assert fixed["setup_type"] != "vacuum" or clean["setup_type"] == "vacuum"
    # 缺失日被剔除后，量能压缩比来自真实观测（fillna(0) 时代这里≈0）。
    assert fixed["volume_compression"] is not None
    assert fixed["volume_compression"] > 0.5


def test_vol_price_match_short_after_filter_reports_not_enough_data() -> None:
    hist = _history(70)
    tampered = hist.copy()
    tampered.iloc[:30, tampered.columns.get_loc("Volume")] = float("nan")
    result = vpm.compute_vol_price_match(tampered)
    assert result["status"] == "not_enough_data"
    assert result["volume_range_ratio"] is None
    assert result["breakout_quality_adjustment"] == 0.0


# ── price_action 空态分数（2.1.10） ──────────────────────────


def test_price_action_empty_states_have_no_score() -> None:
    empty = compute_price_action(pd.DataFrame())
    assert empty["status"] == "missing_data"
    assert empty["score"] is None


# ── 「1年分位」样本约束（2.6.11） ────────────────────────────


def test_percentile_rank_uses_trailing_year_and_minimum_samples() -> None:
    short = pd.Series([1.0] * 10)
    assert signal_service._percentile_rank(short, 1.0) is None

    two_years = pd.Series(
        [10.0] * 252 + [1.0] * 251 + [2.0],
    )
    # 值 2.0 在最近 252 个观测（全是 1.0 与自身）里几乎是最高分位；
    # 若把两年历史全算进去（前 252 个 10.0），分位会被拉到 ~50。
    rank = signal_service._percentile_rank(two_years, 2.0)
    assert rank is not None
    assert rank > 99.0

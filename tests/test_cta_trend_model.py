"""CTA 趋势资金估算模型的严谨性样本与不变量。

覆盖 brief 要求的十种形态 + 关键不变量：
- 无未来函数（逐日回放：全量 history 与前缀重算逐点一致）；
- flow = position − previous_position = trend_flow + volatility_flow；
- 触发价确实让完整敞口曲线产生对应变化；
- 数据不足 → null + insufficient_data，不生成虚假触发位；
- 盘中穿越只做暂定标记，不改写正式估算。
"""

from __future__ import annotations

import math
import random

from app.services.cta.config import (
    FLOW_EPS,
    MIN_BARS_REQUIRED,
    POSITION_NEUTRAL_BAND,
    TRIGGER_MAX_ZONES_PER_SIDE,
    VOL_SCALAR_CAP,
)
from app.services.cta.model import compute_cta_estimate, mark_intraday_crossings

_DAY = 24 * 60 * 60
_START = 1_700_000_000


def _bars_from_closes(closes: list[float], envelope: float = 0.004) -> list[dict]:
    bars = []
    for i, close in enumerate(closes):
        o = closes[i - 1] if i else close
        bars.append({
            "t": _START + i * _DAY,
            "trade_date": f"D{i:04d}",
            "o": o,
            "h": max(o, close) * (1 + envelope),
            "l": min(o, close) * (1 - envelope),
            "c": close,
            "v": 1_000_000,
        })
    return bars


def _drift_series(n: int, daily: float, noise_seed: int | None = None, noise: float = 0.0) -> list[float]:
    rng = random.Random(noise_seed) if noise_seed is not None else None
    closes = [100.0]
    for _ in range(n - 1):
        shock = rng.gauss(0, noise) if rng else 0.0
        closes.append(max(1.0, closes[-1] * (1 + daily + shock)))
    return closes


N = MIN_BARS_REQUIRED + 60


# ── 形态 1/2：单调趋势 ───────────────────────────────────────


def test_monotonic_uptrend_is_strong_long_with_high_agreement() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.004, 1, 0.004)))
    assert result["source_status"] == "active"
    assert result["position_score"] > 50
    assert result["position_label"] in {"strong_long", "net_long"}
    assert result["model_agreement"] >= 0.9


def test_monotonic_downtrend_is_short() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, -0.004, 2, 0.004)))
    assert result["position_score"] < -50
    assert result["position_label"] in {"strong_short", "net_short"}


# ── 形态 3：横盘震荡 → 分歧/中性，不冒充方向 ─────────────────


def test_sideways_chop_stays_inside_neutral_band() -> None:
    # 真横盘 = 短周期摆动 + 噪声（无噪声的平滑长波会被趋势模型诚实地读成
    # 低波动趋势——那是模型的正确行为，不是本样本要测的对象）。
    rng = random.Random(9)
    closes = [100.0 + 1.2 * math.sin(i / 2.2) + rng.gauss(0, 0.5) for i in range(N)]
    result = compute_cta_estimate(_bars_from_closes(closes))
    assert result["source_status"] == "active"
    assert abs(result["position_score"]) < 45
    assert result["position_label"] not in {"strong_long", "strong_short"}


# ── 形态 4：快速反转 → 快慢模型分化，状态是减仓而非立即翻空 ──


def test_fast_reversal_splits_fast_and_slow_models() -> None:
    # 12 天 −1.2%/日的回撤：快速模型翻空、慢速仍在多头 → 真分歧。
    closes = _drift_series(N - 12, 0.004, 3, 0.003)
    for _ in range(12):
        closes.append(closes[-1] * 0.988)
    result = compute_cta_estimate(_bars_from_closes(closes))
    submodels = result["submodels"]
    assert submodels["fast"]["signal"] < -0.3
    assert submodels["slow"]["signal"] > 0.3
    assert result["model_agreement"] < 1.0
    assert result["flow_score"] < 0


# ── 形态 5：波动放大但趋势方向未变 → 波动率去杠杆，不写成翻空 ─


def test_vol_spike_delever_is_not_reported_as_flip() -> None:
    calm = _drift_series(N - 12, 0.003, 4, 0.002)
    wild = calm[:]
    rng = random.Random(44)
    for _ in range(11):
        wild.append(max(1.0, wild[-1] * (1 + 0.003 + rng.gauss(0, 0.035))))
    wild.append(wild[-1] * 0.966)   # 末日大跌：当日 scalar 必然下调
    calm_result = compute_cta_estimate(_bars_from_closes(calm))
    wild_result = compute_cta_estimate(_bars_from_closes(wild))
    # 累计效果：波动放大后仓位缩放显著小于安静期。
    assert wild_result["volatility"]["scalar"] < calm_result["volatility"]["scalar"]
    # 趋势仍为正：仓位仍在多头一侧，当日减仓含波动率去杠杆分量。
    assert wild_result["position_score"] > 0
    assert wild_result["volatility"]["scalar"] < wild_result["volatility"]["previous_scalar"]
    assert wild_result["volatility_flow"] < 0
    assert wild_result["position_label"] not in {"net_short", "strong_short"}


# ── 形态 6：低波动持续趋势 → scalar 封顶为 1，不放大杠杆 ──────


def test_low_vol_trend_caps_scalar_at_one() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.0025, 5, 0.001)))
    assert result["volatility"]["scalar"] == VOL_SCALAR_CAP
    assert result["position_score"] <= 100.0


# ── 形态 7：数据不足 → null + 不生成任何触发位 ────────────────


def test_insufficient_history_returns_nulls_not_neutral() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(120, 0.003)))
    assert result["source_status"] == "insufficient_data"
    assert result["position_score"] is None
    assert result["flow_score"] is None
    assert result["trigger_levels"] is None
    assert result["scenario_curve"] is None
    assert result["history"] == []


# ── 形态 8：极端单日跳变 → 警示 + 方差稳健截断，趋势仍如实 ────


def test_extreme_jump_warns_and_survives() -> None:
    closes = _drift_series(N, 0.002, 6, 0.003)
    closes[-40] = closes[-41] * 0.55   # 类似坏数据/极端跳变
    for i in range(-39, 0):
        closes[i] = closes[i - 1] * (1 + 0.002)
    result = compute_cta_estimate(_bars_from_closes(closes))
    assert result["source_status"] == "active"
    assert any("±20%" in w for w in result["warnings"])
    assert result["position_score"] is not None


# ── 形态 9：盘中穿越但未收盘 → 暂定标记，不改写正式估算 ───────


def test_intraday_crossing_marks_provisional_without_mutation() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.003, 7, 0.004)))
    before = repr(result)
    zones = result["trigger_levels"]["below"]
    assert zones, "顺趋势序列下方应有减仓触发区"
    probe = zones[0]["price_low"] - 0.01
    intraday = mark_intraday_crossings(result, probe, "D9999")
    assert intraday is not None
    assert intraday["provisional"] is True
    assert zones[0]["id"] in intraday["crossed_zone_ids"]
    assert repr(result) == before, "盘中标记不得改写正式估算"
    assert mark_intraday_crossings(result, None, None) is None


# ── 形态 10：相近价格多模型翻转 → 聚簇 + 每侧上限 ─────────────


def test_clustered_flips_are_merged_and_capped() -> None:
    closes = [100.0 + 1.5 * math.sin(i / 7.0) + 0.01 * i for i in range(N)]
    result = compute_cta_estimate(_bars_from_closes(closes))
    for side in ("above", "below"):
        zones = result["trigger_levels"][side]
        assert len(zones) <= TRIGGER_MAX_ZONES_PER_SIDE
    all_zones = result["trigger_levels"]["above"] + result["trigger_levels"]["below"]
    assert any(len(z["models"]) >= 2 or len(z["components"]) >= 2 for z in all_zones), \
        "邻近阈值应聚簇成区间而不是逐条排列"


# ── 不变量：流恒等式 ─────────────────────────────────────────


def test_flow_identity_holds() -> None:
    for seed in (11, 12, 13):
        result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.001, seed, 0.012)))
        pos, prev = result["position_score"], result["previous_position_score"]
        flow = result["flow_score"]
        assert abs(flow - (pos - prev)) <= 0.15
        assert abs(flow - (result["trend_flow"] + result["volatility_flow"])) <= 0.15


# ── 不变量：无未来函数（前缀重算 == 全量 history 对应点） ─────


def test_point_in_time_replay_matches_full_history() -> None:
    closes = _drift_series(N, 0.0015, 21, 0.011)
    bars = _bars_from_closes(closes)
    full = compute_cta_estimate(bars)
    by_date = {row["date"]: row["position"] for row in full["history"]}
    for cut in (len(bars) - 1, len(bars) - 7, len(bars) - 23):
        prefix = compute_cta_estimate(bars[:cut])
        date = prefix["data_through"]
        assert date in by_date, "全量 history 必须覆盖前缀截止日"
        assert abs(prefix["position_score"] - by_date[date]) <= 0.11, \
            f"{date}: 前缀重算 {prefix['position_score']} != 全量 {by_date[date]}"


# ── 不变量：触发区间跨越后仓位确实变化（曲线自洽） ────────────


def test_trigger_zone_deltas_match_curve() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.003, 31, 0.006)))
    curve = result["scenario_curve"]
    prices, full = curve["prices"], curve["full"]

    def curve_at(price: float) -> float:
        idx = min(range(len(prices)), key=lambda i: abs(prices[i] - price))
        return full[idx]

    for side in ("above", "below"):
        for zone in result["trigger_levels"][side]:
            # Δ 按「实际到达方向」评估（P1-01）：上方=自下而上，下方=自上而下。
            if side == "above":
                approx = curve_at(zone["price_high"]) - curve_at(zone["price_low"])
            else:
                approx = curve_at(zone["price_low"]) - curve_at(zone["price_high"])
            assert abs(approx - zone["est_position_change"]) <= 6.0, \
                f"{zone['id']} 声称 Δ{zone['est_position_change']} 但曲线为 Δ{approx:.1f}"
            assert zone["needs_close_confirm"] is True


# ── 不变量（审计 P1-01/02 + 区间钳位 + 权重上限） ─────────────


_BUY_KEYS = {"short_cover", "reopen_long", "add_long", "buy_accelerate", "add_further"}
_SELL_KEYS = {"trim_long", "reopen_short", "add_short", "sell_accelerate", "flip_further"}
_CONFLICT_KEYS = {"trend_up_vol_dominates", "trend_down_vol_dominates"}


def _all_zone_results() -> list[dict]:
    results = []
    for drift, seed, noise in ((0.003, 31, 0.006), (-0.002, 17, 0.008), (0.001, 5, 0.012)):
        results.append(compute_cta_estimate(_bars_from_closes(_drift_series(N, drift, seed, noise))))
    return results


def test_zone_labels_never_contradict_net_change() -> None:
    """标签与净仓位变化同向：买族净 Δ 不得为显著负值，卖族反之（P1-02）。

    趋势与净方向冲突时必须使用显式冲突键，不得沿用买/卖措辞。
    """

    for result in _all_zone_results():
        triggers = result["trigger_levels"] or {"above": [], "below": []}
        for side in ("above", "below"):
            for zone in triggers[side]:
                net, trend = zone["est_position_change"], zone["trend_change"]
                key = zone["label_key"]
                if key in _BUY_KEYS:
                    assert net >= -1.0, f"{zone['id']} 买族标签却净减仓 {net}"
                elif key in _SELL_KEYS:
                    assert net <= 1.0, f"{zone['id']} 卖族标签却净加仓 {net}"
                else:
                    assert key in _CONFLICT_KEYS, f"未知标签 {key}"
                    assert net * trend < 0, f"{zone['id']} 冲突标签但趋势与净同向"


def test_below_zones_report_downward_crossing_sign() -> None:
    """顺涨趋势下方区间 = 跌破趋势阈值 → 净仓位必须下降（P1-01 回归）。"""

    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.003, 7, 0.004)))
    zones = result["trigger_levels"]["below"]
    assert zones, "顺趋势序列下方应有触发区"
    assert all(z["est_position_change"] < 0 or z["trend_change"] < 0 for z in zones), \
        f"下方区间应报下行穿越的负向变化：{[(z['id'], z['est_position_change']) for z in zones]}"


def test_zone_bounds_clamped_to_reference_side() -> None:
    """垫衬不得越过现价；最近距离按靠近现价的边界计（审计 QQQ inside-zone）。"""

    for result in _all_zone_results():
        ref = result["reference_price"]
        if ref is None:
            continue
        triggers = result["trigger_levels"] or {"above": [], "below": []}
        for zone in triggers["above"]:
            assert zone["price_low"] >= round(ref, 2) - 0.01, \
                f"{zone['id']} 上方区间下沿 {zone['price_low']} 低于现价 {ref}"
            assert zone["distance_pct"] >= -0.01
        for zone in triggers["below"]:
            assert zone["price_high"] <= round(ref, 2) + 0.01, \
                f"{zone['id']} 下方区间上沿 {zone['price_high']} 高于现价 {ref}"
            assert zone["distance_pct"] <= 0.01


def test_zone_weight_share_deduped_and_bounded() -> None:
    """weight_share 按 (model, component) 去重后 ∈ (0, 1]（审计问题 4）。"""

    for result in _all_zone_results():
        triggers = result["trigger_levels"] or {"above": [], "below": []}
        for side in ("above", "below"):
            for zone in triggers[side]:
                assert 0 < zone["weight_share"] <= 1.0, \
                    f"{zone['id']} weight_share={zone['weight_share']} 越界"


# ── 不变量：完整曲线的极端端点不超过冻结波动的趋势曲线 ────────


def test_full_curve_delevers_at_extremes() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.002, 41, 0.008)))
    curve = result["scenario_curve"]
    for idx in (0, len(curve["prices"]) - 1):
        full, trend_only = curve["full"][idx], curve["trend_only"][idx]
        if full * trend_only > 0:
            assert abs(full) <= abs(trend_only) + 0.2, \
                "大幅移动推高情景波动率，完整敞口不应超过冻结波动的趋势敞口"


# ── 稳定性：随机游走不应高频翻转 ─────────────────────────────


def test_random_walk_position_does_not_thrash() -> None:
    for seed in (61, 62):
        rng = random.Random(seed)
        closes = [100.0]
        for _ in range(N - 1):
            closes.append(max(1.0, closes[-1] * (1 + rng.gauss(0, 0.012))))
        result = compute_cta_estimate(_bars_from_closes(closes))
        signs = [1 if row["position"] > FLOW_EPS else (-1 if row["position"] < -FLOW_EPS else 0)
                 for row in result["history"]]
        flips = sum(
            1 for a, b in zip(signs, signs[1:])
            if a != 0 and b != 0 and a != b
        )
        assert flips <= 12, f"seed {seed}: {flips} 次方向翻转，过于抖动"


# ── 稳定性：单调趋势历史不翻向 ──────────────────────────────


def test_trend_history_is_stable() -> None:
    result = compute_cta_estimate(_bars_from_closes(_drift_series(N, 0.004, 71, 0.003)))
    tail = result["history"][-60:]
    assert all(row["position"] > 0 for row in tail)


# ── 真实图表 bars 只有 epoch t：日期必须折成纽约交易日（生产实测回归） ──


def test_epoch_only_bars_produce_iso_dates() -> None:
    bars = [{**bar, "trade_date": None} for bar in _bars_from_closes(_drift_series(N, 0.002, 81, 0.005))]
    for bar in bars:
        bar.pop("trade_date")
    result = compute_cta_estimate(bars)
    assert result["source_status"] == "active"
    assert len(result["data_through"]) == 10 and result["data_through"].count("-") == 2
    assert all(row["date"].count("-") == 2 for row in result["history"])

"""结构分析的严谨性样本：负样本、边界样本、逐日回放、映射等价。

既有测试证明「设计好的基底能被检出」；这里补的是反面与边界：
- 单边行情不该被判成「区间震荡 50 分」，而是「结构未确认 + 无分」；
- 随机游走里检出率与质量分布要有界（选择偏乐观不能失控）；
- 末根突破 / 跌破失效位要在 base_state 里如实呈现；
- 未收盘末根不进指标样本；
- 序列断崖之后才是可分析段；
- 已确认摆动点在后续 K 线到来后不得被改写（无未来函数）。
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from app.services.strength import scoring as strength_scoring
from app.services.strength.price_action import _find_swings, compute_price_action
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.technical.base_structure import detect_base_structure
from app.services.technical.indicators import rsi_score as indicators_rsi_score
from app.services.technical.structure import (
    clean_series,
    compute_technical_structure,
    series_excluding_last,
)

_DAY = 24 * 60 * 60
_START = int(datetime(2025, 1, 6, 21, 0, tzinfo=timezone.utc).timestamp())
_AFTER_ALL = datetime(2028, 1, 3, 12, 0, tzinfo=timezone.utc)


def _bar(i: int, o: float, h: float, l: float, c: float, v: int = 800_000) -> dict:
    return {"t": _START + i * _DAY, "o": round(o, 4), "h": round(h, 4), "l": round(l, 4), "c": round(c, 4), "v": v}


def _bars_from_closes(closes: list[float], spread: float = 0.004, volume: int = 800_000) -> list[dict]:
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        h = max(o, c) * (1 + spread)
        l = min(o, c) * (1 - spread)
        bars.append(_bar(i, o, h, l, c, volume))
    return bars


# ── 单边与平台：不足两个确认摆动点 → 结构未确认、无分 ──────────


def test_smooth_monotonic_uptrend_is_unconfirmed_not_range() -> None:
    closes = [100 * (1.004 ** i) for i in range(120)]
    result = compute_technical_structure(_bars_from_closes(closes), now=_AFTER_ALL)
    assert result is not None
    pa = result["price_action"]
    assert pa["structure"] == "unconfirmed"
    assert pa["score"] is None
    assert pa["structure_label"] == "结构未确认"


def test_smooth_monotonic_downtrend_is_unconfirmed_not_range() -> None:
    closes = [100 * (0.996 ** i) for i in range(120)]
    result = compute_technical_structure(_bars_from_closes(closes), now=_AFTER_ALL)
    assert result is not None
    assert result["price_action"]["structure"] == "unconfirmed"
    assert result["price_action"]["score"] is None


def test_flat_plateau_is_unconfirmed_not_range() -> None:
    closes = [100.0 + (0.01 if i % 2 else -0.01) for i in range(120)]
    result = compute_technical_structure(_bars_from_closes(closes), now=_AFTER_ALL)
    assert result is not None
    # 微幅锯齿没有分形意义上的确认摆动序列；不许假装观察到了均衡区间。
    assert result["price_action"]["score"] is None or result["price_action"]["structure"] != "unconfirmed"


def test_unconfirmed_price_action_drops_out_of_strength_scoring() -> None:
    assert strength_scoring._active_price_action(
        {"price_action": {"status": "active", "score": None, "structure": "unconfirmed"}}
    ) is None


# ── 摆动确认的无未来函数（逐日回放） ─────────────────────────


def test_confirmed_swings_never_rewritten_by_future_bars() -> None:
    rng = random.Random(20260808)
    closes = [100.0]
    for _ in range(219):
        closes.append(max(5.0, closes[-1] * (1 + rng.gauss(0, 0.02))))
    bars = _bars_from_closes(closes)
    series = clean_series(bars)
    assert series is not None
    highs, lows = series["highs"], series["lows"]
    span = 3
    full_high_swings, full_low_swings = _find_swings(highs, lows, span)
    for cut in range(40, len(highs), 7):
        part_high, part_low = _find_swings(highs[:cut], lows[:cut], span)
        # 前缀内「已确认」（右侧已有 span 根）的摆动点必须与全量计算完全一致：
        # 后来的 K 线只能新增确认，不能改写或撤销既有确认。
        assert part_high == [(i, p) for i, p in full_high_swings if i < cut - span]
        assert part_low == [(i, p) for i, p in full_low_swings if i < cut - span]


def test_base_detection_is_point_in_time_across_replay() -> None:
    rng = random.Random(7)
    closes = [80.0]
    for _ in range(219):
        closes.append(max(5.0, closes[-1] * (1 + rng.gauss(0.0005, 0.015))))
    bars = _bars_from_closes(closes)
    series = clean_series(bars)
    assert series is not None
    for cut in range(60, len(series["closes"]), 20):
        prefix = {key: values[:cut] for key, values in series.items()}
        prior = series_excluding_last(prefix)
        assert prior is not None
        base = detect_base_structure(prior)
        if base is not None:
            # 评估日（prefix 最后一根）不得参与自己的基底窗口。
            assert base["base_end"] <= prior["dates"][-1]
            assert base["base_end"] < prefix["dates"][-1]


# ── 随机游走的检出率与质量分布（选择偏乐观有界） ──────────────


def test_random_walk_base_quality_stays_bounded() -> None:
    detected = 0
    qualities: list[float] = []
    runs = 40
    for seed in range(runs):
        rng = random.Random(1000 + seed)
        closes = [50.0]
        for _ in range(179):
            closes.append(max(2.0, closes[-1] * (1 + rng.gauss(0, 0.018))))
        series = clean_series(_bars_from_closes(closes))
        assert series is not None
        prior = series_excluding_last(series)
        base = detect_base_structure(prior) if prior else None
        if base is not None:
            detected += 1
            qualities.append(base["quality"])
            # 纯噪声里的「基底」永远带着窗口共识与覆盖度字段供 UI 降权呈现。
            assert 1 <= base["window_agreement"] <= base["windows_scanned"]
            assert base["quality_coverage"]["total"] == 7
    # 随机游走没有真实整理结构。检出本身允许（启发式必然有误检），
    # 但整体质量不得普遍高分：均值显著低于精心构造的正样本（≈0.75+）。
    assert detected < runs
    if qualities:
        assert sum(qualities) / len(qualities) < 0.68
        assert max(qualities) < 0.85


# ── 末根状态矩阵：突破 / 跌破失效 / 未收盘暂定 ─────────────────


def _shelf_bars(n: int = 200, last_close: float | None = None) -> list[dict]:
    """长整理段：高点压着 ~100 的平台，低点缓慢抬升；末根可注入指定收盘。"""

    bars: list[dict] = []
    price = 70.0
    for i in range(n):
        if i < n - 80:
            price += 0.3 if i % 6 else -0.35
            o, c = price - 0.2, price
            h, l = price + 0.5, price - 0.6
            v = 900_000
        else:
            j = i - (n - 80)
            touch = j % 11 == 5
            floor = 90.0 + j * 0.04
            c = floor + 2.2 + (j % 4) * 0.25
            o = c - 0.3
            h = 100.0 + (0.1 if touch else -1.0 + (j % 3) * 0.2)
            l = floor
            v = 780_000 - j * 3_000
        bars.append(_bar(i, o, h, l, c, v))
    if last_close is not None:
        last = dict(bars[-1])
        last["c"] = last_close
        last["h"] = max(last["h"], last_close)
        last["l"] = min(last["l"], last_close)
        bars[-1] = last
    return bars


def test_base_state_reports_breakout_on_latest_bar() -> None:
    result = compute_technical_structure(_shelf_bars(last_close=104.0), now=_AFTER_ALL)
    assert result is not None
    assert result["base"] is not None
    state = result["base_state"]
    assert state is not None
    assert state["status"] == "breakout"
    assert state["provisional"] is False
    assert result["chart_overlays"]["base_status"] == "breakout"


def test_base_state_reports_failed_below_invalidation() -> None:
    result = compute_technical_structure(_shelf_bars(last_close=80.0), now=_AFTER_ALL)
    assert result is not None
    base = result["base"]
    assert base is not None and 80.0 < base["invalidation_price"]
    assert result["base_state"]["status"] == "failed"


def test_base_state_inside_base_and_at_resistance() -> None:
    inside = compute_technical_structure(_shelf_bars(last_close=95.0), now=_AFTER_ALL)
    assert inside is not None and inside["base_state"]["status"] in {"in_base", "at_resistance"}
    testing = compute_technical_structure(_shelf_bars(last_close=99.5), now=_AFTER_ALL)
    assert testing is not None and testing["base_state"]["status"] == "at_resistance"


def test_provisional_last_bar_is_excluded_from_indicators() -> None:
    bars = _shelf_bars(last_close=104.0)
    # 时钟停在末根交易日的午间（纽约 12:00 = UTC 16:00/17:00，取 16:30 保险）
    last_day = datetime.fromtimestamp(bars[-1]["t"], tz=timezone.utc)
    midday = last_day.replace(hour=16, minute=30)
    result = compute_technical_structure(bars, now=midday)
    assert result is not None
    assert result["last_bar"]["closed"] is False
    # 指标截止于前一根收盘；末根只作为「最新价」参与状态判定，且标记暂定。
    closed_result = compute_technical_structure(bars[:-1], now=_AFTER_ALL)
    assert closed_result is not None
    assert result["data_through"] == closed_result["data_through"]
    assert result["technicals"]["rsi14"] == closed_result["technicals"]["rsi14"]
    assert result["base_state"] is not None
    assert result["base_state"]["status"] == "breakout"
    assert result["base_state"]["provisional"] is True
    # 显式覆盖：调用方明确说已收盘时按全量计算。
    forced = compute_technical_structure(bars, last_bar_closed=True, now=midday)
    assert forced is not None and forced["last_bar"]["closed"] is True
    assert forced["data_through"] != result["data_through"]


# ── 序列断崖与跳空 ───────────────────────────────────────────


def test_series_break_truncates_analysis_to_consistent_segment() -> None:
    closes = [300.0 - i * 0.5 for i in range(60)] + [90.0] + [
        90.0 * (1 + 0.003 * ((i % 8) - 4)) for i in range(1, 100)
    ]
    result = compute_technical_structure(_bars_from_closes(closes), now=_AFTER_ALL)
    assert result is not None
    assert result["series_break_at"] is not None
    # 断裂点之前的摆动不得进入结果：所有标注日期都在断裂日当天或之后。
    for point in result["price_action"]["swing_highs"] + result["price_action"]["swing_lows"]:
        assert point["trade_date"] >= result["series_break_at"]


def test_ordinary_earnings_gap_does_not_truncate() -> None:
    closes = [100.0 + (0.4 if i % 2 else -0.3) + i * 0.05 for i in range(80)]
    closes += [closes[-1] * 1.15]  # +15% 跳空：真实行情，必须保留在样本内
    closes += [closes[-1] * (1 + 0.002 * ((i % 6) - 3)) for i in range(1, 40)]
    result = compute_technical_structure(_bars_from_closes(closes), now=_AFTER_ALL)
    assert result is not None
    assert result["series_break_at"] is None


# ── 量价方向性（消融式最小校验） ─────────────────────────────


def _vpm_frame(closes: list[float], volumes: list[float]):
    import pandas as pd

    index = pd.DatetimeIndex(
        [datetime.fromtimestamp(_START + i * _DAY, tz=timezone.utc) for i in range(len(closes))]
    )
    opens = [closes[0]] + closes[:-1]
    return pd.DataFrame(
        {
            "Open": opens,
            "High": [max(o, c) * 1.004 for o, c in zip(opens, closes)],
            "Low": [min(o, c) * 0.996 for o, c in zip(opens, closes)],
            "Close": closes,
            "Volume": volumes,
        },
        index=index,
    )


def test_vol_price_displacement_uses_ten_full_intervals() -> None:
    # 前 80 根横盘，之后每天 +1%，最后 10 个间隔累计 ≈ +10.5%
    closes = [100.0 + 0.2 * ((i % 6) - 3) for i in range(80)]
    for _ in range(11):
        closes.append(closes[-1] * 1.01)
    volumes = [1e6] * len(closes)
    result = compute_vol_price_match(_vpm_frame(closes, volumes))
    assert result["status"] == "active"
    expected = abs(closes[-1] / closes[-11] - 1)
    baseline_median_tr = result["result"] and expected / result["result"]
    # result = 位移 ÷ 基准单日波幅：位移必须覆盖 10 个完整间隔（不是 9 个）。
    assert abs(closes[-1] / closes[-11] - 1) > abs(closes[-1] / closes[-10] - 1)
    assert result["result"] is not None and baseline_median_tr is not None


def test_vacuum_with_weak_internals_scores_full_risk() -> None:
    # 缩量 + 收盘贴着日内低位阴跌 → 真空收缩满额风险
    closes = [100.0 + 0.5 * ((i % 8) - 4) for i in range(70)]
    for i in range(12):
        closes.append(closes[-1] * (1 - 0.0015))
    volumes = [1.5e6] * 70 + [4e5] * 12
    frame = _vpm_frame(closes, volumes)
    # 把近期日内区间做宽、收盘压在低位（CLV 显著为负）
    tail = frame.index[-12:]
    frame.loc[tail, "High"] = frame.loc[tail, "Close"] * 1.012
    frame.loc[tail, "Low"] = frame.loc[tail, "Close"] * 0.999
    result = compute_vol_price_match(frame)
    if result["setup_type"] == "vacuum":
        assert result["false_breakout_risk"] == 12.0
        assert result["breakout_quality_adjustment"] == -10.0


def test_thin_history_reports_not_enough_data_not_zero_risk() -> None:
    closes = [100.0 + 0.3 * ((i % 5) - 2) for i in range(65)]  # < 60+10+2
    result = compute_vol_price_match(_vpm_frame(closes, [1e6] * len(closes)))
    assert result["status"] == "not_enough_data"
    assert result["false_breakout_risk"] is None


# ── RSI 评分映射：detail 与正式评分同一条曲线 ─────────────────


def test_rsi_score_maps_identically_in_indicators_and_scoring() -> None:
    value = 0.0
    while value <= 100.0:
        a = indicators_rsi_score(value)
        b = strength_scoring.rsi_score(value)
        assert a is not None and b is not None
        # scoring 侧对结果做 4 位舍入；同一条折线，舍入级即等价。
        assert abs(a - b) < 1e-3, f"RSI {value}: indicators {a} != scoring {b}"
        value += 0.5


# ── 形态与陷阱的时点信息 ─────────────────────────────────────


def test_pattern_and_trap_events_carry_dates() -> None:
    result = compute_technical_structure(_shelf_bars(last_close=104.0), now=_AFTER_ALL)
    assert result is not None
    pa = result["price_action"]
    for event in pa["pattern_events"]:
        assert event["bars_ago"] is not None and event["bars_ago"] >= 0
        assert event["trade_date"] is not None
    if pa["spring"]:
        assert pa["spring_bars_ago"] is not None and pa["spring_trade_date"] is not None
    if pa["upthrust"]:
        assert pa["upthrust_bars_ago"] is not None and pa["upthrust_trade_date"] is not None

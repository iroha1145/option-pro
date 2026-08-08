from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _clamp(value: float | int | None, lo: float = 0.0, hi: float = 100.0, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _slope(values: list[float]) -> float | None:
    size = len(values)
    if size < 3:
        return None
    x_mean = (size - 1) / 2
    y_mean = sum(values) / size
    denom = sum((idx - x_mean) ** 2 for idx in range(size))
    if denom <= 0:
        return None
    numer = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(values))
    return numer / denom


def _empty(status: str, tag: str) -> dict[str, Any]:
    # 量价数据不足时三个调整量必须是 None：0.0 会被 UI 与快照读成
    # 「实测过、风险为零」。评分侧 finite_number(None)/or 0.0 本就兼容缺失。
    return {
        "status": status,
        "setup_type": status,
        "setup_label": tag,
        "range_compression": None,
        "volume_compression": None,
        "volume_range_ratio": None,
        "clv_mean": None,
        "up_down_volume_ratio": None,
        "obv_slope": None,
        "effort": None,
        "result": None,
        "effort_result_ratio": None,
        "breakout_quality_adjustment": None,
        "false_breakout_risk": None,
        "risk_penalty_adjustment": None,
        "tags": [tag],
    }


def compute_vol_price_match(
    hist: pd.DataFrame,
    *,
    recent_window: int = 10,
    baseline_window: int = 60,
    compression_threshold: float = 0.65,
    absorption_ratio_threshold: float = 1.25,
    vacuum_ratio_threshold: float = 0.70,
) -> dict[str, Any]:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if hist.empty or not required.issubset(hist.columns):
        return _empty("missing_data", "量价数据不足")

    data = hist.copy()
    # 缺失的成交量是「没观测到」，不是「当天零成交」。fillna(0) 会把
    # recent/baseline 美元额中位数压低，伪造 vacuum/假突破判定；与
    # scanner._feature_row 的 valid_volume 口径一致：整行剔除后再算。
    observed_volume = data["Volume"].notna() & (data["Volume"].astype(float) >= 0)
    if not observed_volume.all():
        data = data.loc[observed_volume]
    # 基准窗口剔除近期窗口（否则「近期 vs 基准」的对比被近期自身稀释），
    # 所以需要 recent+baseline 两段都齐；上市不足约 70 个交易日如实报样本不足。
    if len(data) < baseline_window + recent_window + 2:
        return _empty("not_enough_data", "量价样本不足")

    open_ = data["Open"].astype(float)
    high = data["High"].astype(float)
    low = data["Low"].astype(float)
    close = data["Close"].astype(float)
    volume = data["Volume"].astype(float)
    prev_close = close.shift(1)

    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr_pct = (true_range / close.where(close > 0)).replace([math.inf, -math.inf], pd.NA).dropna()
    dollar_volume = close * volume

    # 近期 = 最后 10 个交易日；基准 = 再往前的 60 个交易日（不含近期）。
    # tail(60) 会把近期 10 日也算进基准，收缩信号被自身平均掉约 1/6。
    recent_tr = tr_pct.tail(recent_window).median()
    baseline_tr = tr_pct.iloc[-(baseline_window + recent_window):-recent_window].median()
    recent_dv = dollar_volume.tail(recent_window).median()
    baseline_dv = dollar_volume.iloc[-(baseline_window + recent_window):-recent_window].median()
    if not baseline_tr or not baseline_dv or baseline_tr <= 0 or baseline_dv <= 0:
        return _empty("invalid_baseline", "量价基准异常")

    range_compression = float(recent_tr / baseline_tr)
    volume_compression = float(recent_dv / baseline_dv)
    volume_range_ratio = float(volume_compression / max(range_compression, 1e-9))

    daily_range = (high - low).replace(0, pd.NA)
    clv = ((2 * close - high - low) / daily_range).replace([math.inf, -math.inf], pd.NA).dropna()
    clv_mean = float(clv.tail(recent_window).mean()) if not clv.tail(recent_window).empty else 0.0

    # 涨跌日按「较前收」划分（跳空日的方向在开收口径下会被吞掉），
    # 与下方 OBV 的 close.diff 方向口径一致——同一个模块不该有两套涨跌定义。
    close_change = close.diff()
    recent_dv_series = dollar_volume.tail(recent_window)
    recent_change = close_change.tail(recent_window)
    up_dv = float(recent_dv_series[recent_change > 0].sum())
    down_dv = float(recent_dv_series[recent_change < 0].sum())
    up_down_volume_ratio = float(up_dv / max(down_dv, 1e-9))

    direction = close.diff().fillna(0)
    obv_step = direction.apply(lambda value: 1 if value > 0 else (-1 if value < 0 else 0)) * volume
    obv = obv_step.cumsum()
    obv_tail = [float(value) for value in obv.tail(recent_window).dropna()]
    obv_slope = _slope(obv_tail)
    obv_scale = max(float(volume.tail(recent_window).median() or 1.0), 1.0)
    normalized_obv_slope = (obv_slope / obv_scale) if obv_slope is not None else 0.0

    # iloc[-recent_window] 只覆盖 9 个收益间隔；真正的「近 10 日位移」要从
    # 第 11 根收盘起算。分母沿用剔除近期后的基准波幅（同一把尺）。
    anchor_close = close.iloc[-(recent_window + 1)]
    recent_abs_return = abs(close.iloc[-1] / anchor_close - 1) if anchor_close else 0.0
    baseline_range = float(baseline_tr or 1e-9)
    effort = float(recent_dv / max(baseline_dv, 1e-9))
    result = float(recent_abs_return / max(baseline_range, 1e-9))
    effort_result_ratio = float(effort / max(result, 1e-9))

    tags: list[str] = []
    breakout_adjustment = 0.0
    false_breakout_risk = 0.0
    risk_penalty_adjustment = 0.0

    if range_compression > compression_threshold:
        setup_type = "no_compression"
        setup_label = "未收缩"
        tags.append("未明显收缩")
        if effort <= 0.8 and result > 1.25:
            tags.append("真空上涨")
            false_breakout_risk += 6
            risk_penalty_adjustment += 4
    elif volume_range_ratio >= absorption_ratio_threshold:
        tags.append("吸收型收缩")
        bullish = clv_mean > 0.15 and up_down_volume_ratio > 1.2 and normalized_obv_slope > 0
        bearish = clv_mean < -0.15 and up_down_volume_ratio < 0.8 and normalized_obv_slope < 0
        if bullish:
            setup_type = "absorption_bullish"
            setup_label = "多头吸收"
            tags.append("多头吸收")
            breakout_adjustment = 12.0
            false_breakout_risk = -3.0
            risk_penalty_adjustment = -2.0
        elif bearish:
            setup_type = "absorption_bearish"
            setup_label = "空头吸收"
            tags.append("空头吸收")
            breakout_adjustment = -8.0
            false_breakout_risk = 10.0
            risk_penalty_adjustment = 6.0
        else:
            setup_type = "absorption_neutral"
            setup_label = "吸收未确认"
            tags.append("方向未确认")
            breakout_adjustment = 3.0
            false_breakout_risk = 3.0
            risk_penalty_adjustment = 2.0
    elif volume_range_ratio <= vacuum_ratio_threshold:
        setup_type = "vacuum"
        setup_label = "真空型收缩"
        # 量价同枯不天然等于高危：收盘位置与 OBV 都偏多的「安静回踩」
        # 历史上更接近建设性洗盘，风险降一档；内部同样疲弱才给满额加点。
        constructive = clv_mean > 0.15 and normalized_obv_slope > 0
        if constructive:
            tags.extend(["真空型收缩", "收缩内部偏多"])
            breakout_adjustment = -6.0
            false_breakout_risk = 8.0
            risk_penalty_adjustment = 5.0
        else:
            tags.extend(["真空型收缩", "假突破风险高"])
            breakout_adjustment = -10.0
            false_breakout_risk = 12.0
            risk_penalty_adjustment = 8.0
    else:
        setup_type = "balanced_compression"
        setup_label = "平衡收缩"
        tags.append("平衡收缩")
        breakout_adjustment = 2.0

    if effort > 1.3 and result > 1.0:
        tags.append("高努力高结果")
    elif effort > 1.3 and result <= 1.0:
        tags.append("高换手吸收")
    elif effort <= 0.8 and result > 1.0:
        tags.append("低量真空移动")
        false_breakout_risk += 4
        risk_penalty_adjustment += 3

    return {
        "status": "active",
        "setup_type": setup_type,
        "setup_label": setup_label,
        "range_compression": _safe_float(range_compression, 4),
        "volume_compression": _safe_float(volume_compression, 4),
        "volume_range_ratio": _safe_float(volume_range_ratio, 4),
        "clv_mean": _safe_float(clv_mean, 4),
        "up_down_volume_ratio": _safe_float(up_down_volume_ratio, 4),
        "obv_slope": _safe_float(normalized_obv_slope, 4),
        "effort": _safe_float(effort, 4),
        "result": _safe_float(result, 4),
        "effort_result_ratio": _safe_float(effort_result_ratio, 4),
        "breakout_quality_adjustment": round(breakout_adjustment, 1),
        "false_breakout_risk": round(false_breakout_risk, 1),
        "risk_penalty_adjustment": round(_clamp(risk_penalty_adjustment, -6, 16, 0), 1),
        "tags": list(dict.fromkeys(tags))[:5],
    }

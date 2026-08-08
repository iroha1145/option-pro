"""CTA 趋势资金估算：纯计算模块（无 IO，一切从日线 bars 出发）。

设计要点（对应 config.py 的研究依据）：

1. **前向游走**：历史仓位用一次从头到尾的游走计算——评估第 i 天时只
   持有 i-1 及以前的运行状态（EWMA 均线、EWMA 方差、通道窗口），随后才
   把第 i 天并入状态。未来函数在结构上不可能发生。
2. **统一情景函数**：「当前仓位」= 情景函数在 P=最新收盘 的取值；
   「情景曲线」= 同一函数扫价格网格；「触发位」= 同一函数的解析断点。
   三者永远同源，不存在展示与计算两套口径。
3. **恒等流拆分**：position = 100 × trend × scalar（scalar ≤ 1，无截断），
   flow = trend_flow + vol_flow 严格成立：
   trend_flow = 100 × (T_t − T_{t−1}) × S_{t−1}
   vol_flow   = 100 × T_t × (S_t − S_{t−1})
4. **收盘语义**：情景问「下一交易日收于 P 会怎样」；未收盘末根不进
   任何状态，只用于「盘中已穿越（暂定）」标记。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.services.cta.config import (
    AGREEMENT_DIVERGENT,
    ATR_WINDOW,
    ComponentSpec,
    EWMA_SATURATION_SIGMA,
    FLOW_EPS,
    HISTORY_POINTS,
    METHOD_VERSION,
    MIN_BARS_REQUIRED,
    POSITION_NEUTRAL_BAND,
    POSITION_STRONG,
    SATURATION_SIGMA,
    SCENARIO_SPAN_PCT,
    SCENARIO_STEPS,
    SUBMODELS,
    SUBMODEL_ACTIVE_EPS,
    TARGET_VOL_ANNUAL,
    TRIGGER_CLUSTER_ATR,
    TRIGGER_MAX_ZONES_PER_SIDE,
    TRIGGER_MIN_DELTA,
    VOL_ANNUALIZE,
    VOL_EWMA_LAMBDA,
    VOL_RETURN_CLAMP,
    VOL_SCALAR_CAP,
    VOL_SCALAR_FLOOR,
    VOL_WARMUP_DAYS,
)


_NEW_YORK_TZ = ZoneInfo("America/New_York")


def _clip(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _ewma_alpha(span: int) -> float:
    return 2.0 / (span + 1.0)


# ── 运行状态（前向游走时增量维护） ───────────────────────────


@dataclass
class _WalkState:
    """截至「已并入的最后一根收盘」的全部运行状态。"""

    ewma: dict[tuple[int, int], tuple[float, float]]  # (fast,slow) -> (Ef, Es)
    var: float | None       # EWMA 日方差（预热完成前为 None）
    seed_returns: list[float]

    @classmethod
    def fresh(cls) -> "_WalkState":
        pairs = {
            (c.fast, c.slow)
            for m in SUBMODELS
            for c in m.components
            if c.kind == "ewma"
        }
        return cls(ewma={pair: (math.nan, math.nan) for pair in pairs}, var=None, seed_returns=[])

    def absorb(self, close: float, prev_close: float | None) -> None:
        for (fast, slow), (ef, es) in self.ewma.items():
            af, as_ = _ewma_alpha(fast), _ewma_alpha(slow)
            self.ewma[(fast, slow)] = (
                close if math.isnan(ef) else af * close + (1 - af) * ef,
                close if math.isnan(es) else as_ * close + (1 - as_) * es,
            )
        if prev_close is not None and prev_close > 0:
            r = _clip(close / prev_close - 1.0, -VOL_RETURN_CLAMP, VOL_RETURN_CLAMP)
            if self.var is None:
                self.seed_returns.append(r)
                if len(self.seed_returns) >= VOL_WARMUP_DAYS:
                    mean = sum(self.seed_returns) / len(self.seed_returns)
                    self.var = sum((x - mean) ** 2 for x in self.seed_returns) / len(self.seed_returns)
            else:
                self.var = VOL_EWMA_LAMBDA * self.var + (1 - VOL_EWMA_LAMBDA) * r * r

    def scenario_var(self, prev_close: float, price: float) -> float | None:
        if self.var is None or prev_close <= 0:
            return None
        r = _clip(price / prev_close - 1.0, -VOL_RETURN_CLAMP, VOL_RETURN_CLAMP)
        return VOL_EWMA_LAMBDA * self.var + (1 - VOL_EWMA_LAMBDA) * r * r


def _sigma_daily(var: float | None) -> float | None:
    if var is None or var <= 0:
        return None
    return math.sqrt(var)


def _vol_scalar(var: float | None) -> float | None:
    sigma = _sigma_daily(var)
    if sigma is None:
        return None
    annual = sigma * math.sqrt(VOL_ANNUALIZE)
    if annual <= 0:
        return VOL_SCALAR_CAP
    return _clip(TARGET_VOL_ANNUAL / annual, VOL_SCALAR_FLOOR, VOL_SCALAR_CAP)


# ── 分量信号（对情景价 P 的分段线性函数） ─────────────────────


def _component_signal(
    spec: ComponentSpec,
    closes: Sequence[float],
    state: _WalkState,
    price: float,
    sigma: float,
    atr: float,
    ref_price: float,
) -> float:
    n = len(closes)
    if spec.kind == "tsmom":
        base = closes[n - spec.horizon]
        if base <= 0:
            return 0.0
        norm = SATURATION_SIGMA * sigma * math.sqrt(spec.horizon)
        if norm <= 0:
            return 0.0
        return _clip((price / base - 1.0) / norm)
    if spec.kind == "ewma":
        ef, es = state.ewma[(spec.fast, spec.slow)]
        af, as_ = _ewma_alpha(spec.fast), _ewma_alpha(spec.slow)
        gap = (af * price + (1 - af) * ef) - (as_ * price + (1 - as_) * es)
        norm = EWMA_SATURATION_SIGMA * sigma * math.sqrt(spec.slow) * ref_price
        if norm <= 0:
            return 0.0
        return _clip(gap / norm)
    if spec.kind == "donchian":
        window = closes[n - spec.window:]
        high, low = max(window), min(window)
        mid = (high + low) / 2.0
        half = max((high - low) / 2.0, 0.25 * atr)
        if half <= 0:
            return 0.0
        return _clip((price - mid) / half)
    raise ValueError(spec.kind)


def _trend_signal(
    closes: Sequence[float],
    state: _WalkState,
    price: float,
    sigma: float,
    atr: float,
    ref_price: float,
) -> tuple[float, dict[str, dict[str, Any]]]:
    total = 0.0
    detail: dict[str, dict[str, Any]] = {}
    for model in SUBMODELS:
        weight_sum = sum(c.weight for c in model.components)
        signal = 0.0
        parts: list[dict[str, Any]] = []
        for comp in model.components:
            value = _component_signal(comp, closes, state, price, sigma, atr, ref_price)
            signal += value * (comp.weight / weight_sum)
            parts.append({
                "kind": comp.kind,
                "params": _component_params(comp),
                "weight": round(comp.weight / weight_sum, 4),
                "signal": round(value, 4),
            })
        total += signal * model.weight
        detail[model.key] = {
            "label": model.label,
            "weight": model.weight,
            "signal": round(signal, 4),
            "components": parts,
        }
    return total, detail


def _component_params(comp: ComponentSpec) -> str:
    if comp.kind == "tsmom":
        return f"{comp.horizon}d"
    if comp.kind == "ewma":
        return f"{comp.fast}/{comp.slow}"
    return f"{comp.window}d"


def _atr_through(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> float:
    n = len(closes)
    window = min(ATR_WINDOW, n - 1)
    if window < 1:
        return 0.0
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(n - window, n)
    ]
    return sum(trs) / len(trs)


# ── 情景评估（hist = 截至前一收盘的序列 + 运行状态） ──────────


def _positions_at(
    closes: Sequence[float],
    state: _WalkState,
    price: float,
    atr: float,
    *,
    frozen_scalar: float | None = None,
) -> tuple[float | None, float | None, float | None, dict[str, dict[str, Any]] | None]:
    """返回 (position, trend, scalar, submodel_detail)；数据不足时全 None。

    ``frozen_scalar`` 用于趋势触发曲线：波动率固定为当前估计，只看价格
    穿越趋势阈值的效果。
    """

    prev_close = closes[-1]
    sigma = _sigma_daily(state.var)
    if sigma is None or prev_close <= 0:
        return None, None, None, None
    trend, detail = _trend_signal(closes, state, price, sigma, atr, prev_close)
    if frozen_scalar is not None:
        scalar = frozen_scalar
    else:
        scalar = _vol_scalar(state.scenario_var(prev_close, price))
    if scalar is None:
        return None, None, None, None
    return 100.0 * trend * scalar, trend, scalar, detail


# ── 解析触发事件 ─────────────────────────────────────────────


def _component_break_prices(
    spec: ComponentSpec,
    closes: Sequence[float],
    state: _WalkState,
    sigma: float,
    atr: float,
    ref_price: float,
) -> list[dict[str, Any]]:
    """分量的翻转价（信号过零）与饱和价（响应到 ±1 后不再变化）。"""

    n = len(closes)
    events: list[dict[str, Any]] = []

    def add(price: float, kind: str) -> None:
        if math.isfinite(price) and price > 0:
            events.append({"price": price, "event": kind})

    if spec.kind == "tsmom":
        base = closes[n - spec.horizon]
        norm = SATURATION_SIGMA * sigma * math.sqrt(spec.horizon)
        add(base, "flip")
        add(base * (1 + norm), "saturate_up")
        add(base * (1 - norm), "saturate_down")
    elif spec.kind == "ewma":
        ef, es = state.ewma[(spec.fast, spec.slow)]
        af, as_ = _ewma_alpha(spec.fast), _ewma_alpha(spec.slow)
        denom = af - as_
        if abs(denom) > 1e-12:
            base_gap = (1 - af) * ef - (1 - as_) * es
            add(-base_gap / denom, "flip")
            norm = EWMA_SATURATION_SIGMA * sigma * math.sqrt(spec.slow) * ref_price
            add((norm - base_gap) / denom, "saturate_up")
            add((-norm - base_gap) / denom, "saturate_down")
    elif spec.kind == "donchian":
        window = closes[n - spec.window:]
        high, low = max(window), min(window)
        mid = (high + low) / 2.0
        half = max((high - low) / 2.0, 0.25 * atr)
        add(mid, "flip")
        add(mid + half, "saturate_up")
        add(mid - half, "saturate_down")
    return events


def _collect_events(
    closes: Sequence[float],
    state: _WalkState,
    sigma: float,
    atr: float,
    ref_price: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for model in SUBMODELS:
        weight_sum = sum(c.weight for c in model.components)
        for comp in model.components:
            for event in _component_break_prices(comp, closes, state, sigma, atr, ref_price):
                events.append({
                    **event,
                    "model": model.key,
                    "component": f"{comp.kind}_{_component_params(comp)}",
                    # 全局权重占比：子模型权重 × 分量在子模型内的权重
                    "weight": model.weight * comp.weight / weight_sum,
                })
    return events


_BUY_DEPTH_LABELS = ("buy_accelerate", "add_further")
_SELL_DEPTH_LABELS = ("sell_accelerate", "flip_further")


def _zone_label(rank: int, position: float, net: float, trend: float) -> str:
    """标签跟随**净仓位变化方向**，不再按「上方=买/下方=卖」硬编码。

    GPT-5.6-Pro 审计 P1-02：价格上冲若触发波动率去杠杆，净变化可以为负——
    把它叫「买盘加速」是错误语义。趋势方向与净方向相反时给显式冲突标签，
    由前端译成「趋势转多，但波动率去杠杆占优」。净变化近零（|net|<1）时按
    趋势方向选族——该区间的意义是趋势触发本身。
    """

    if net <= -1.0 and trend >= 1.0:
        return "trend_up_vol_dominates"
    if net >= 1.0 and trend <= -1.0:
        return "trend_down_vol_dominates"
    direction = net if abs(net) >= 1.0 else trend
    if direction >= 0:
        if rank == 0:
            if position <= -POSITION_NEUTRAL_BAND:
                return "short_cover"   # 净空时的首个买向区 = 空头回补
            if position < POSITION_NEUTRAL_BAND:
                return "reopen_long"   # 分歧/中性 = 重新加多
            return "add_long"          # 已净多 = 恢复/继续加仓
        return _BUY_DEPTH_LABELS[min(rank - 1, 1)]
    if rank == 0:
        if position >= POSITION_NEUTRAL_BAND:
            return "trim_long"         # 净多时的首个卖向区 = 多头减仓
        if position > -POSITION_NEUTRAL_BAND:
            return "reopen_short"      # 分歧/中性 = 重新加空
        return "add_short"             # 已净空 = 继续加空
    return _SELL_DEPTH_LABELS[min(rank - 1, 1)]


def _cluster_triggers(
    events: list[dict[str, Any]],
    *,
    side: str,
    ref_price: float,
    atr: float,
    position: float,
    curve_eval,
    trend_eval,
) -> list[dict[str, Any]]:
    """按价格聚簇 → 触发区间，附净仓位变化与趋势/波动归因。"""

    if not events:
        return []
    span = ref_price * SCENARIO_SPAN_PCT
    lo_bound, hi_bound = ref_price - span, ref_price + span
    scoped = sorted(
        (e for e in events if lo_bound <= e["price"] <= hi_bound
         and (e["price"] > ref_price) == (side == "above")
         and abs(e["price"] - ref_price) > 0.05 * atr),
        key=lambda e: e["price"],
    )
    if not scoped:
        return []
    gap = TRIGGER_CLUSTER_ATR * atr
    clusters: list[list[dict[str, Any]]] = [[scoped[0]]]
    for event in scoped[1:]:
        if event["price"] - clusters[-1][-1]["price"] <= gap:
            clusters[-1].append(event)
        else:
            clusters.append([event])

    pad = 0.5 * gap
    zones: list[dict[str, Any]] = []
    for cluster in clusters:
        # 垫衬不得越过现价（GPT-5.6-Pro 审计：QQQ 的「上方」区间下沿被垫到
        # 现价之下，总览却仍显示 +0.23% 距离）。钳位后区间恒在对应一侧，
        # 盘中穿越判断也不会因垫衬提前挂章。
        lo = min(e["price"] for e in cluster) - pad
        hi = max(e["price"] for e in cluster) + pad
        if side == "above":
            lo = max(lo, ref_price)
        else:
            hi = min(hi, ref_price)
        # 穿越方向随侧别（审计 P1-01）：上方区间自下而上到达，下方区间自上
        # 而下到达——Δ 一律按「实际到达方向」评估，下方不再复用上行差值。
        if side == "above":
            full_delta = curve_eval(hi) - curve_eval(lo)
            trend_delta = trend_eval(hi) - trend_eval(lo)
        else:
            full_delta = curve_eval(lo) - curve_eval(hi)
            trend_delta = trend_eval(lo) - trend_eval(hi)
        vol_delta = full_delta - trend_delta
        if abs(full_delta) < TRIGGER_MIN_DELTA and abs(trend_delta) < TRIGGER_MIN_DELTA:
            continue
        if abs(trend_delta) >= TRIGGER_MIN_DELTA and abs(vol_delta) >= TRIGGER_MIN_DELTA:
            kind = "mixed"
        elif abs(trend_delta) >= TRIGGER_MIN_DELTA:
            kind = "trend_flip"
        else:
            kind = "vol_delever"
        models = sorted({e["model"] for e in cluster}, key=lambda k: ["fast", "medium", "slow"].index(k))
        # 权重按 (model, component) 去重（审计：同一分量的翻转价+两个饱和价
        # 落进一簇会把同一份权重加三次，理论上能超 100%）。
        unique_weights = {(e["model"], e["component"]): e["weight"] for e in cluster}
        # 「最近距离」按靠近现价的边界计（不再用区间中点）：上方=下沿，下方=上沿。
        near_edge = lo if side == "above" else hi
        zones.append({
            "price_low": round(lo, 2),
            "price_high": round(hi, 2),
            "price": round((lo + hi) / 2, 2),
            "distance_pct": round((near_edge / ref_price - 1) * 100, 2),
            "models": models,
            "components": sorted({e["component"] for e in cluster}),
            "weight_share": round(min(1.0, sum(unique_weights.values())), 4),
            "est_position_change": round(full_delta, 1),
            "trend_change": round(trend_delta, 1),
            "vol_change": round(vol_delta, 1),
            "kind": kind,
            "needs_close_confirm": True,
        })
    zones.sort(key=lambda z: abs(z["est_position_change"]), reverse=True)
    zones = zones[:TRIGGER_MAX_ZONES_PER_SIDE]
    zones.sort(key=lambda z: z["price"], reverse=(side == "below"))
    for rank, zone in enumerate(zones):
        zone["rank"] = rank + 1
        zone["label_key"] = _zone_label(
            rank, position, zone["est_position_change"], zone["trend_change"]
        )
        zone["id"] = f"{side}-{rank + 1}"
    return zones


# ── 状态标签 ─────────────────────────────────────────────────


def _position_label(position: float, agreement: float) -> str:
    if position >= POSITION_STRONG:
        return "strong_long"
    if position >= POSITION_NEUTRAL_BAND:
        return "net_long"
    if position <= -POSITION_STRONG:
        return "strong_short"
    if position <= -POSITION_NEUTRAL_BAND:
        return "net_short"
    return "divergent" if agreement < AGREEMENT_DIVERGENT else "neutral"


def _flow_state(position: float, flow: float) -> str:
    if abs(flow) < FLOW_EPS:
        return "steady"
    if position >= POSITION_NEUTRAL_BAND:
        return "long_add" if flow > 0 else "long_trim"
    if position <= -POSITION_NEUTRAL_BAND:
        return "short_add" if flow < 0 else "short_cover"
    return "rebuilding" if flow > 0 else "reducing"


def _agreement_stats(
    detail: Mapping[str, Mapping[str, Any]], total_trend: float
) -> tuple[float, float]:
    """(加权同向占比, 表态权重覆盖)。不表态（|signal|≤ε）的子模型不进分母。

    审计口径澄清：同向占比只回答「表态的模型是否指向同一方向」，不反映趋势
    强弱；覆盖单独返回，UI 得以呈现「3/3 同向但趋势只有 +35」这类完整读数。
    """

    direction = 1.0 if total_trend >= 0 else -1.0
    active = [(m["weight"], m["signal"]) for m in detail.values() if abs(m["signal"]) > SUBMODEL_ACTIVE_EPS]
    if not active:
        return 0.0, 0.0
    active_weight = sum(w for w, _ in active)
    agree = sum(w for w, s in active if s * direction > 0)
    return round(agree / active_weight, 4), round(active_weight, 4)


# ── 单标的完整估算 ───────────────────────────────────────────


def compute_cta_estimate(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """输入图表契约形状的**已收盘**日线 bars（t/o/h/l/c/v），输出单标的估算。

    数据不足时返回 source_status=insufficient_data 且数值全 null——
    绝不用 0 或中性值冒充。
    """

    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    dates: list[str] = []
    warnings: list[str] = []
    for bar in bars:
        try:
            c = float(bar["c"])
            h = float(bar.get("h") or c)
            low = float(bar.get("l") or c)
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(c) and c > 0) or h < low:
            continue
        closes.append(c)
        highs.append(h)
        lows.append(low)
        # 真实图表 bars 只有 epoch t（无 trade_date）：按纽约时区折成交易日，
        # 否则 data_through/history 会把裸时间戳当日期下发（生产实测踩过）。
        trade_date = bar.get("trade_date")
        if isinstance(trade_date, str) and trade_date:
            dates.append(trade_date)
        else:
            raw_t = bar.get("t")
            if isinstance(raw_t, (int, float)) and math.isfinite(raw_t) and raw_t > 0:
                dates.append(
                    datetime.fromtimestamp(int(raw_t), tz=timezone.utc)
                    .astimezone(_NEW_YORK_TZ)
                    .date()
                    .isoformat()
                )
            else:
                dates.append(str(raw_t))

    coverage = {"bars": len(closes), "required": MIN_BARS_REQUIRED}
    if len(closes) < MIN_BARS_REQUIRED:
        return {
            "source_status": "insufficient_data",
            "coverage": coverage,
            "warnings": ["历史长度不足，未生成估算（不以中性值代替）"],
            "position_score": None,
            "previous_position_score": None,
            "flow_score": None,
            "trend_flow": None,
            "volatility_flow": None,
            "state": None,
            "position_label": None,
            "model_agreement": None,
            "trend_strength": None,
            "active_model_weight": None,
            "submodels": None,
            "volatility": None,
            "trigger_levels": None,
            "scenario_curve": None,
            "history": [],
            "reference_price": closes[-1] if closes else None,
            "data_through": dates[-1] if dates else None,
        }

    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and abs(closes[i] / closes[i - 1] - 1) > VOL_RETURN_CLAMP:
            warnings.append(f"窗口内存在超过 ±{VOL_RETURN_CLAMP:.0%} 的单日变动（{dates[i]}），方差更新已稳健截断")

    # 前向游走：state 永远只包含「已评估日之前」的信息。
    state = _WalkState.fresh()
    history: list[dict[str, Any]] = []
    walk_start = MIN_BARS_REQUIRED - 1
    prev_snapshot: tuple[float, float, float] | None = None   # (position, trend, scalar)
    last_snapshot: tuple[float, float, float] | None = None
    last_detail: dict[str, dict[str, Any]] | None = None
    for i, close in enumerate(closes):
        if i >= walk_start:
            hist = closes[:i]
            atr = _atr_through(highs[:i], lows[:i], closes[:i])
            position, trend, scalar, detail = _positions_at(hist, state, close, atr)
            if position is not None:
                history.append({"date": dates[i], "position": round(position, 1)})
                prev_snapshot = last_snapshot
                last_snapshot = (position, trend, scalar)
                last_detail = detail
        state.absorb(close, closes[i - 1] if i else None)

    if last_snapshot is None or last_detail is None:
        return {
            "source_status": "insufficient_data",
            "coverage": coverage,
            "warnings": warnings + ["波动率状态未完成预热"],
            "position_score": None,
            "previous_position_score": None,
            "flow_score": None,
            "trend_flow": None,
            "volatility_flow": None,
            "state": None,
            "position_label": None,
            "model_agreement": None,
            "trend_strength": None,
            "active_model_weight": None,
            "submodels": None,
            "volatility": None,
            "trigger_levels": None,
            "scenario_curve": None,
            "history": [],
            "reference_price": closes[-1],
            "data_through": dates[-1],
        }

    position, trend, scalar = last_snapshot
    if prev_snapshot is None:
        prev_position, prev_trend, prev_scalar = position, trend, scalar
    else:
        prev_position, prev_trend, prev_scalar = prev_snapshot
    flow = position - prev_position
    trend_flow = 100.0 * (trend - prev_trend) * prev_scalar
    vol_flow = 100.0 * trend * (scalar - prev_scalar)

    # 情景与触发：hist = 全部已收盘序列（state 此刻已含最后一根）。
    ref_price = closes[-1]
    sigma = _sigma_daily(state.var) or 0.0
    atr = _atr_through(highs, lows, closes)
    agreement, active_model_weight = _agreement_stats(last_detail, trend)

    def full_curve(price: float) -> float:
        value, _, _, _ = _positions_at(closes, state, price, atr)
        return value if value is not None else 0.0

    def trend_curve(price: float) -> float:
        value, _, _, _ = _positions_at(closes, state, price, atr, frozen_scalar=scalar)
        return value if value is not None else 0.0

    span = ref_price * SCENARIO_SPAN_PCT
    prices = [
        round(ref_price - span + 2 * span * i / (SCENARIO_STEPS - 1), 2)
        for i in range(SCENARIO_STEPS)
    ]
    curve = {
        "prices": prices,
        "full": [round(full_curve(p), 1) for p in prices],
        "trend_only": [round(trend_curve(p), 1) for p in prices],
    }

    events = _collect_events(closes, state, sigma, atr, ref_price)
    triggers = {
        "above": _cluster_triggers(
            events, side="above", ref_price=ref_price, atr=atr, position=position,
            curve_eval=full_curve, trend_eval=trend_curve,
        ),
        "below": _cluster_triggers(
            events, side="below", ref_price=ref_price, atr=atr, position=position,
            curve_eval=full_curve, trend_eval=trend_curve,
        ),
    }

    annual_vol = (sigma * math.sqrt(VOL_ANNUALIZE)) if sigma else None
    return {
        "source_status": "active",
        "coverage": coverage,
        "warnings": warnings,
        "position_score": round(position, 1),
        "previous_position_score": round(prev_position, 1),
        "flow_score": round(flow, 1),
        "trend_flow": round(trend_flow, 1),
        "volatility_flow": round(vol_flow, 1),
        "state": _flow_state(position, flow),
        "position_label": _position_label(position, agreement),
        "model_agreement": agreement,
        # 一致度只表方向同向；强弱与覆盖单列（GPT-5.6-Pro 审计：QQQ 三模型
        # 微幅为正也显 100%，视觉像高置信度）。趋势强度 = 波动率缩放前的
        # 加权趋势 ×100，与 position = 强度 × scalar 恒等。
        "trend_strength": round(100.0 * trend, 1),
        "active_model_weight": active_model_weight,
        "submodels": last_detail,
        "volatility": {
            "realized_annual": round(annual_vol, 4) if annual_vol else None,
            "target_annual": TARGET_VOL_ANNUAL,
            "scalar": round(scalar, 4),
            "previous_scalar": round(prev_scalar, 4),
            "ewma_lambda": VOL_EWMA_LAMBDA,
        },
        "trigger_levels": triggers,
        "scenario_curve": curve,
        "history": history[-HISTORY_POINTS:],
        "reference_price": ref_price,
        "data_through": dates[-1],
    }


def mark_intraday_crossings(
    estimate: Mapping[str, Any],
    intraday_price: float | None,
    intraday_date: str | None,
) -> dict[str, Any] | None:
    """盘中未收盘价对触发区的暂定穿越标记；绝不改写正式估算与历史。"""

    if intraday_price is None or not math.isfinite(intraday_price) or intraday_price <= 0:
        return None
    triggers = estimate.get("trigger_levels") or {}
    crossed: list[str] = []
    for side in ("above", "below"):
        for zone in triggers.get(side) or []:
            low, high = zone.get("price_low"), zone.get("price_high")
            if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
                continue
            if (side == "above" and intraday_price >= low) or (side == "below" and intraday_price <= high):
                crossed.append(str(zone.get("id")))
    return {
        "price": round(intraday_price, 2),
        "date": intraday_date,
        "provisional": True,
        "crossed_zone_ids": crossed,
    }


__all__ = ["METHOD_VERSION", "compute_cta_estimate", "mark_intraday_crossings"]

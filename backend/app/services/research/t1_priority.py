"""T1 as a daily attention reorder. Raw triggers are never dropped."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import fmean
from typing import Any, Mapping, Sequence

from app.services.research.algorithm_protocol import (
    T1_PRIORITY_DEFAULT_K,
    T1_PRIORITY_DIAGNOSTIC_K,
    T1_PRIORITY_RANK_PROXY,
)
from app.services.research.dataset import OfflineOHLCV
from app.services.research.metrics import paired_difference_ci, summarize_return_targets
from app.services.research.path_stats import (
    executable_open_return,
    mae_mfe_from_entry,
    trigger_to_entry_change,
    universe_open_return_mean,
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def research_attention_key(event: Mapping[str, Any]) -> tuple[float, str]:
    """Disclosed proxy. Not production alert_priority_score."""

    volume = _finite(event.get("volume"))
    ticker = str(event.get("ticker") or "")
    return (volume if volume is not None else -1.0, ticker)


def t1_confirmed(event: Mapping[str, Any]) -> bool:
    block = event.get("t1") or {}
    return block.get("status") == "active" and bool(block.get("confirmed"))


def rank_same_day(
    events: Sequence[Mapping[str, Any]],
    *,
    t1_first: bool,
) -> list[dict[str, Any]]:
    copies = [dict(event) for event in events]
    copies.sort(key=lambda item: research_attention_key(item), reverse=True)
    if t1_first:
        copies.sort(key=lambda item: (t1_confirmed(item), research_attention_key(item)), reverse=True)
    for rank, item in enumerate(copies, start=1):
        item["attention_rank"] = rank
        item["attention_proxy"] = T1_PRIORITY_RANK_PROXY["id"]
        item["t1_priority_boost"] = bool(t1_first and t1_confirmed(item))
    return copies


def take_attention_slots(ranked: Sequence[Mapping[str, Any]], *, k: int) -> list[dict[str, Any]]:
    return [dict(row) for row in ranked[:k]]


def _group_by_date(events: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        session = str(event.get("trading_date") or event.get("signal_date") or "")
        if session:
            grouped[session].append(dict(event))
    return dict(grouped)


def compare_t1_priority(
    dataset: OfflineOHLCV,
    events: Sequence[Mapping[str, Any]],
    *,
    k: int = T1_PRIORITY_DEFAULT_K,
    diagnostic_k: Sequence[int] = T1_PRIORITY_DIAGNOSTIC_K,
) -> dict[str, Any]:
    """Raw Top-K vs T1-priority + fill, same K, same research proxy."""

    days = _group_by_date(events)
    symbols = list(dataset.tickers())
    universe_cache: dict[tuple[str, str], float | None] = {}
    ks = tuple(dict.fromkeys((k, *diagnostic_k)))
    by_k: dict[str, dict[str, Any]] = {}

    def _exec(event: Mapping[str, Any]) -> tuple[float | None, float | None, dict[str, Any]]:
        session = str(event.get("trading_date") or "")
        ticker = str(event.get("ticker") or "")
        payload = executable_open_return(dataset, ticker, session, entry_lag_sessions=1)
        raw = _finite(payload.get("forward_return"))
        excess = None
        entry = payload.get("entry_date")
        exit_date = payload.get("exit_date")
        if raw is not None and entry and exit_date:
            cache_key = (str(entry), str(exit_date))
            if cache_key not in universe_cache:
                universe_cache[cache_key] = _finite(
                    universe_open_return_mean(dataset, symbols, entry, exit_date).get("mean")
                )
            universe = universe_cache[cache_key]
            if universe is not None:
                excess = raw - universe
        return raw, excess, payload

    for slot in ks:
        raw_daily: list[float] = []
        prio_daily: list[float] = []
        raw_excess_daily: list[float] = []
        prio_excess_daily: list[float] = []
        paired_excess: list[float] = []
        raw_event_excess: list[tuple[str, float]] = []
        prio_event_excess: list[tuple[str, float]] = []
        raw_mae: list[float] = []
        prio_mae: list[float] = []
        raw_mfe: list[float] = []
        prio_mfe: list[float] = []
        raw_incomplete = 0
        prio_incomplete = 0
        delays: list[float] = []
        t1_in_raw = 0
        t1_in_prio = 0
        slots_filled_raw = 0
        slots_filled_prio = 0
        missed_t1_by_raw = 0
        days_with_events = 0
        days_short_t1 = 0
        economic_fail_raw = 0
        economic_fail_prio = 0
        technical_fail_raw = 0
        technical_fail_prio = 0
        repeats_raw = 0
        repeats_prio = 0
        seen_raw: set[tuple[str, str]] = set()
        seen_prio: set[tuple[str, str]] = set()
        label_missing_raw = 0
        label_missing_prio = 0

        for session, items in sorted(days.items()):
            days_with_events += 1
            raw_ranked = rank_same_day(items, t1_first=False)
            prio_ranked = rank_same_day(items, t1_first=True)
            raw_sel = take_attention_slots(raw_ranked, k=slot)
            prio_sel = take_attention_slots(prio_ranked, k=slot)
            slots_filled_raw += len(raw_sel)
            slots_filled_prio += len(prio_sel)
            if sum(1 for event in items if t1_confirmed(event)) < slot:
                days_short_t1 += 1
            raw_day: list[float] = []
            prio_day: list[float] = []
            raw_ex_day: list[float] = []
            prio_ex_day: list[float] = []
            raw_tickers = {str(event.get("ticker") or "") for event in raw_sel}
            for event in raw_sel:
                if t1_confirmed(event):
                    t1_in_raw += 1
                key = (str(event.get("ticker") or ""), str(event.get("pivot_id") or ""))
                if key in seen_raw:
                    repeats_raw += 1
                seen_raw.add(key)
                t2 = event.get("t2") or {}
                if t2.get("hold_t1") is False:
                    technical_fail_raw += 1
                raw, excess, payload = _exec(event)
                if raw is None:
                    label_missing_raw += 1
                else:
                    raw_day.append(raw)
                    raw_event_excess.append((session, raw if excess is None else excess))
                    if raw < 0:
                        economic_fail_raw += 1
                if excess is not None:
                    raw_ex_day.append(excess)
                if payload.get("entry_date") and payload.get("exit_date"):
                    path = mae_mfe_from_entry(
                        dataset,
                        str(event.get("ticker") or ""),
                        payload["entry_date"],
                        payload["exit_date"],
                        entry_price=_finite(payload.get("entry_open")),
                    )
                    if path.get("status") == "active":
                        if path.get("mae") is not None:
                            raw_mae.append(float(path["mae"]))
                        if path.get("mfe") is not None:
                            raw_mfe.append(float(path["mfe"]))
                    else:
                        raw_incomplete += 1
                change = trigger_to_entry_change(
                    dataset,
                    str(event.get("ticker") or ""),
                    session,
                    payload.get("entry_date") or session,
                )
                if change.get("price_change") is not None:
                    delays.append(float(change["price_change"]))
            for event in prio_sel:
                if t1_confirmed(event):
                    t1_in_prio += 1
                    if str(event.get("ticker") or "") not in raw_tickers:
                        missed_t1_by_raw += 1
                key = (str(event.get("ticker") or ""), str(event.get("pivot_id") or ""))
                if key in seen_prio:
                    repeats_prio += 1
                seen_prio.add(key)
                t2 = event.get("t2") or {}
                if t2.get("hold_t1") is False:
                    technical_fail_prio += 1
                raw, excess, payload = _exec(event)
                if raw is None:
                    label_missing_prio += 1
                else:
                    prio_day.append(raw)
                    prio_event_excess.append((session, raw if excess is None else excess))
                    if raw < 0:
                        economic_fail_prio += 1
                if excess is not None:
                    prio_ex_day.append(excess)
                if payload.get("entry_date") and payload.get("exit_date"):
                    path = mae_mfe_from_entry(
                        dataset,
                        str(event.get("ticker") or ""),
                        payload["entry_date"],
                        payload["exit_date"],
                        entry_price=_finite(payload.get("entry_open")),
                    )
                    if path.get("status") == "active":
                        if path.get("mae") is not None:
                            prio_mae.append(float(path["mae"]))
                        if path.get("mfe") is not None:
                            prio_mfe.append(float(path["mfe"]))
                    else:
                        prio_incomplete += 1
            if raw_day:
                raw_daily.append(fmean(raw_day))
            if prio_day:
                prio_daily.append(fmean(prio_day))
            if raw_ex_day:
                raw_excess_daily.append(fmean(raw_ex_day))
            if prio_ex_day:
                prio_excess_daily.append(fmean(prio_ex_day))
            if raw_ex_day and prio_ex_day:
                paired_excess.append(fmean(prio_ex_day) - fmean(raw_ex_day))

        paired = None
        if len(raw_excess_daily) == len(prio_excess_daily) and len(raw_excess_daily) >= 2:
            paired = paired_difference_ci(prio_excess_daily, raw_excess_daily, horizon_days=20)
        by_k[str(slot)] = {
            "k": slot,
            "primary": slot == k,
            "days_with_events": days_with_events,
            "days_not_enough_t1": days_short_t1,
            "slots_filled_raw": slots_filled_raw,
            "slots_filled_priority": slots_filled_prio,
            "t1_share_raw": None if not slots_filled_raw else t1_in_raw / slots_filled_raw,
            "t1_share_priority": None if not slots_filled_prio else t1_in_prio / slots_filled_prio,
            "t1_promoted_into_k": missed_t1_by_raw,
            "repeat_alerts_raw": repeats_raw,
            "repeat_alerts_priority": repeats_prio,
            "label_missing_raw": label_missing_raw,
            "label_missing_priority": label_missing_prio,
            "raw_top_k": {
                "daily_ew_abs": None if not raw_daily else fmean(raw_daily),
                "daily_ew_excess": None if not raw_excess_daily else fmean(raw_excess_daily),
                "event_equal": summarize_return_targets(raw_event_excess),
                "economic_negative_20d": economic_fail_raw,
                "technical_resistance_lost_t1": technical_fail_raw,
                "complete_mae_mean": None if not raw_mae else fmean(raw_mae),
                "complete_mfe_mean": None if not raw_mfe else fmean(raw_mfe),
                "incomplete_path_n": raw_incomplete,
            },
            "t1_priority_top_k": {
                "daily_ew_abs": None if not prio_daily else fmean(prio_daily),
                "daily_ew_excess": None if not prio_excess_daily else fmean(prio_excess_daily),
                "event_equal": summarize_return_targets(prio_event_excess),
                "economic_negative_20d": economic_fail_prio,
                "technical_resistance_lost_t1": technical_fail_prio,
                "complete_mae_mean": None if not prio_mae else fmean(prio_mae),
                "complete_mfe_mean": None if not prio_mfe else fmean(prio_mfe),
                "incomplete_path_n": prio_incomplete,
            },
            "paired_daily_excess_priority_minus_raw": paired,
            "mean_trigger_to_entry_change": None if not delays else fmean(delays),
        }

    return {
        "rank_proxy": T1_PRIORITY_RANK_PROXY,
        "default_k": k,
        "diagnostic_k": list(diagnostic_k),
        "day_count": len(days),
        "event_count": len(events),
        "by_k": by_k,
        "notes": [
            "Raw triggers are kept. T1 only reorders.",
            "K was frozen before seeing results; 1 and 5 are diagnostic only.",
            "This is not production breakout-score-v1.",
            "T1 is a daily close proxy, not verified intraday RVOL.",
            "20d negative is economic fail; T+1 close losing resistance is technical fail.",
        ],
    }

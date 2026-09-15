"""T1/T2 evaluation on one frozen raw-trigger set."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any, Mapping, Sequence

from app.services.research.algorithm_protocol import MINIMUM_MEANINGFUL, PRIMARY_SCREENER_HORIZON
from app.services.research.dataset import OfflineOHLCV
from app.services.research.labels import attach_event_labels
from app.services.research.metrics import summarize_return_targets
from app.services.research.path_stats import (
    executable_open_return,
    mae_mfe_from_entry,
    trigger_to_entry_change,
    universe_open_return_mean,
)
from app.services.research.protocol import parse_session_date
from app.services.research.timing import attach_timing_candidates


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _event_excess(event: Mapping[str, Any]) -> float | None:
    return _finite(event.get("excess_vs_universe_20d"))


def _summarize_returns(values: Sequence[tuple[str, float]]) -> dict[str, Any]:
    targets = summarize_return_targets(values, horizon_days=PRIMARY_SCREENER_HORIZON)
    event_mean = targets["event_equal"]["mean"]
    return {
        "n": targets["n_events"],
        "mean": event_mean,
        "mean_is": "event_equal",
        "fail_rate": targets["event_equal"]["fail_rate"],
        "event_equal": targets["event_equal"],
        "date_equal": targets["date_equal"],
        "ci": targets["event_equal"]["ci"],
        "date_equal_ci": targets["date_equal"]["ci"],
        "empty_trading_days_are_not_zero_events": True,
        "note": (
            "mean is event-equal. date_equal.ci must not be read as the "
            "interval for this mean."
        ),
    }


def evaluate_timing_variant(
    dataset: OfflineOHLCV,
    events: Sequence[Mapping[str, Any]],
    *,
    variant_key: str,
    entry_lag: int,
    universe_tickers: Sequence[str] | None = None,
) -> dict[str, Any]:
    confirmed_exec: list[tuple[str, float]] = []
    confirmed_excess: list[tuple[str, float]] = []
    overall_skip_zero: list[tuple[str, float]] = []
    overall_skip_zero_excess: list[tuple[str, float]] = []
    missed_upside: list[float] = []
    missed_upside_excess: list[float] = []
    delays: list[float] = []
    maes: list[float] = []
    mfes: list[float] = []
    mae_incomplete = 0
    confirmable = 0
    confirmed = 0
    unavailable = 0
    executable_unavailable = 0
    details_head: list[dict[str, Any]] = []
    symbols = list(universe_tickers or dataset.tickers())
    universe_cache: dict[tuple[str, str], float | None] = {}

    for event in events:
        session = str(event.get("trading_date") or event.get("signal_date") or "")
        ticker = str(event.get("ticker") or "")
        block = event.get(variant_key) or {}
        status = block.get("status")
        if status != "active":
            unavailable += 1
            continue
        confirmable += 1
        is_confirmed = bool(block.get("confirmed"))
        if is_confirmed:
            confirmed += 1
        raw_close = _event_excess(event)
        exec_ret = executable_open_return(
            dataset,
            ticker,
            session,
            entry_lag_sessions=entry_lag,
        )
        exec_value = _finite(exec_ret.get("forward_return"))
        excess_value = None
        entry_date = exec_ret.get("entry_date")
        exit_date = exec_ret.get("exit_date")
        if exec_value is not None and entry_date and exit_date:
            cache_key = (str(entry_date), str(exit_date))
            if cache_key not in universe_cache:
                universe_cache[cache_key] = _finite(
                    universe_open_return_mean(
                        dataset,
                        symbols,
                        entry_date,
                        exit_date,
                    ).get("mean")
                )
            universe_mean = universe_cache[cache_key]
            if universe_mean is not None:
                excess_value = exec_value - universe_mean
        if exec_value is None:
            executable_unavailable += 1
        if exec_value is not None:
            if is_confirmed:
                confirmed_exec.append((session, exec_value))
                overall_skip_zero.append((session, exec_value))
                if excess_value is not None:
                    confirmed_excess.append((session, excess_value))
                    overall_skip_zero_excess.append((session, excess_value))
            else:
                overall_skip_zero.append((session, 0.0))
                if excess_value is not None:
                    overall_skip_zero_excess.append((session, 0.0))
                if exec_value > 0:
                    missed_upside.append(exec_value)
                if excess_value is not None and excess_value > 0:
                    missed_upside_excess.append(excess_value)
        elif not is_confirmed and raw_close is not None and raw_close > 0:
            missed_upside.append(raw_close)

        if is_confirmed and exec_ret.get("entry_date"):
            change = trigger_to_entry_change(dataset, ticker, session, exec_ret["entry_date"])
            if change.get("price_change") is not None:
                delays.append(float(change["price_change"]))
            path = mae_mfe_from_entry(
                dataset,
                ticker,
                exec_ret["entry_date"],
                exec_ret.get("exit_date") or exec_ret["entry_date"],
                entry_price=_finite(exec_ret.get("entry_open")),
            )
            if path.get("status") == "active":
                if path.get("mae") is not None:
                    maes.append(float(path["mae"]))
                if path.get("mfe") is not None:
                    mfes.append(float(path["mfe"]))
            else:
                mae_incomplete += 1
        if len(details_head) < 8:
            details_head.append(
                {
                    "trading_date": session,
                    "ticker": ticker,
                    "confirmed": is_confirmed,
                    "executable": exec_ret,
                    "executable_excess_vs_universe": excess_value,
                    "raw_close_excess_20d": raw_close,
                }
            )

    return {
        "variant": variant_key,
        "entry_lag_sessions": entry_lag,
        "raw_opportunities": len(events),
        "confirmable": confirmable,
        "unavailable": unavailable,
        "executable_unavailable": executable_unavailable,
        "confirmed": confirmed,
        "confirm_rate": None if not confirmable else confirmed / confirmable,
        "conditional_executable": _summarize_returns(confirmed_exec),
        "conditional_executable_excess_vs_universe": _summarize_returns(confirmed_excess),
        "selection_contribution_on_original_opportunity_set": _summarize_returns(overall_skip_zero),
        "selection_contribution_on_original_opportunity_set_excess_vs_universe": _summarize_returns(
            overall_skip_zero_excess
        ),
        "overall_skip_as_zero": _summarize_returns(overall_skip_zero),
        "overall_skip_as_zero_excess_vs_universe": _summarize_returns(overall_skip_zero_excess),
        "mean_trigger_to_entry_change": None if not delays else fmean(delays),
        "trigger_to_entry_change_is": "trigger_close_to_entry_open",
        "mae_mean": None if not maes else fmean(maes),
        "mfe_mean": None if not mfes else fmean(mfes),
        "mae_complete_n": len(maes),
        "mae_incomplete_n": mae_incomplete,
        "missed_upside_n": len(missed_upside),
        "missed_upside_mean": None if not missed_upside else fmean(missed_upside),
        "missed_upside_excess_n": len(missed_upside_excess),
        "missed_upside_excess_mean": None if not missed_upside_excess else fmean(missed_upside_excess),
        "details_head": details_head,
        "minimum_meaningful": MINIMUM_MEANINGFUL,
        "notes": [
            "Conditional stats use confirmed names only.",
            "selection_contribution_on_original_opportunity_set keeps the original opportunity set; rejects contribute 0. It is not a cash-versus-fully-invested book and does not model a limited daily attention slot.",
            "overall_skip_as_zero is a compatibility alias for that selection contribution.",
            "Executable excess uses the same entry/exit opens as the fill, minus the same-window universe open-to-open mean.",
            "Close/close raw excess is not reused as the executable return.",
            "Incomplete MAE paths are excluded from mae_mean.",
        ],
    }


def _paired_vs_raw(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    diffs: list[tuple[str, float]] = []
    for event in events:
        session = str(event.get("trading_date") or "")
        t1 = event.get("t1") or {}
        raw = _finite(event.get("executable_t1_excess_vs_universe"))
        if raw is None or t1.get("status") != "active":
            continue
        selected = raw if t1.get("confirmed") else 0.0
        diffs.append((session, selected - raw))
    if not diffs:
        return {
            "status": "unavailable",
            "reason": "executable_t1_excess_not_attached",
        }
    return {
        "status": "active",
        "target": "t1_selection_contribution_minus_raw",
        "series": summarize_return_targets(diffs, horizon_days=PRIMARY_SCREENER_HORIZON),
        "note": (
            "Matched on the same opportunity. A positive T1 conditional mean "
            "does not replace this comparison."
        ),
    }


def compare_radar_candidates(
    dataset: OfflineOHLCV,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    labeled = attach_event_labels(list(events), dataset, horizon=20)
    labeled = attach_timing_candidates(dataset, labeled)
    raw_close = [
        (str(event.get("trading_date") or ""), value)
        for event in labeled
        if (value := _event_excess(event)) is not None
    ]
    symbols = list(dataset.tickers())
    raw_exec = []
    raw_exec_excess = []
    universe_cache: dict[tuple[str, str], float | None] = {}
    for event in labeled:
        session = str(event.get("trading_date") or "")
        payload = executable_open_return(
            dataset,
            str(event.get("ticker") or ""),
            session,
            entry_lag_sessions=1,
        )
        value = _finite(payload.get("forward_return"))
        if value is not None:
            raw_exec.append((session, value))
            entry_date = payload.get("entry_date")
            exit_date = payload.get("exit_date")
            if entry_date and exit_date:
                cache_key = (str(entry_date), str(exit_date))
                if cache_key not in universe_cache:
                    universe_cache[cache_key] = _finite(
                        universe_open_return_mean(
                            dataset,
                            symbols,
                            entry_date,
                            exit_date,
                        ).get("mean")
                    )
                universe_mean = universe_cache[cache_key]
                if universe_mean is not None:
                    excess = value - universe_mean
                    raw_exec_excess.append((session, excess))
                    event["executable_t1_excess_vs_universe"] = excess
    return {
        "raw_close_label": _summarize_returns(raw_close),
        "raw_executable_t1_open": _summarize_returns(raw_exec),
        "raw_executable_t1_open_excess_vs_universe": _summarize_returns(raw_exec_excess),
        "t1": evaluate_timing_variant(
            dataset,
            labeled,
            variant_key="t1",
            entry_lag=1,
            universe_tickers=symbols,
        ),
        "paired_vs_raw": _paired_vs_raw(labeled),
        "t2": evaluate_timing_variant(
            dataset,
            labeled,
            variant_key="t2",
            entry_lag=2,
            universe_tickers=symbols,
        ),
        "event_count": len(labeled),
        "notes": [
            "T1 and T2 are applied independently to the same raw TRIGGERED set.",
            "T1 information is T close; T2 information is T+1 close.",
            "Paired diffs vs raw use the same opportunity identities; a positive conditional mean is not a test vs raw.",
        ],
        "events": labeled,
    }

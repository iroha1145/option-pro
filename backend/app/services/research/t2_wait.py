"""T2 wait-cost clocks. Confirmation never backfills an earlier fill."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any, Mapping, Sequence

from app.services.research.dataset import OfflineOHLCV
from app.services.research.metrics import summarize_return_targets
from app.services.research.path_stats import (
    executable_open_return,
    mae_mfe_from_entry,
    trigger_to_entry_change,
    universe_open_return_mean,
)
from app.services.research.protocol import parse_session_date


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _open_on(dataset: OfflineOHLCV, ticker: str, session: Any) -> float | None:
    if session is None:
        return None
    bar = dataset.bar(ticker, parse_session_date(str(session)))
    if bar is None:
        return None
    return _finite(bar.get("adj_open"))


def compare_t2_wait_paths(
    dataset: OfflineOHLCV,
    events: Sequence[Mapping[str, Any]],
    *,
    universe_tickers: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Same T2-confirmed events: T+1 vs T+2 open, two hold clocks, split MAE."""

    symbols = list(universe_tickers or dataset.tickers())
    universe_cache: dict[tuple[str, str], float | None] = {}
    ratios: list[float] = []
    incremental_waits: list[float] = []
    trigger_to_t1: list[float] = []
    trigger_to_t2: list[float] = []
    own_t1: list[tuple[str, float]] = []
    own_t2: list[tuple[str, float]] = []
    common_t1: list[tuple[str, float]] = []
    common_t2: list[tuple[str, float]] = []
    own_excess_t1: list[tuple[str, float]] = []
    own_excess_t2: list[tuple[str, float]] = []
    mae_complete_t1: list[float] = []
    mae_complete_t2: list[float] = []
    mfe_complete_t1: list[float] = []
    mfe_complete_t2: list[float] = []
    mae_incomplete_t1 = 0
    mae_incomplete_t2 = 0
    economic_fail_t1 = 0
    economic_fail_t2 = 0
    technical_fail_t = 0
    technical_fail_t1 = 0
    both_opens = 0
    confirmed = 0
    skipped_unconfirmed = 0

    def _excess(entry: str | None, exit_date: str | None, raw: float | None) -> float | None:
        if raw is None or not entry or not exit_date:
            return None
        key = (str(entry), str(exit_date))
        if key not in universe_cache:
            universe_cache[key] = _finite(
                universe_open_return_mean(dataset, symbols, entry, exit_date).get("mean")
            )
        universe = universe_cache[key]
        if universe is None:
            return None
        return raw - universe

    for event in events:
        block = event.get("t2") or {}
        if block.get("status") != "active" or not block.get("confirmed"):
            skipped_unconfirmed += 1
            continue
        confirmed += 1
        if block.get("hold_t") is False:
            technical_fail_t += 1
        if block.get("hold_t1") is False:
            technical_fail_t1 += 1
        session = str(event.get("trading_date") or event.get("signal_date") or "")
        ticker = str(event.get("ticker") or "")
        t1 = executable_open_return(dataset, ticker, session, entry_lag_sessions=1)
        t2 = executable_open_return(dataset, ticker, session, entry_lag_sessions=2)
        open_t1 = _finite(t1.get("entry_open"))
        open_t2 = _finite(t2.get("entry_open"))
        if open_t1 is not None and open_t2 is not None and open_t1 > 0:
            both_opens += 1
            ratios.append(open_t2 / open_t1)
            incremental_waits.append(open_t2 / open_t1 - 1.0)
        close_to_t1 = trigger_to_entry_change(dataset, ticker, session, t1.get("entry_date") or session)
        close_to_t2 = trigger_to_entry_change(dataset, ticker, session, t2.get("entry_date") or session)
        if close_to_t1.get("price_change") is not None:
            trigger_to_t1.append(float(close_to_t1["price_change"]))
        if close_to_t2.get("price_change") is not None:
            trigger_to_t2.append(float(close_to_t2["price_change"]))

        ret_t1 = _finite(t1.get("forward_return"))
        ret_t2 = _finite(t2.get("forward_return"))
        if ret_t1 is not None:
            own_t1.append((session, ret_t1))
            if ret_t1 < 0:
                economic_fail_t1 += 1
            excess = _excess(t1.get("entry_date"), t1.get("exit_date"), ret_t1)
            if excess is not None:
                own_excess_t1.append((session, excess))
        if ret_t2 is not None:
            own_t2.append((session, ret_t2))
            if ret_t2 < 0:
                economic_fail_t2 += 1
            excess = _excess(t2.get("entry_date"), t2.get("exit_date"), ret_t2)
            if excess is not None:
                own_excess_t2.append((session, excess))

        common_exit = t2.get("exit_date")
        if open_t1 is not None and open_t1 > 0 and common_exit:
            common_bar = dataset.bar(ticker, parse_session_date(str(common_exit)))
            common_px = None if common_bar is None else _finite(common_bar.get("adj_open"))
            if common_px is not None and common_px > 0:
                common_t1.append((session, common_px / open_t1 - 1.0))
        if ret_t2 is not None:
            common_t2.append((session, ret_t2))

        if t1.get("entry_date") and t1.get("exit_date") and open_t1 is not None:
            path = mae_mfe_from_entry(
                dataset,
                ticker,
                t1["entry_date"],
                t1["exit_date"],
                entry_price=open_t1,
            )
            if path.get("status") == "active":
                if path.get("mae") is not None:
                    mae_complete_t1.append(float(path["mae"]))
                if path.get("mfe") is not None:
                    mfe_complete_t1.append(float(path["mfe"]))
            else:
                mae_incomplete_t1 += 1
        if t2.get("entry_date") and t2.get("exit_date") and open_t2 is not None:
            path = mae_mfe_from_entry(
                dataset,
                ticker,
                t2["entry_date"],
                t2["exit_date"],
                entry_price=open_t2,
            )
            if path.get("status") == "active":
                if path.get("mae") is not None:
                    mae_complete_t2.append(float(path["mae"]))
                if path.get("mfe") is not None:
                    mfe_complete_t2.append(float(path["mfe"]))
            else:
                mae_incomplete_t2 += 1

    paired_own = []
    for left, right in zip(own_t2, own_t1):
        if left[0] == right[0]:
            paired_own.append((left[0], left[1] - right[1]))

    return {
        "confirmed": confirmed,
        "skipped_unconfirmed": skipped_unconfirmed,
        "both_opens": both_opens,
        "t2_open_over_t1_open": {
            "n": len(ratios),
            "mean": None if not ratios else fmean(ratios),
            "incremental_wait_mean": None if not incremental_waits else fmean(incremental_waits),
            "note": (
                "T+2 open / T+1 open on the same confirmed names. "
                "trigger_to_entry_change is close-to-open, not this increment."
            ),
        },
        "trigger_close_to_t1_open_mean": None if not trigger_to_t1 else fmean(trigger_to_t1),
        "trigger_close_to_t2_open_mean": None if not trigger_to_t2 else fmean(trigger_to_t2),
        "own_20d_clock": {
            "t1_open": summarize_return_targets(own_t1),
            "t2_open": summarize_return_targets(own_t2),
            "t2_minus_t1": summarize_return_targets(paired_own),
            "t1_excess_vs_universe": summarize_return_targets(own_excess_t1),
            "t2_excess_vs_universe": summarize_return_targets(own_excess_t2),
            "note": "Each entry holds 20 trading days from its own fill.",
        },
        "common_exit_clock": {
            "t1_open_to_t2_exit": summarize_return_targets(common_t1),
            "t2_open_to_t2_exit": summarize_return_targets(common_t2),
            "note": (
                "Both exit on the T+2 + 20d open. Diagnostic for waiting, "
                "not a tradable extra day added after confirmation."
            ),
        },
        "mae_mfe": {
            "t1_complete_mae_mean": None if not mae_complete_t1 else fmean(mae_complete_t1),
            "t1_complete_mfe_mean": None if not mfe_complete_t1 else fmean(mfe_complete_t1),
            "t1_complete_n": len(mae_complete_t1),
            "t1_incomplete_n": mae_incomplete_t1,
            "t2_complete_mae_mean": None if not mae_complete_t2 else fmean(mae_complete_t2),
            "t2_complete_mfe_mean": None if not mfe_complete_t2 else fmean(mfe_complete_t2),
            "t2_complete_n": len(mae_complete_t2),
            "t2_incomplete_n": mae_incomplete_t2,
            "incomplete_excluded_from_complete_mean": True,
        },
        "failure_definitions": {
            "economic_negative_20d_t1": economic_fail_t1,
            "economic_negative_20d_t2": economic_fail_t2,
            "technical_resistance_lost_on_T": technical_fail_t,
            "technical_resistance_lost_on_T1": technical_fail_t1,
            "note": (
                "A negative 20d hold is an economic fail, not a technical "
                "false break. Technical fail is losing T-frozen resistance+buffer."
            ),
        },
        "no_backfill_earlier_fill": True,
    }

"""开盘前 / 收盘后两个槽的判定（只在交易日；按美东墙钟）。

时刻一律先按美东墙钟落到交易日上（``market_datetime`` 处理夏令时），再换成 UTC
做加减：宽限与收盘后偏移是绝对时长，跨夏令时切换或跨午夜都不会差一小时。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.services.market_calendar import (
    ET,
    early_close_minutes,
    is_trading_day,
    market_datetime,
    previous_trading_day,
)

from .schema import BriefSlot

_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_SLOTS: tuple[BriefSlot, ...] = ("pre_open", "post_close")
_REGULAR_CLOSE_MINUTES = 16 * 60
NEXT_SLOT_LOOKAHEAD_DAYS = 15
# 收盘后槽的关窗时刻 = 兜底时刻 + 宽限，可能落到次日凌晨；due_slot 因此要回看
# 前几个交易日的窗口。回看 2 个交易日覆盖宽限不超过一天的任何配置。
_DUE_LOOKBACK_TRADING_DAYS = 2


@dataclass(frozen=True)
class BriefSchedule:
    """槽位参数（来自 personal.toml [market_brief]）。

    - pre_open_time_et：开盘前槽的美东时刻，默认 08:40（宏观模块 08:30 刷新之后）。
    - post_close_offset_minutes：收盘后多少分钟开窗（早收盘按早收盘时刻算）。
    - post_close_fallback_time_et：等不到当日全市场扫描批次时的兜底发车时刻。
    - grace_minutes：开窗后仍允许发车的时长，只用于 worker 停机后恢复时补跑本槽。
      一个槽跑过（无论成败）就等下一个槽，窗口内不自动重试；要补就走手动补发。
    """

    pre_open_time_et: str = "08:40"
    post_close_offset_minutes: int = 30
    post_close_fallback_time_et: str = "23:30"
    grace_minutes: int = 150


def _clock_minutes(value: str, *, name: str) -> int:
    if not isinstance(value, str) or not _HHMM.fullmatch(value):
        raise ValueError(f"{name} must be an HH:MM wall-clock time")
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _non_negative_minutes(value: int, *, name: str) -> timedelta:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer number of minutes")
    return timedelta(minutes=value)


def _et_wall_clock_utc(trading_date: date, minutes: int) -> datetime:
    return market_datetime(trading_date, minutes).astimezone(timezone.utc)


def _aware(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return now.astimezone(timezone.utc)


def _post_close_fallback_at(trading_date: date, schedule: BriefSchedule) -> datetime:
    minutes = _clock_minutes(schedule.post_close_fallback_time_et, name="post_close_fallback_time_et")
    return _et_wall_clock_utc(trading_date, minutes)


def slot_window(trading_date: date, slot: BriefSlot, schedule: BriefSchedule) -> tuple[datetime, datetime]:
    """返回该交易日该槽的 (开窗时刻, 关窗时刻)，均为 UTC aware datetime。

    pre_open：开窗 = pre_open_time_et；关窗 = 开窗 + grace。
    post_close：开窗 = 当日收盘（含早收盘）+ offset；关窗 = fallback 时刻 + grace。
    非交易日没有槽，直接报错，避免调用方拿到一个看似合法的窗口。
    """

    if not is_trading_day(trading_date):
        raise ValueError(f"{trading_date.isoformat()} is not a NYSE trading day")
    grace = _non_negative_minutes(schedule.grace_minutes, name="grace_minutes")
    if slot == "pre_open":
        opens = _et_wall_clock_utc(
            trading_date,
            _clock_minutes(schedule.pre_open_time_et, name="pre_open_time_et"),
        )
        return opens, opens + grace
    if slot == "post_close":
        close_minutes = early_close_minutes(trading_date) or _REGULAR_CLOSE_MINUTES
        opens = _et_wall_clock_utc(trading_date, close_minutes) + _non_negative_minutes(
            schedule.post_close_offset_minutes,
            name="post_close_offset_minutes",
        )
        closes = _post_close_fallback_at(trading_date, schedule) + grace
        if closes <= opens:
            raise ValueError("post_close window closes before it opens; check fallback time and grace")
        return opens, closes
    raise ValueError(f"unknown market brief slot: {slot!r}")


def next_slot_at(now: datetime, schedule: BriefSchedule) -> tuple[datetime, date, BriefSlot] | None:
    """下一个开窗时刻（>= now）及其交易日与槽；只看交易日，最多向后找 15 天。"""

    observed = _aware(now)
    first_day = observed.astimezone(ET).date()
    for offset in range(NEXT_SLOT_LOOKAHEAD_DAYS + 1):
        candidate = first_day + timedelta(days=offset)
        if not is_trading_day(candidate):
            continue
        for slot in _SLOTS:
            opens, _closes = slot_window(candidate, slot, schedule)
            if opens >= observed:
                return opens, candidate, slot
    return None


def due_slot(now: datetime, schedule: BriefSchedule) -> tuple[date, BriefSlot] | None:
    """now 落在哪个槽的窗口内；若同时落在两个窗口（理论上不会）取开窗更晚的那个。

    窗口按 [开窗, 关窗) 计。今天不是交易日时仍要看上一个交易日的收盘后窗口：
    周五的收盘后窗口按默认配置在周六凌晨才关。
    """

    observed = _aware(now)
    today = observed.astimezone(ET).date()
    candidates: list[date] = [today] if is_trading_day(today) else []
    cursor = today
    for _ in range(_DUE_LOOKBACK_TRADING_DAYS):
        cursor = previous_trading_day(cursor)
        candidates.append(cursor)
    best: tuple[datetime, date, BriefSlot] | None = None
    for candidate in candidates:
        for slot in _SLOTS:
            opens, closes = slot_window(candidate, slot, schedule)
            if opens <= observed < closes and (best is None or opens > best[0]):
                best = (opens, candidate, slot)
    return None if best is None else (best[1], best[2])


def post_close_fallback_reached(now: datetime, trading_date: date, schedule: BriefSchedule) -> bool:
    """收盘后槽是否已到兜底时刻（此后不再等当日全市场批次）。"""

    return _aware(now) >= _post_close_fallback_at(trading_date, schedule)


__all__ = [
    "NEXT_SLOT_LOOKAHEAD_DAYS",
    "BriefSchedule",
    "slot_window",
    "next_slot_at",
    "due_slot",
    "post_close_fallback_reached",
]

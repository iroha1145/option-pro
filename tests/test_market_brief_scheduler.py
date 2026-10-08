from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.market_brief.scheduler import (
    BriefSchedule,
    due_slot,
    next_slot_at,
    post_close_fallback_reached,
    slot_window,
)

SCHEDULE = BriefSchedule()  # 08:40 / 收盘 + 30 分钟 / 兜底 23:30 / 宽限 150 分钟


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def test_regular_day_windows_follow_new_york_wall_clock() -> None:
    # 2026-10-08 周四，夏令时 UTC-4。
    assert slot_window(date(2026, 10, 8), "pre_open", SCHEDULE) == (utc(2026, 10, 8, 12, 40), utc(2026, 10, 8, 15, 10))
    # 收盘 16:00 + 30 分钟开窗；兜底 23:30 + 150 分钟关窗，落到次日 UTC 06:00。
    assert slot_window(date(2026, 10, 8), "post_close", SCHEDULE) == (utc(2026, 10, 8, 20, 30), utc(2026, 10, 9, 6, 0))


def test_early_close_day_opens_post_close_after_one_pm() -> None:
    # 2026-11-27 感恩节次日早收盘 13:00，冬令时 UTC-5。
    opens, closes = slot_window(date(2026, 11, 27), "post_close", SCHEDULE)
    assert opens == utc(2026, 11, 27, 18, 30)
    assert closes == utc(2026, 11, 28, 7, 0)
    assert slot_window(date(2026, 11, 27), "pre_open", SCHEDULE)[0] == utc(2026, 11, 27, 13, 40)


@pytest.mark.parametrize("closed_day", [date(2026, 10, 10), date(2026, 10, 11), date(2026, 11, 26), date(2026, 12, 25)])
def test_weekends_and_holidays_have_no_slots(closed_day: date) -> None:
    with pytest.raises(ValueError, match="not a NYSE trading day"):
        slot_window(closed_day, "pre_open", SCHEDULE)


def test_due_slot_inside_each_window_and_none_between() -> None:
    assert due_slot(utc(2026, 10, 8, 12, 40), SCHEDULE) == (date(2026, 10, 8), "pre_open")
    assert due_slot(utc(2026, 10, 8, 15, 9, 59), SCHEDULE) == (date(2026, 10, 8), "pre_open")
    assert due_slot(utc(2026, 10, 8, 15, 10), SCHEDULE) is None  # 关窗时刻不含
    assert due_slot(utc(2026, 10, 8, 18, 0), SCHEDULE) is None  # 盘中不在任何窗口
    assert due_slot(utc(2026, 10, 8, 20, 30), SCHEDULE) == (date(2026, 10, 8), "post_close")


def test_post_close_window_crosses_midnight_into_the_next_utc_and_et_day() -> None:
    # 美东 10-08 22:00 = UTC 10-09 02:00：仍属 10-08 的收盘后窗口。
    assert due_slot(utc(2026, 10, 9, 2, 0), SCHEDULE) == (date(2026, 10, 8), "post_close")
    # 美东 10-09 01:30（已过美东午夜）也仍在窗口内。
    assert due_slot(utc(2026, 10, 9, 5, 30), SCHEDULE) == (date(2026, 10, 8), "post_close")
    assert due_slot(utc(2026, 10, 9, 6, 0), SCHEDULE) is None


def test_friday_post_close_window_is_still_due_on_saturday_morning() -> None:
    # 周六不是交易日，但周五的收盘后窗口在周六凌晨 02:00（美东）才关。
    assert due_slot(utc(2026, 10, 10, 5, 30), SCHEDULE) == (date(2026, 10, 9), "post_close")
    assert due_slot(utc(2026, 10, 10, 12, 45), SCHEDULE) is None  # 周六没有开盘前槽


def test_next_slot_skips_weekend_and_holiday() -> None:
    # 周五收盘后窗口已开：下一个开窗是周一开盘前（10-12 哥伦布日美股照常开市）。
    assert next_slot_at(utc(2026, 10, 9, 21, 0), SCHEDULE) == (utc(2026, 10, 12, 12, 40), date(2026, 10, 12), "pre_open")
    # 感恩节当天：下一个是周五早收盘日的开盘前槽。
    assert next_slot_at(utc(2026, 11, 26, 15, 0), SCHEDULE) == (utc(2026, 11, 27, 13, 40), date(2026, 11, 27), "pre_open")
    # 开窗时刻本身算「>= now」。
    assert next_slot_at(utc(2026, 10, 8, 12, 40), SCHEDULE) == (utc(2026, 10, 8, 12, 40), date(2026, 10, 8), "pre_open")
    # 开盘前窗口已开：下一个开窗是当日收盘后。
    assert next_slot_at(utc(2026, 10, 8, 13, 0), SCHEDULE) == (utc(2026, 10, 8, 20, 30), date(2026, 10, 8), "post_close")


def test_next_slot_crosses_the_daylight_saving_change() -> None:
    # 2026-11-01 夏令时结束：周一开盘前 08:40 变成 UTC 13:40。
    assert next_slot_at(utc(2026, 10, 30, 21, 0), SCHEDULE) == (utc(2026, 11, 2, 13, 40), date(2026, 11, 2), "pre_open")


def test_post_close_fallback_is_absolute_new_york_time() -> None:
    # 23:30 EDT = UTC 次日 03:30。
    assert post_close_fallback_reached(utc(2026, 10, 9, 3, 29), date(2026, 10, 8), SCHEDULE) is False
    assert post_close_fallback_reached(utc(2026, 10, 9, 3, 30), date(2026, 10, 8), SCHEDULE) is True


def test_custom_schedule_and_invalid_inputs() -> None:
    schedule = BriefSchedule(pre_open_time_et="07:15", post_close_offset_minutes=45, grace_minutes=60)
    assert slot_window(date(2026, 10, 8), "pre_open", schedule) == (utc(2026, 10, 8, 11, 15), utc(2026, 10, 8, 12, 15))
    assert slot_window(date(2026, 10, 8), "post_close", schedule)[0] == utc(2026, 10, 8, 20, 45)
    with pytest.raises(ValueError, match="HH:MM"):
        slot_window(date(2026, 10, 8), "pre_open", BriefSchedule(pre_open_time_et="8:40"))
    with pytest.raises(ValueError, match="timezone-aware"):
        due_slot(datetime(2026, 10, 8, 13, 0), SCHEDULE)
    with pytest.raises(ValueError, match="unknown market brief slot"):
        slot_window(date(2026, 10, 8), "midday", SCHEDULE)  # type: ignore[arg-type]

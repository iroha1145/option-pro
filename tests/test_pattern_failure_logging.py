"""形态检测失败的日志必须**有界**。

这条路径每个请求每支票都会走一遍。以前它是个裸 `except Exception: []`——
线上「一条形态都画不出来」和「本来就没有形态」看起来完全一样，查了很久。
补日志的同时必须限体积，否则系统性故障会按请求量刷屏把日志盘吃掉。
"""
from __future__ import annotations

import logging

import pytest

from app.services.technical import structure as S


@pytest.fixture(autouse=True)
def _reset_counters():
    S._PATTERN_FAIL_COUNTS.clear()
    S._pattern_fail_suppressed = False
    yield
    S._PATTERN_FAIL_COUNTS.clear()
    S._pattern_fail_suppressed = False


def test_same_ticker_and_error_logs_once_then_stays_quiet(caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            S._log_pattern_failure("NVDA", ValueError("boom"))
    assert len(caplog.records) == 1, "同一票同一种故障只应说一次"
    assert S._PATTERN_FAIL_COUNTS["NVDA:ValueError"] == 50, "但重复次数仍要留在计数里"


def test_distinct_keys_are_separated_by_ticker_and_error_type(caplog):
    with caplog.at_level(logging.WARNING):
        S._log_pattern_failure("NVDA", ValueError("a"))
        S._log_pattern_failure("AAPL", ValueError("a"))
        S._log_pattern_failure("NVDA", KeyError("b"))
    assert len(caplog.records) == 3


def test_only_the_first_few_carry_a_traceback(caplog):
    with caplog.at_level(logging.WARNING):
        for i in range(S._PATTERN_FAIL_TRACEBACKS + 3):
            S._log_pattern_failure(f"T{i}", ValueError("boom"))
    with_tb = [r for r in caplog.records if r.exc_info]
    assert len(with_tb) == S._PATTERN_FAIL_TRACEBACKS, "堆栈是体积大头，只给最先几次"
    assert len(caplog.records) == S._PATTERN_FAIL_TRACEBACKS + 3


def test_key_table_is_capped_and_says_so_once(caplog):
    with caplog.at_level(logging.WARNING):
        for i in range(S._PATTERN_FAIL_KEY_CAP + 25):
            S._log_pattern_failure(f"T{i}", ValueError("boom"))
    assert len(S._PATTERN_FAIL_COUNTS) == S._PATTERN_FAIL_KEY_CAP, "去重表自身不能无限增长"
    suppressed = [r for r in caplog.records if "further first-sightings" in r.getMessage()]
    assert len(suppressed) == 1, "封顶提示只说一次"


def test_long_error_message_is_truncated(caplog):
    with caplog.at_level(logging.WARNING):
        S._log_pattern_failure("NVDA", ValueError("x" * 5000))
    assert len(caplog.records) == 1
    assert len(caplog.records[0].getMessage()) < 500, "单条消息不能把整个异常正文写进去"


def test_detector_failure_does_not_break_the_payload_and_is_logged(monkeypatch, caplog):
    """真实调用路径：检测器抛错时形态为空，但页面照出，且留下一条日志。"""
    monkeypatch.setattr(
        S, "detect_auto_patterns", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("detector blew up"))
    )
    from datetime import datetime, timedelta, timezone

    # bar 的字段是 t/o/h/l/c/v，t 是**秒**级时间戳（fromtimestamp 直接吃它）
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    bars = []
    for i in range(90):
        day = start + timedelta(days=i)
        base = 100.0 + i * 0.4
        bars.append(
            {
                "t": int(day.timestamp()),
                "o": base,
                "h": base + 1.2,
                "l": base - 1.1,
                "c": base + 0.3,
                "v": 1_000_000 + i * 1000,
            }
        )
    with caplog.at_level(logging.WARNING):
        result = S.compute_technical_structure(bars, ticker="NVDA")
    assert result is not None, "装饰性图层失败不该拖垮整页"
    logged = [r for r in caplog.records if "auto-pattern detection failed" in r.getMessage()]
    assert len(logged) == 1
    assert "RuntimeError" in logged[0].getMessage()

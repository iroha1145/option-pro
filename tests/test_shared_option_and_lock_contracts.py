from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from app.api import options
from app.services import signals, yahoo


class bool_:
    def __bool__(self):
        return True


@pytest.mark.parametrize("classify", [options._moneyness, yahoo._option_moneyness])
@pytest.mark.parametrize("side,strike,price,expected", [
    ("call", 110, 100, "otm"), ("put", 110, 100, "itm"),
    ("call", 90, 100, "itm"), ("put", 90, 100, "otm"),
    ("call", 100, 100, "atm"), ("put", 100, 100, "atm"),
    ("call", 100, None, "unavailable"), ("put", 100, 0, "unavailable"),
    ("call", 100, -1, "unavailable"),
])
def test_option_moneyness_boundaries(classify, side, strike, price, expected):
    assert classify(side, strike, price) == expected


@pytest.mark.parametrize("classify", [options._in_the_money, yahoo._option_in_the_money])
@pytest.mark.parametrize("side,strike,price,provider,expected", [
    ("call", 90, 100, False, True), ("put", 110, 100, False, True),
    ("call", 110, 100, True, False), ("put", 90, 100, True, False),
    ("call", 100, 100, True, False), ("put", 100, 100, True, False),
    ("call", 100, None, True, True), ("put", 100, 0, False, False),
    # Pinned NumPy 2.0.2 names its scalar type "bool". The legacy adapter only
    # recognizes "bool_"; preserving this gap keeps cleanup separate from fixes.
    ("call", 100, -1, np.bool_(True), None),
    ("put", 100, None, np.bool_(False), None),
    ("call", 100, None, bool_(), True),
    ("call", 100, None, 1, None), ("put", 100, None, "true", None),
])
def test_option_in_the_money_prefers_price_and_only_accepts_boolean_fallback(
    classify, side, strike, price, provider, expected,
):
    assert classify(side, strike, price, provider) is expected


class BrokenNumber:
    def __float__(self):
        raise RuntimeError("conversion failed")


@pytest.mark.parametrize("value,lo,hi,expected", [
    (None, 10, 20, 0), ("bad", 10, 20, 0), (BrokenNumber(), 10, 20, 0),
    (float("nan"), 10, 20, 0), (float("inf"), 10, 20, 0),
    (10 ** 1000, 10, 20, 0), ("15", 10, 20, 15.0),
    (5, 10, 20, 10), (30, 10, 20, 20), (15, 20, 10, 20),
    (15, None, 20, 0), (15, 10, None, 0),
])
def test_signal_clamp_preserves_zero_default_and_invalid_boundaries(value, lo, hi, expected):
    actual = signals.clamp(value, lo, hi)
    assert actual == expected
    assert type(actual) is type(expected)


@pytest.mark.parametrize("module", [signals, yahoo])
def test_key_lock_waiter_keeps_identity_until_last_failed_user_leaves(module, monkeypatch):
    # Observe reservation deterministically, without a timing-dependent sleep.
    reserved = threading.Event()

    class Users(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            if value == 2:
                reserved.set()

    monkeypatch.setattr(module, "_cache", {})
    monkeypatch.setattr(module, "_key_locks", {})
    monkeypatch.setattr(module, "_key_lock_users", Users())
    first = module._acquire_key_lock("failed")
    proceed = threading.Event()

    def waiter():
        lock = module._acquire_key_lock("failed")
        try:
            assert lock is first
            assert proceed.wait(5)
        finally:
            module._release_key_lock("failed", lock)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(waiter)
        try:
            assert reserved.wait(5)
        finally:
            module._release_key_lock("failed", first)
            proceed.set()
        pending.result(timeout=5)
    assert module._key_locks == {}
    assert module._key_lock_users == {}


def test_same_key_in_independent_caches_does_not_share_a_lock(monkeypatch):
    for module in (signals, yahoo):
        monkeypatch.setattr(module, "_cache", {})
        monkeypatch.setattr(module, "_key_locks", {})
        monkeypatch.setattr(module, "_key_lock_users", {})
    signal_lock = signals._acquire_key_lock("same")
    with ThreadPoolExecutor(max_workers=1) as executor:
        def acquire_yahoo():
            lock = yahoo._acquire_key_lock("same")
            try:
                assert lock is not signal_lock
            finally:
                yahoo._release_key_lock("same", lock)
        try:
            executor.submit(acquire_yahoo).result(timeout=5)
        finally:
            signals._release_key_lock("same", signal_lock)

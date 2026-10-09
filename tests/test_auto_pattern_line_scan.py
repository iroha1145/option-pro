"""Candidate-line scoring scans bars with arrays; results must stay bit-identical."""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, timedelta

import pytest

from app.services.technical import auto_patterns

# Regimes: (bars, start price, daily volatility, volume gaps every N bars or 0).
_REGIMES = {
    "large_cap": (502, 120.0, 0.028, 0),
    "small_cap": (380, 18.0, 0.045, 0),
    "low_vol": (260, 450.0, 0.012, 0),
    "volume_gaps": (420, 60.0, 0.03, 50),
}


def _series(regime: str, seed: int) -> dict[str, list]:
    # Only random() and + - * /: no libm call, so every platform builds the
    # same floats and the digests below stay portable.
    bars, price, vol, gap_every = _REGIMES[regime]
    rng = random.Random(f"{regime}:{seed}")
    first_day = date(2024, 1, 2)
    series: dict[str, list] = {
        "times": [], "dates": [], "opens": [], "highs": [], "lows": [], "closes": [], "volumes": [],
    }
    for index in range(bars):
        cycle = (index % 46) / 23.0 - 1.0
        move = (rng.random() - 0.5) * vol * 3.4 + 0.0006 + 0.001 * cycle
        open_ = price * (1 + (rng.random() - 0.5) * vol * 0.7)
        close = max(1.0, price * (1 + move))
        series["times"].append(1_700_000_000 + index * 86_400)
        series["dates"].append((first_day + timedelta(days=index)).isoformat())
        series["opens"].append(open_)
        series["highs"].append(max(open_, close) * (1 + rng.random() * vol * 0.6))
        series["lows"].append(min(open_, close) * (1 - rng.random() * vol * 0.6))
        series["closes"].append(close)
        volume = float(int(2_000_000 * (0.5 + rng.random())))
        series["volumes"].append(None if gap_every and index % gap_every == 0 else volume)
        price = close
    return series


def _digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()


# Captured from the per-bar loop implementation before the array scan replaced it.
_GOLDEN: dict[str, str] = {
    "large_cap:0": "fa21b4aa1599fae8c78a82b508a166b06717378b8210ef5c5d7c4c3b24e12592",
    "large_cap:1": "d01098f7c756cdd7120251302f48fe506e085bd3461145b89c301f2f11c78c6c",
    "large_cap:10": "1ae0342ebaf7b540373760681153b67ffc0b3fa58e6a099b6e10854563725971",
    "large_cap:11": "93ad0cf5e8215e6b0f9b8e20ac8caa91637a6c6abe3e05b558072a5a63c0f084",
    "large_cap:2": "852f8a75f686d12c679483f23466f35d9101ef2a9076d137a9c216eaab22f09c",
    "large_cap:3": "2f415d05fe7e3ec88f0f1db357e0deb65b763300815fd28886cab5317753ef26",
    "large_cap:4": "7f8514076ba1f15efc6ebc88f3f5a2d634e14efc6df9b7987a7991a0211dbcd2",
    "large_cap:5": "1f394df30e64ce810588ecbacd4ba67e72fccb84e6583476118dd5ffc6d35a25",
    "large_cap:6": "088bc4e8931500164ca5a2c543a768161828e3e1b4c74e68bbffdaa4d04120e9",
    "large_cap:7": "0727d549810cb1f11db561130eac50e985dd5d1935e90a70556ef87cd283ceff",
    "large_cap:8": "194dccd8f4237420da5e32a4f58667fcd2255e9ee88db6cc9394482d4c97b978",
    "large_cap:9": "4e78386d6b1f36b7acc12e20c2731aa91230e3f75499c138e49db7db9d9db6ac",
    "low_vol:0": "a155636b9e03c3cdfabb8314c9075c530d11e2ac844463ee11a034d291190850",
    "low_vol:1": "2de02ab0c0782338ce203d9efbc6e6782fa5a494ae340726a90db8886077edb8",
    "low_vol:10": "6f1ec3785ce1be8000365564dc1c892f3ad5b8665e2177cfc5d9682402208856",
    "low_vol:11": "944a833e513cd95e37c85776811496e2b1a0891c93422366acb5c2a3e9178fe0",
    "low_vol:2": "3cdcb85a119767f37e1a6488a03daaad39ed94e5e43569550520edf173825ed8",
    "low_vol:3": "ad0a04773c860d2077d7f12458b43a070f2dd12e8313424e1fe53342aa212743",
    "low_vol:4": "6a1db30d488d9bb7bd6bfd404c2f1b06f1dae102aa74693f9afd8f4dcf49ac19",
    "low_vol:5": "30efde7b46ff15e900a11140028200d9fae82d7d0bd1aa7ad81aedfe4d923769",
    "low_vol:6": "f52d6f6d17289aea7263d3980bcc2b27d09ad99cfd54c061be1954d8933e0716",
    "low_vol:7": "d9bb3e26e91c9bbeb8740f2fd7cf20ff63c1776f733cd77687329eb9e6dcee44",
    "low_vol:8": "dfbe5f76a37f9ed1d1110bf349cf5ae2544893d4dd3310e0c5957ed3b36100b5",
    "low_vol:9": "cc68c2a097338e2c04b1301612b878b82f3d78960102d0236f263c90a117ebf2",
    "small_cap:0": "6598763652f887b5c33b60b573064c4300286312850365e36d77eaa76324417b",
    "small_cap:1": "03ecfbe705412da504999f6df7ce7d783d3571cdc1d1978f1d0de9a092dffa74",
    "small_cap:10": "d518c748766ad721357e01ef2ef93ba16aa7288306b0bd7f7c261253a4dd7169",
    "small_cap:11": "7db7e06c81bfdbb53d6d2147153ac1095fa1338fbf1d492948a7592f4a6fc54a",
    "small_cap:2": "3df79c05b4ab7a00f4f12432fc416244d297af66744b9910b5f1aa09c2e9081b",
    "small_cap:3": "9833f2768820e6fb1cf7fd5ab438f26c8953460b1082bac3657beff91ded55a7",
    "small_cap:4": "a3fa040f117d19e5883f661878fe16e8521ddc0ce8bf18178be919bcb293c1d0",
    "small_cap:5": "64779dd3b0a5ca2c8f65aecfacf3882447b2de4a1848a12bcb165e719a47028a",
    "small_cap:6": "ca679b85dd84212880784d244b9c04173e89659c142f4b2e6cfe593cccc8d9d3",
    "small_cap:7": "7a35b3b7346826b7589ecf2bfbd37de27bc2b5551eb021cb364054366790c836",
    "small_cap:8": "d01bde2b27766554fd902f67ea72f01afaf05fee2266e5530fc5814a40685f92",
    "small_cap:9": "a4bd60d5efd19a69bf2ee6289b62aa7157324cff594d4b988a9a0459b4c5898a",
    "volume_gaps:0": "2b993f7f2caba49a7991f08e2c7eacff64455003e467074eb9de2f5ef59d6f09",
    "volume_gaps:1": "356e2bca2500c4d2f105721304a8ad516f8902deb9d42546106e983f55c96464",
    "volume_gaps:10": "a5da30886b8bd616c184b1a2448a1901d807d5e15084e4f0c8f9cd014ba3f718",
    "volume_gaps:11": "64a8248ddfe2980537b7746ff17e0354adfcf985db0c9e4ded9a2946e68f5321",
    "volume_gaps:2": "387a82d1063e2a1328211da662a751474f92ed88717de04571f0edd2f2b902aa",
    "volume_gaps:3": "14e5d2cfa0739e34b6885d62f27adc150411331c91a7640161feccb60d64cff5",
    "volume_gaps:4": "80a50a9f8ed3813aba30f68f3576ad9e19d4f3fb550220185c407e31bc0aa9f3",
    "volume_gaps:5": "e98c9b373bbe6ee71def5b21f49baa44e7cb1eb2b03ab4f1beb3845fa5187919",
    "volume_gaps:6": "5a44352d9719ae027c445d6341a7ab40eed3d091f2f4e00d22ee3d1777d1bd48",
    "volume_gaps:7": "660e45ce2cecab8f5da7d6c5bbc672c76af709e1f2acdf032d6b251a892f1fba",
    "volume_gaps:8": "777690722e7fdb5da1ef5ee5c6c08b635aedef9226a12047f1f81adcd6ca1403",
    "volume_gaps:9": "7638abfae10a97134160fa247183267058b0ef9c3f103c9e577a453c0704a139",
}


@pytest.mark.parametrize("case", sorted(_GOLDEN))
def test_patterns_match_the_loop_implementation(case: str) -> None:
    regime, seed = case.rsplit(":", 1)
    series = _series(regime, int(seed))
    rows = auto_patterns.detect_auto_patterns(series, data_through=series["dates"][-1])
    assert _digest(rows) == _GOLDEN[case]


def test_window_volume_median_is_computed_once_per_scan(monkeypatch) -> None:
    series = _series("large_cap", 7)
    window = min(len(series["closes"]), auto_patterns._LOOKBACK)
    full_window_medians: list[int] = []
    real_median = auto_patterns._median

    def counting(values):
        if len(values) == window:
            full_window_medians.append(len(values))
        return real_median(values)

    monkeypatch.setattr(auto_patterns, "_median", counting)
    rows = auto_patterns.detect_auto_patterns(series, data_through=series["dates"][-1])

    assert rows
    assert full_window_medians == [window]

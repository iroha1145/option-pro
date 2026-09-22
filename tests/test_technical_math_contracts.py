import numpy as np
import pandas as pd

from app.services.breakouts import base_detector
from app.services.breakouts.feature_engine import compute_atr
from app.services.technical import base_structure


def test_cluster_roundoff_keeps_existing_candidate_tie_order():
    pivots = [(0, 1000.), (1, 1000.), (2, 1000.), (3, 15.3), (4, 15.3), (5, 15.3)]
    assert base_structure._best_cluster(pivots, .01, prefer_high=True) == pivots[3:]
    assert base_detector._best_cluster(pivots, .01, prefer_high=True) == pivots[:3]


def test_pivot_reducers_keep_existing_nan_semantics():
    values = [0., float("nan"), 2., 1., 0.]
    assert base_structure._pivots(values, high=True) == [(2, 2.0)]
    assert base_detector._pivots(np.array(values), high=True) == []


def test_detail_atr_shrinks_short_window_while_radar_requires_full_history():
    frame = pd.DataFrame({"Open": [10., 11., 12.], "High": [11., 12., 13.],
                          "Low": [9., 10., 11.], "Close": [10., 11., 12.]})
    assert base_structure._atr(frame.High.to_list(), frame.Low.to_list(), frame.Close.to_list(), 20) == 2.0
    assert compute_atr(frame, 20) is None

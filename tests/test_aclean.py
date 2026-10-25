# Purpose: Unit tests for core/aclean.py
import numpy as np
import pandas as pd
import pytest

from core.aclean import add_indicators


def test_indicator_definitions():
    rng = np.random.default_rng(0)
    c = 100 + rng.normal(0, 1, 60).cumsum()
    idx = pd.date_range("2024-10-21 09:15", periods=60, freq="5min")
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c}, index=idx)

    d = add_indicators(df)

    w = c[-20:]                                   # last 20 closes, computed by hand
    assert d["bb_mid"].iloc[-1] == pytest.approx(w.mean())
    assert d["bb_lo"].iloc[-1] == pytest.approx(w.mean() - 2 * w.std(ddof=0))
    assert d["bb_up"].iloc[-1] == pytest.approx(w.mean() + 2 * w.std(ddof=0))

    e, a = c[0], 2 / (9 + 1)                      # recursive EMA by hand
    for x in c[1:]:
        e = a * x + (1 - a) * e
    assert d["ema9"].iloc[-1] == pytest.approx(e)

    assert d["bb_lo"].iloc[:19].isna().all()      # no band before 20 bars
    assert "bb_lo" not in df.columns              # input not mutated
